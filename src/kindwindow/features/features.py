"""Vectorized windows, splits, EDA, and feature encodings (Step 08).

Window semantics are exactly ``contracts.history_counts`` / the pandas
engine's fast path, reimplemented with whole-array ``searchsorted`` calls
(no per-window Python loop):

* origins ``t`` are half-hour boundaries; the target ``[t, t+30m)`` must
  lie inside the segment span and 120 min of earlier history must exist
  in the same segment (``generate_window_keys`` rule);
* history counts are over half-open ``[t-w, t)`` for w in (5, 30, 120)
  minutes, all accepted events (motion ON *and* OFF, door OPEN *and*
  CLOSE); door counts restrict to door events;
* label is 1 iff any positive event (motion ON, door OPEN, door CLOSE)
  falls in ``[t, t+30m)``;
* recency is seconds since the most recent earlier accepted event
  strictly before ``t`` (``None`` + ``recency_missing=True`` when none);
* ``prior_freq_per_hour`` is ``count_120m / 2.0`` (the warmup guarantees
  the full 2 h are observed).

Segments follow ``engines.common.assign_segments``: a new segment starts
at a *corroborated* unresolved clock reversal in file order. The vectorized
implementation below detects reversal candidates with a running maximum
and checks corroboration exactly (a later event continuing from the
decreased time, still below the old high-water mark) with whole-array
operations per candidate.

Malformed-row exclusion (see ``real_ingest``): windows whose target
intersects a malformed row with a reliable time, or whose date matches a
malformed row whose time is unreliable, are excluded; an unlocatable
malformed row excludes its whole segment. Unmapped-message rows (known
non-activity readings) do NOT exclude windows; the fraction that would
be excluded under a strict reading is reported as a counterfactual.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from kindwindow.contracts import purge_mask, split_indices

NS_PER_SECOND = 1_000_000_000
TARGET_NS = 30 * 60 * NS_PER_SECOND
WARMUP_NS = 120 * 60 * NS_PER_SECOND
STEP_NS = 30 * 60 * NS_PER_SECOND
HISTORY_NS = (5 * 60 * NS_PER_SECOND, 30 * 60 * NS_PER_SECOND, 120 * 60 * NS_PER_SECOND)

WINDOW_COLUMNS: list[str] = [
    "home",
    "t",
    "count_5m",
    "count_30m",
    "count_120m",
    "door_5m",
    "door_30m",
    "door_120m",
    "recency_seconds",
    "recency_missing",
    "hour",
    "weekday",
    "prior_freq_per_hour",
    "label",
    "member",
    "segment",
]

# Feature columns of the windows frame, in canonical order, with dtypes.
# ``recency_seconds`` is Int64 (nullable): missing stays missing until a
# model explicitly applies the predeclared missing handling (0 + indicator).
FEATURE_COLUMNS: list[str] = [
    "count_5m",
    "count_30m",
    "count_120m",
    "door_5m",
    "door_30m",
    "door_120m",
    "recency_seconds",
    "recency_missing",
    "hour",
    "weekday",
    "prior_freq_per_hour",
]
FEATURE_DTYPES: dict[str, str] = {
    "count_5m": "int64",
    "count_30m": "int64",
    "count_120m": "int64",
    "door_5m": "int64",
    "door_30m": "int64",
    "door_120m": "int64",
    "recency_seconds": "Int64",
    "recency_missing": "bool",
    "hour": "int64",
    "weekday": "int64",
    "prior_freq_per_hour": "float64",
}

# Columns of the recency-only baseline design (before home indicators).
RECENCY_FEATURE_COLUMNS: list[str] = ["recency_scaled", "recency_missing"]


def _ceil_to_half_hour_ns(ts_ns: int) -> int:
    """Smallest half-hour boundary (seconds aligned to zero) >= ts_ns."""
    minute_ns = 60 * NS_PER_SECOND
    base = ((ts_ns + minute_ns - 1) // minute_ns) * minute_ns
    remainder = (base // minute_ns) % 30
    if remainder:
        base += (30 - remainder) * minute_ns
    return int(base)


def assign_segments_from_epochs(epoch_ns: np.ndarray) -> list[tuple[int, int]]:
    """Segment boundaries (half-open index slices, file order).

    Exactly mirrors :func:`kindwindow.engines.common.assign_segments`
    for a single (home, member) stream: a new segment starts at a
    *corroborated* unresolved clock reversal. The high-water mark
    RESETS at every split -- an event below the *global* high is only a
    candidate when it is also below the *current segment's* high, and
    corroboration is a later event in the file continuing from the
    decreased time (at or after it, still below the current segment's
    high). Simultaneous events never split, and a single out-of-order
    row with no continuation stays in its segment.

    Only reversal candidates (below the global running maximum) are
    examined; between candidates the segment high is advanced with
    whole-array maxima, so this stays vectorized in practice.
    """
    n = len(epoch_ns)
    if n == 0:
        return []
    high = np.maximum.accumulate(epoch_ns)
    candidates = np.flatnonzero(epoch_ns[1:] < high[:-1]) + 1
    if len(candidates) == 0:
        return [(0, n)]
    split_at: list[int] = []
    seg_high = int(epoch_ns[0])
    prev = 1  # seg_high covers epoch_ns[:prev]
    for j in candidates:
        j = int(j)
        if j > prev:
            seg_high = max(seg_high, int(epoch_ns[prev:j].max()))
        prev = j + 1
        cand = int(epoch_ns[j])
        if cand >= seg_high:
            # Below the global high but not the segment's high: this
            # event raises the segment high; it cannot start a split.
            seg_high = cand
            continue
        # Corroboration: a later event in [cand, seg_high). The first
        # event >= seg_high bounds the search; check a bounded window
        # first (the recovering clock is dense) and fall back to the
        # full remainder only when the window is still decreasing.
        rest = epoch_ns[j + 1 :]
        corroborated = False
        window = rest[:4096]
        in_range = (window >= cand) & (window < seg_high)
        if bool(in_range.any()):
            corroborated = True
        elif bool((window >= seg_high).any()):
            corroborated = False
        else:
            ge = rest >= seg_high
            if bool(ge.any()):
                region = rest[: int(np.argmax(ge))]
            else:
                region = rest
            corroborated = bool(
                region.size and ((region >= cand) & (region < seg_high)).any()
            )
        if corroborated:
            split_at.append(j)
            seg_high = cand
            prev = j + 1
    bounds = [0, *split_at, n]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _intersect_mask(keys_ns: np.ndarray, bad_ns: np.ndarray) -> np.ndarray:
    """True for keys whose target ``[t, t+30m)`` contains any bad time."""
    mask = np.zeros(len(keys_ns), dtype=bool)
    for b in bad_ns:
        lo = int(np.searchsorted(keys_ns, int(b) - TARGET_NS, side="right"))
        hi = int(np.searchsorted(keys_ns, int(b), side="right"))
        if lo < hi:
            mask[lo:hi] = True
    return mask


def compute_windows_vectorized(
    home: str,
    member: str,
    epoch_ns: np.ndarray,
    is_door: np.ndarray,
    is_positive: np.ndarray,
    *,
    malformed_times_ns: np.ndarray | None = None,
    malformed_days: set | None = None,
    malformed_unlocatable: bool = False,
    quarantined_times_ns: np.ndarray | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Vectorized forecast windows for one (home, member) event stream.

    ``epoch_ns`` / ``is_door`` / ``is_positive`` are file-order arrays of
    accepted events. Returns ``(windows_df, report)`` with the same
    columns as :func:`kindwindow.engines.pandas_engine.compute_windows`.
    """
    epoch_ns = np.asarray(epoch_ns, dtype=np.int64)
    is_door = np.asarray(is_door, dtype=bool)
    is_positive = np.asarray(is_positive, dtype=bool)
    bad_times = (
        np.asarray(malformed_times_ns, dtype=np.int64)
        if malformed_times_ns is not None
        else np.empty(0, dtype=np.int64)
    )
    bad_day_ordinals = set()
    for d in malformed_days or set():
        bad_day_ordinals.add(d.toordinal() - 719163)  # days since 1970-01-01
    q_times = (
        np.asarray(quarantined_times_ns, dtype=np.int64)
        if quarantined_times_ns is not None
        else np.empty(0, dtype=np.int64)
    )

    segments = assign_segments_from_epochs(epoch_ns)
    frames: list[pd.DataFrame] = []
    seen_keys: set[int] = set()
    n_excluded = 0
    n_discarded = 0
    n_keys_total = 0
    n_q_counterfactual = 0
    for seg_index, (start, end) in enumerate(segments):
        seg = epoch_ns[start:end]
        order = np.argsort(seg, kind="stable")
        times = seg[order]
        door = is_door[start:end][order]
        pos = is_positive[start:end][order]
        door_prefix = np.concatenate([[0], np.cumsum(door, dtype=np.int64)])
        pos_prefix = np.concatenate([[0], np.cumsum(pos, dtype=np.int64)])
        first = _ceil_to_half_hour_ns(int(times[0]) + WARMUP_NS)
        last = int(times[-1]) - TARGET_NS
        if first > last:
            continue
        keys = np.arange(first, last + 1, STEP_NS, dtype=np.int64)
        n_keys_total += len(keys)
        if malformed_unlocatable:
            n_excluded += len(keys)
            continue
        if len(q_times):
            n_q_counterfactual += int(_intersect_mask(keys, q_times).sum())
        excluded = np.zeros(len(keys), dtype=bool)
        if len(bad_times):
            excluded |= _intersect_mask(keys, bad_times)
        if bad_day_ordinals:
            days = (keys // NS_PER_SECOND) // 86400
            excluded |= np.isin(days, list(bad_day_ordinals))
        idx_t = np.searchsorted(times, keys, side="left")
        idx_end = np.searchsorted(times, keys + TARGET_NS, side="left")
        counts = {}
        doors = {}
        for w_ns, w_min in zip(HISTORY_NS, (5, 30, 120)):
            lo = np.searchsorted(times, keys - w_ns, side="left")
            counts[w_min] = idx_t - lo
            doors[w_min] = door_prefix[idx_t] - door_prefix[lo]
        has_prev = idx_t > 0
        prev_idx = np.maximum(idx_t - 1, 0)
        recency = np.where(
            has_prev, (keys - times[prev_idx]) // NS_PER_SECOND, -1
        ).astype(np.int64)
        label = (pos_prefix[idx_end] - pos_prefix[idx_t] > 0).astype(np.int64)
        keep = ~excluded
        # First segment in (member, segment) order wins each (home, t).
        dup = np.zeros(len(keys), dtype=bool)
        if seen_keys:
            dup = np.array([int(k) in seen_keys for k in keys], dtype=bool)
            n_discarded += int((keep & dup).sum())
            keep &= ~dup
        # An excluded key already emitted by an earlier segment is not
        # double-counted: it is a duplicate there, not an exclusion here.
        n_excluded += int((excluded & ~dup).sum())
        kept_keys = keys[keep]
        for k in kept_keys:
            seen_keys.add(int(k))
        if not keep.any():
            continue
        seconds = kept_keys // NS_PER_SECOND
        rec_kept = recency[keep]
        missing_kept = ~has_prev[keep]
        rec_col = pd.array(rec_kept, dtype="Int64")
        rec_col[missing_kept] = pd.NA
        frames.append(
            pd.DataFrame(
                {
                    "home": home,
                    "t": pd.to_datetime(kept_keys),
                    "count_5m": counts[5][keep].astype(np.int64),
                    "count_30m": counts[30][keep].astype(np.int64),
                    "count_120m": counts[120][keep].astype(np.int64),
                    "door_5m": doors[5][keep].astype(np.int64),
                    "door_30m": doors[30][keep].astype(np.int64),
                    "door_120m": doors[120][keep].astype(np.int64),
                    "recency_seconds": rec_col,
                    "recency_missing": missing_kept,
                    "hour": ((seconds % 86400) // 3600).astype(np.int64),
                    "weekday": ((seconds // 86400 + 3) % 7).astype(np.int64),
                    "prior_freq_per_hour": counts[120][keep].astype(np.float64) / 2.0,
                    "label": label[keep].astype(np.int64),
                    "member": member,
                    "segment": np.int64(seg_index),
                }
            )
        )
    if frames:
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["home", "t"], kind="stable").reset_index(drop=True)
        df = df[WINDOW_COLUMNS]
    else:
        df = pd.DataFrame(
            {
                "home": pd.Series(dtype="string"),
                "t": pd.Series(dtype="datetime64[ns]"),
                "count_5m": pd.Series(dtype="int64"),
                "count_30m": pd.Series(dtype="int64"),
                "count_120m": pd.Series(dtype="int64"),
                "door_5m": pd.Series(dtype="int64"),
                "door_30m": pd.Series(dtype="int64"),
                "door_120m": pd.Series(dtype="int64"),
                "recency_seconds": pd.Series(dtype="Int64"),
                "recency_missing": pd.Series(dtype="bool"),
                "hour": pd.Series(dtype="int64"),
                "weekday": pd.Series(dtype="int64"),
                "prior_freq_per_hour": pd.Series(dtype="float64"),
                "label": pd.Series(dtype="int64"),
                "member": pd.Series(dtype="string"),
                "segment": pd.Series(dtype="int64"),
            }
        )
    report = {
        "n_segments": len(segments),
        "n_keys_total": n_keys_total,
        "n_windows": len(df),
        "n_excluded_windows": n_excluded,
        "n_discarded_duplicate_windows": n_discarded,
        "quarantine_counterfactual_excluded": n_q_counterfactual,
        "quarantine_counterfactual_fraction": (
            n_q_counterfactual / n_keys_total if n_keys_total else 0.0
        ),
    }
    return df, report


def assign_splits(windows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-home chronological splits with the frozen 24 h purge.

    Returns ``(assigned, summary)``. ``assigned`` is ``windows`` sorted
    by (home, t) with a ``split`` column in {train, selection,
    calibration, test, purged}. ``summary`` has one row per home with the
    unpurged cut indices, boundary times, purge counts, and final counts.
    Boundaries are the origin times of the first window of the next split
    (``t[c1]``, ``t[c2]``, ``t[c3]``); windows within ``<= 24 h`` of any
    boundary on either side, and windows whose label interval crosses a
    boundary, are purged (``contracts.purge_mask``).
    """
    assigned_parts: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    for home, group in windows.sort_values(["home", "t"], kind="stable").groupby(
        "home", sort=True
    ):
        g = group.reset_index(drop=True)
        n = len(g)
        c1, c2, c3 = split_indices(n)
        times = [pd.Timestamp(t).to_pydatetime() for t in g["t"]]
        boundary_by_cut: dict[str, datetime] = {}
        for name, c in (("train_selection", c1), ("selection_calibration", c2),
                        ("calibration_test", c3)):
            if 0 < c < n:
                boundary_by_cut[name] = times[c]
        boundaries = list(boundary_by_cut.values())
        mask = purge_mask(times, boundaries) if boundaries else [False] * n
        labels = ["train"] * n
        for i in range(n):
            if i >= c3:
                labels[i] = "test"
            elif i >= c2:
                labels[i] = "calibration"
            elif i >= c1:
                labels[i] = "selection"
        for i, purged in enumerate(mask):
            if purged:
                labels[i] = "purged"
        g = g.copy()
        g["split"] = labels
        assigned_parts.append(g)
        counts = {s: int(sum(1 for x in labels if x == s)) for s in
                  ("train", "selection", "calibration", "test", "purged")}
        summary_rows.append(
            {
                "home": home,
                "n_windows": n,
                "cut_train_end": c1,
                "cut_selection_end": c2,
                "cut_calibration_end": c3,
                "boundary_train_selection": (
                    boundary_by_cut["train_selection"].isoformat()
                    if "train_selection" in boundary_by_cut else ""
                ),
                "boundary_selection_calibration": (
                    boundary_by_cut["selection_calibration"].isoformat()
                    if "selection_calibration" in boundary_by_cut else ""
                ),
                "boundary_calibration_test": (
                    boundary_by_cut["calibration_test"].isoformat()
                    if "calibration_test" in boundary_by_cut else ""
                ),
                "n_purged": counts["purged"],
                "n_train": counts["train"],
                "n_selection": counts["selection"],
                "n_calibration": counts["calibration"],
                "n_test": counts["test"],
            }
        )
    assigned = (
        pd.concat(assigned_parts, ignore_index=True)
        if assigned_parts
        else windows.copy()
    )
    summary = pd.DataFrame(summary_rows)
    return assigned, summary


def training_eda(assigned: pd.DataFrame) -> pd.DataFrame:
    """Train-split-only EDA: per-home totals and per-home-per-hour rows.

    Columns: home, hour ("all" for the home total, else 0-23), n,
    positives, prevalence. Train split only; selection/calibration/test
    are never summarized here.
    """
    train = assigned[assigned["split"] == "train"]
    rows: list[dict] = []
    for home, group in train.groupby("home", sort=True):
        n = len(group)
        pos = int(group["label"].sum())
        rows.append(
            {
                "home": home,
                "hour": "all",
                "n": n,
                "positives": pos,
                "prevalence": pos / n if n else 0.0,
            }
        )
        for hour, hgroup in group.groupby("hour", sort=True):
            hn = len(hgroup)
            hpos = int(hgroup["label"].sum())
            rows.append(
                {
                    "home": home,
                    "hour": int(hour),
                    "n": hn,
                    "positives": hpos,
                    "prevalence": hpos / hn if hn else 0.0,
                }
            )
    return pd.DataFrame(rows, columns=["home", "hour", "n", "positives", "prevalence"])


def home_indicator_columns(homes: list[str]) -> list[str]:
    """One-hot column names for the home indicators, in sorted-home order."""
    return [f"home_{h}" for h in sorted(homes)]


def encode_home_indicators(df: pd.DataFrame, homes: list[str]) -> np.ndarray:
    """0/1 home-indicator matrix (one column per home, sorted order)."""
    homes_sorted = sorted(homes)
    home_values = df["home"].to_numpy()
    out = np.zeros((len(df), len(homes_sorted)), dtype=np.float64)
    for j, h in enumerate(homes_sorted):
        out[:, j] = (home_values == h).astype(np.float64)
    return out


def fit_recency_scaler(train: pd.DataFrame) -> dict[str, float]:
    """Train-only scaler for recency (population mean/std, ddof=0).

    Fit on non-missing train recency values only. Stored as plain floats
    so the transform is reproducible without a fitted sklearn object.
    """
    observed = train.loc[~train["recency_missing"], "recency_seconds"].to_numpy(
        dtype=np.float64
    )
    if len(observed) == 0:
        return {"mean": 0.0, "std": 1.0}
    mean = float(np.mean(observed))
    std = float(np.std(observed))
    if std == 0.0:
        std = 1.0
    return {"mean": mean, "std": std}


def transform_recency(df: pd.DataFrame, scaler: dict[str, float]) -> np.ndarray:
    """Recency design columns: [recency_scaled, recency_missing].

    Predeclared missing handling: missing recency -> scaled value 0.0
    plus indicator 1.0; observed recency is z-scored with the
    train-fitted scaler. Never an invented interval.
    """
    missing = df["recency_missing"].to_numpy(dtype=bool)
    scaled = np.zeros(len(df), dtype=np.float64)
    if (~missing).any():
        observed = df.loc[~missing, "recency_seconds"].to_numpy(dtype=np.float64)
        scaled[~missing] = (observed - scaler["mean"]) / scaler["std"]
    return np.column_stack([scaled, missing.astype(np.float64)])


def build_recency_design(
    df: pd.DataFrame, homes: list[str], scaler: dict[str, float]
) -> tuple[np.ndarray, list[str]]:
    """Design matrix for the recency-only baseline.

    Columns: ``recency_scaled``, ``recency_missing``, then one home
    indicator per home (sorted order). ``scaler`` must come from
    :func:`fit_recency_scaler` on the TRAIN split only.
    """
    rec = transform_recency(df, scaler)
    ind = encode_home_indicators(df, homes)
    names = RECENCY_FEATURE_COLUMNS + home_indicator_columns(homes)
    return np.hstack([rec, ind]), names
