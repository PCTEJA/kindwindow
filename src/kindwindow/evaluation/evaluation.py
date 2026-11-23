"""Step 11 evaluation (REDUCED scope): test-once scoring of the frozen model.

Loads the frozen artifact from ``local/models/frozen_model.json`` (+ joblib
sidecar) and never refits anything. The test split is read exactly once,
by :func:`run_evaluation`, after the caller has written the freeze manifest
(``local/runs/step11/manifest.json``). Reduced scope: gap 12 h only,
degradation 30% of events gone with seed 2207 only, abstention at 50% and
100% retention only. See ``reports/tables/step11_summary.md`` for omissions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from kindwindow.features.features import compute_windows_vectorized
from kindwindow.models.baselines import (
    brier_score,
    fit_constant,
    fit_hour_of_week,
    fit_recency_logistic,
    macro_brier,
    per_home_brier,
    predict_constant,
    predict_hour_of_week,
    predict_recency_logistic,
)
from kindwindow.models.calibration import CalibrationResult, apply_calibration
from kindwindow.models.freeze import hash_key, load_frozen
from kindwindow.models.train import sha256_file, verify_split_hash

GAP_HOURS = 12
DEGRADATION_FRACTION = 0.30
DEGRADATION_SEED = 2207
RETENTION_TARGETS = (0.5, 1.0)
NS_PER_SECOND = 1_000_000_000
HISTORY_NS = 120 * 60 * NS_PER_SECOND
TARGET_NS = 30 * 60 * NS_PER_SECOND


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_windows_with_splits(
    features_dir: Path, split_path: Path
) -> pd.DataFrame:
    """All windows joined to split.csv (including TEST).

    Verifies the split hash first. This is the ONLY loader in the package
    that returns test rows; callers must treat its output as test-sealed.
    """
    features_dir = Path(features_dir)
    split_path = Path(split_path)
    if not split_path.is_file():
        raise FileNotFoundError(f"split file not found: {split_path}")
    if not features_dir.is_dir() or not list(features_dir.rglob("*.parquet")):
        raise FileNotFoundError(f"no windows Parquet at {features_dir}")
    verify_split_hash(split_path)
    windows = pd.concat(
        [pd.read_parquet(f) for f in sorted(features_dir.rglob("*.parquet"))],
        ignore_index=True,
    )
    splits = pd.read_csv(split_path)
    splits["t"] = pd.to_datetime(splits["t"])
    merged = windows.merge(splits, on=["home", "t"], how="left", validate="one_to_one")
    if merged["split"].isna().any():
        raise ValueError("windows without a split assignment")
    return merged


def load_frozen_model(root: Path) -> tuple[dict, object, CalibrationResult]:
    """Load the frozen artifact (JSON + forest sidecar) and its calibration."""
    data, estimator = load_frozen(Path(root) / "local" / "models" / "frozen_model.json")
    calibration = CalibrationResult.from_dict(data["calibration"])
    return data, estimator, calibration


def predict_frozen_raw(data: dict, estimator, df: pd.DataFrame) -> np.ndarray:
    """Raw positive-class probabilities of the reloaded frozen model."""
    from kindwindow.models.freeze import predict_frozen

    return predict_frozen(data, estimator, df)


# ---------------------------------------------------------------------------
# Per-home scoring
# ---------------------------------------------------------------------------


def per_home_table(
    df: pd.DataFrame, p: np.ndarray, *, model: str, split: str
) -> pd.DataFrame:
    """One row per home (n, prevalence, Brier, AP) plus a MACRO row."""
    y = df["label"].to_numpy(dtype=np.int64)
    homes = df["home"].to_numpy()
    rows: list[dict] = []
    aps: list[float] = []
    for home in sorted(df["home"].unique()):
        pos = np.where(homes == home)[0]
        yh, ph = y[pos], np.asarray(p, dtype=np.float64)[pos]
        if len(np.unique(yh)) < 2:
            ap = float("nan")
            ap_note = "undefined: single class in test" if len(yh) else "undefined: no windows"
        else:
            ap = float(average_precision_score(yh, ph))
            ap_note = ""
            aps.append(ap)
        rows.append(
            {
                "model": model,
                "split": split,
                "home": str(home),
                "n": int(len(pos)),
                "n_positive": int(yh.sum()),
                "prevalence": float(yh.mean()) if len(yh) else float("nan"),
                "brier": brier_score(yh, ph),
                "ap": ap,
                "ap_note": ap_note,
            }
        )
    per_home = per_home_brier(df, np.asarray(p, dtype=np.float64))
    rows.append(
        {
            "model": model,
            "split": split,
            "home": "MACRO",
            "n": int(len(df)),
            "n_positive": int(y.sum()),
            "prevalence": float(y.mean()) if len(y) else float("nan"),
            "brier": macro_brier(per_home),
            "ap": float(np.mean(aps)) if aps else float("nan"),
            "ap_note": f"macro AP over {len(aps)} defined homes"
            if aps
            else "undefined: no home with both classes",
        }
    )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Abstention (frozen gates, u = p_raw * (1 - p_raw), low retained)
# ---------------------------------------------------------------------------


def retention_mask(
    uncertainties: np.ndarray,
    weights: np.ndarray,
    keys: list[str],
    target_fraction: float,
    cutoff: float,
) -> np.ndarray:
    """Boolean retained mask under a FROZEN retention gate.

    ``cutoff`` is the frozen numeric cutoff (from selection, never
    recomputed on the scored cohort). Windows with ``u`` strictly
    below the cutoff are retained; windows exactly at the cutoff are
    ordered by stable hash ascending and retained until the nominal
    retained weight (``target_fraction`` of total weight) is reached.
    At 100% retention thresholding is bypassed (all retained).
    """
    u = np.asarray(uncertainties, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if target_fraction >= 1.0:
        return np.ones(len(u), dtype=bool)
    below = u < cutoff
    at = u == cutoff
    total_weight = float(w.sum())
    needed = target_fraction * total_weight - float(w[below].sum())
    keep = below.copy()
    tie_idx = np.flatnonzero(at)
    tie_order = sorted(tie_idx, key=lambda i: keys[i])
    retained_tie_weight = 0.0
    for i in tie_order:
        if retained_tie_weight >= needed - 1e-12:
            break
        keep[i] = True
        retained_tie_weight += float(w[i])
    return keep


def equal_home_weights(df: pd.DataFrame) -> np.ndarray:
    """Predeclared normalized weights ``N / (H * n_home)``."""
    from kindwindow.models.baselines import equal_home_weights as _w

    return _w(df)


# ---------------------------------------------------------------------------
# Gap sensitivity (12 h only, reduced scope)
# ---------------------------------------------------------------------------


def flag_long_gap_windows(
    df: pd.DataFrame, event_times_ns: dict[str, np.ndarray], threshold_hours: float = 12
) -> np.ndarray:
    """Flag windows whose span [t-120m, t+30m) intersects an event-free gap.

    A gap is the interval between two consecutive (time-sorted) accepted
    events, strictly longer than ``threshold_hours``. Matches
    ``contracts.intersects_long_gap`` / ``docs/data-dictionary.md`` §8.
    """
    threshold_ns = int(threshold_hours * 3600 * NS_PER_SECOND)
    flagged = np.zeros(len(df), dtype=bool)
    t_ns = pd.to_datetime(df["t"]).to_numpy(dtype="datetime64[ns]").astype(np.int64)
    homes = df["home"].to_numpy()
    for home in sorted(df["home"].unique()):
        pos = np.where(homes == home)[0]
        times = np.unique(np.asarray(event_times_ns[str(home)], dtype=np.int64))
        if len(times) < 2:
            continue
        starts = times[:-1]
        ends = times[1:]
        long_gap = (ends - starts) > threshold_ns
        g_starts, g_ends = starts[long_gap], ends[long_gap]
        if len(g_starts) == 0:
            continue
        span_start = t_ns[pos] - HISTORY_NS
        span_end = t_ns[pos] + TARGET_NS
        # A window intersects a gap iff span_start < gap_end and gap_start < span_end.
        idx = np.searchsorted(g_ends, span_start, side="right")
        hit = np.zeros(len(pos), dtype=bool)
        valid = idx < len(g_starts)
        hit[valid] = g_starts[idx[valid]] < span_end[valid]
        flagged[pos] = hit
    return flagged


# ---------------------------------------------------------------------------
# Degradation: 30% of events gone, seed 2207 only (reduced scope)
# ---------------------------------------------------------------------------


def drop_event_mask(
    home: str, member: str, row: np.ndarray, *, seed: int, fraction: float
) -> np.ndarray:
    """Boolean mask of events to DROP, keyed deterministically by event identity.

    ``SHA-256(seed|home|member|row)`` uniformized to [0, 1); drop when below
    ``fraction``. One draw per event, reproducible across runs.
    """
    out = np.empty(len(row), dtype=bool)
    for i, r in enumerate(np.asarray(row)):
        digest = hashlib.sha256(f"{seed}|{home}|{member}|{int(r)}".encode()).digest()
        u = int.from_bytes(digest[:8], "big") / 2**64
        out[i] = u < fraction
    return out


# ---------------------------------------------------------------------------
# Orchestration (test read exactly once)
# ---------------------------------------------------------------------------


def run_evaluation(root: Path, *, out_dir: Path | None = None) -> dict:
    """Run the reduced Step 11 evaluation. Reads the test split once.

    Returns a JSON-serializable summary dict. Writes per-home scores to
    ``reports/tables/test_scores.csv`` and the summary to
    ``local/runs/step11/results.json`` (row-level predictions are NOT
    written to reports; only aggregates leave ``local/``).
    """
    import time

    root = Path(root)
    t_start = time.time()
    frozen_path = root / "local" / "models" / "frozen_model.json"
    frozen_sha = sha256_file(frozen_path)
    manifest_path = root / "local" / "runs" / "step11" / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"evaluation manifest not found: {manifest_path} "
            "(write the freeze manifest BEFORE reading test)"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["frozen_model_sha256"] != frozen_sha:
        raise ValueError("frozen model changed since the manifest was written")

    merged = load_windows_with_splits(
        root / "local" / "features", root / "data" / "manifests" / "split.csv"
    )
    train = merged[merged["split"] == "train"].reset_index(drop=True)
    test = merged[merged["split"] == "test"].reset_index(drop=True)
    failed: list[dict] = []
    if test.empty:
        failed.append({"scope": "test", "count": 0, "reason": "test split empty"})

    # Baselines fit on TRAIN only (already-implemented Step 08 code).
    const = fit_constant(train)
    how = fit_hour_of_week(train)
    rec = fit_recency_logistic(train)

    # Frozen model, loaded — never refit.
    frozen_data, estimator, calibration = load_frozen_model(root)
    p_raw = predict_frozen_raw(frozen_data, estimator, test)
    p_cal = apply_calibration(p_raw, calibration)
    if not np.isfinite(p_raw).all() or not np.isfinite(p_cal).all():
        failed.append(
            {"scope": "frozen-model predictions", "count": int((~np.isfinite(p_raw)).sum()),
             "reason": "non-finite raw probability"}
        )

    tables: list[pd.DataFrame] = []
    tables.append(per_home_table(test, p_cal, model="forest-d10-leaf10-calibrated", split="test"))
    tables.append(per_home_table(test, p_raw, model="forest-d10-leaf10-raw", split="test"))
    tables.append(
        per_home_table(test, predict_recency_logistic(rec, test),
                       model="recency-baseline", split="test")
    )
    tables.append(
        per_home_table(test, predict_constant(const, test),
                       model="constant-baseline", split="test")
    )
    tables.append(
        per_home_table(test, predict_hour_of_week(how, test),
                       model="hour-of-week-baseline", split="test")
    )
    scores = pd.concat(tables, ignore_index=True)

    # --- Abstention: frozen gates, 50% and 100% only. ---
    u = p_raw * (1.0 - p_raw)
    weights = equal_home_weights(test)
    keys = [hash_key(h, t) for h, t in zip(test["home"], test["t"])]
    abstention: dict = {}
    for target in RETENTION_TARGETS:
        if target >= 1.0:
            mask = np.ones(len(test), dtype=bool)
            cutoff = float("inf")
        else:
            # FROZEN cutoff from selection (never recomputed on test).
            cutoff = float(frozen_data["retention_gates"][str(target)]["cutoff"])
            mask = retention_mask(u, weights, keys, target, cutoff)
        retained = test[mask].reset_index(drop=True)
        abstention[str(target)] = {
            "cutoff": cutoff,
            "n_retained": int(mask.sum()),
            "n_eligible": int(len(test)),
            "coverage": float(mask.sum() / len(test)) if len(test) else float("nan"),
            "macro_brier_retained": macro_brier(per_home_brier(retained, p_cal[mask]))
            if len(retained) else float("nan"),
            "n_retained_per_home": {
                str(h): int((retained["home"] == h).sum())
                for h in sorted(test["home"].unique())
            },
        }
        tables.append(
            per_home_table(
                retained, p_cal[mask],
                model=f"forest-d10-leaf10-calibrated-retained-{target}", split="test",
            )
        )
    scores = pd.concat(tables, ignore_index=True)

    # --- Gap sensitivity: 12 h only. ---
    gap: dict = {"threshold_hours": GAP_HOURS}
    try:
        event_times = _load_event_times_ns(root)
        flagged = flag_long_gap_windows(test, event_times, GAP_HOURS)
        kept = test[~flagged].reset_index(drop=True)
        gap.update(
            {
                "n_flagged": int(flagged.sum()),
                "n_kept": int((~flagged).sum()),
                "n_flagged_per_home": {
                    str(h): int(flagged[test["home"].to_numpy() == h].sum())
                    for h in sorted(test["home"].unique())
                },
                "macro_brier_excluding_flagged": macro_brier(per_home_brier(kept, p_cal[~flagged]))
                if len(kept) else float("nan"),
            }
        )
        tables.append(
            per_home_table(kept, p_cal[~flagged],
                           model="forest-d10-leaf10-calibrated-gap12h-excluded", split="test")
        )
        scores = pd.concat(tables, ignore_index=True)
    except Exception as exc:  # gap analysis is priority (c); record and move on
        gap.update({"omitted": True, "reason": str(exc)})
        failed.append({"scope": "gap-12h", "count": 0, "reason": str(exc)})

    # --- Degradation: 30% of events gone, seed 2207 only. ---
    degradation: dict = {
        "fraction": DEGRADATION_FRACTION,
        "seed": DEGRADATION_SEED,
        "condition": "30pct-events-gone",
    }
    try:
        deg_windows = _degraded_windows(root, fraction=DEGRADATION_FRACTION, seed=DEGRADATION_SEED)
        n_dropped_events = int(deg_windows.attrs.get("n_dropped_events", -1))
        n_kept_events = int(deg_windows.attrs.get("n_kept_events", -1))
        # Labels preserved from clean test data: drop the degraded-stream
        # label before merging so the clean test label survives intact.
        deg = deg_windows.drop(columns=["label"]).merge(
            test[["home", "t", "label"]], on=["home", "t"], how="inner",
            validate="one_to_one",
        )
        missing = len(test) - len(deg)
        if missing:
            failed.append(
                {"scope": "degradation-30pct-seed-2207", "count": int(missing),
                 "reason": (
                     "test keys with no recomputed degraded window "
                     "(key range shifted after event drops)"
                 ),}
            )
        # Labels preserved from clean test data (merge keeps test label).
        p_deg_raw = predict_frozen_raw(frozen_data, estimator, deg)
        p_deg = apply_calibration(p_deg_raw, calibration)
        degradation.update(
            {
                "n_scored": int(len(deg)),
                "n_test_keys": int(len(test)),
                "n_missing_keys": int(missing),
                "n_dropped_events": n_dropped_events,
                "n_kept_events": n_kept_events,
                "actual_dropped_fraction": (
                    n_dropped_events / (n_dropped_events + n_kept_events)
                    if n_dropped_events >= 0 and n_kept_events >= 0
                    else float("nan")
                ),
                "macro_brier": macro_brier(per_home_brier(deg, p_deg)),
            }
        )
        tables.append(
            per_home_table(
                deg, p_deg,
                model="forest-d10-leaf10-calibrated-degraded-30pct-2207", split="test",
            )
        )
        scores = pd.concat(tables, ignore_index=True)
    except Exception as exc:  # priority (d); record and move on
        degradation.update({"omitted": True, "reason": str(exc)})
        failed.append({"scope": "degradation-30pct-seed-2207", "count": 0, "reason": str(exc)})

    tables_dir = root / "reports" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    scores_path = tables_dir / "test_scores.csv"
    scores.to_csv(scores_path, index=False)

    macro_row = scores[
        (scores["model"] == "forest-d10-leaf10-calibrated") & (scores["home"] == "MACRO")
    ].iloc[0]
    rec_row = scores[
        (scores["model"] == "recency-baseline") & (scores["home"] == "MACRO")
    ].iloc[0]
    summary = {
        "step": "11-reduced",
        "code_revision": manifest["code_revision"],
        "config_sha256": manifest["config_sha256"],
        "split_sha256": manifest["split_sha256"],
        "frozen_model_sha256": frozen_sha,
        "n_test": int(len(test)),
        "n_test_per_home": {
            str(h): int((test["home"] == h).sum())
            for h in sorted(test["home"].unique())
        },
        "frozen_calibrated_macro_brier": float(macro_row["brier"]),
        "frozen_calibrated_macro_ap": float(macro_row["ap"]),
        "recency_baseline_macro_brier": float(rec_row["brier"]),
        "paired_improvement_macro_brier": float(rec_row["brier"] - macro_row["brier"]),
        "abstention": abstention,
        "gap_12h": gap,
        "degradation_30pct_seed_2207": degradation,
        "failed_cases": failed,
        "elapsed_seconds": time.time() - t_start,
        "omitted": [
            "gap 6h/24h (reduced scope)",
            "degradation 10% events, 25% sensors, seeds 1103/3301/4409/5519 (reduced scope)",
            "abstention 75% (reduced scope)",
            "notes-eval on heldout (not run in this reduced step)",
        ],
    }
    run_dir = root / "local" / "runs" / "step11"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _load_event_times_ns(root: Path) -> dict[str, np.ndarray]:
    """Accepted event times (ns since epoch) per home, from normalized Parquet."""
    normalized_dir = Path(root) / "local" / "normalized"
    out: dict[str, np.ndarray] = {}
    for home_dir in sorted(normalized_dir.glob("home=*")):
        home = home_dir.name.removeprefix("home=")
        frames = [pd.read_parquet(f, columns=["time"]) for f in sorted(home_dir.rglob("*.parquet"))]
        times = pd.concat(frames, ignore_index=True)["time"]
        out[home] = pd.to_datetime(times).to_numpy(dtype="datetime64[ns]").astype(np.int64)
    return out


def _degraded_windows(root: Path, *, fraction: float, seed: int) -> pd.DataFrame:
    """Recompute windows on the degraded event stream (vectorized path).

    Per home: load normalized events in file order (sorted by ``row``,
    exactly as Step 08 ``kindwindow features`` does), drop ``fraction``
    of events by the deterministic hash mask, recompute windows with
    ``compute_windows_vectorized`` using the same malformed/quarantine
    sidecars. Labels here come from the degraded stream and are
    overwritten by clean test labels on merge in :func:`run_evaluation`.
    """
    normalized_dir = Path(root) / "local" / "normalized"
    run_dir = root / "local" / "runs" / "step08"
    ledgers = json.loads((run_dir / "ingest_ledgers.json").read_text(encoding="utf-8"))["ledgers"]
    sidecars = run_dir / "sidecars"
    frames: list[pd.DataFrame] = []
    dropped_total = 0
    kept_total = 0
    for home_dir in sorted(normalized_dir.glob("home=*")):
        home = home_dir.name.removeprefix("home=")
        files = sorted(home_dir.rglob("*.parquet"))
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.sort_values(["row"], kind="stable").reset_index(drop=True)
        drop = drop_event_mask(
            home, str(df["member"].iloc[0]), df["row"].to_numpy(dtype=np.int64),
            seed=seed, fraction=fraction,
        )
        dropped_total += int(drop.sum())
        kept_total += int((~drop).sum())
        kept = df[~drop].reset_index(drop=True)
        epoch_ns = pd.to_datetime(kept["time"]).to_numpy(dtype="datetime64[ns]").astype(np.int64)
        is_door = kept["type"].to_numpy() == "door"
        is_positive = kept["is_positive"].to_numpy(dtype=bool)
        q_path = sidecars / f"{home}.quarantined_times_ns.npy"
        m_path = sidecars / f"{home}.malformed_times_ns.npy"
        q_times = np.load(q_path) if q_path.is_file() else None
        m_times = np.load(m_path) if m_path.is_file() else None
        ing = ledgers.get(home, {}).get("ingest", {})
        m_days = {_date.fromisoformat(d) for d in ing.get("malformed_days", [])}
        windows, _report = compute_windows_vectorized(
            home,
            f"data/{home}.csv",
            epoch_ns,
            is_door,
            is_positive,
            malformed_times_ns=m_times,
            malformed_days=m_days,
            malformed_unlocatable=ing.get("malformed_unlocatable", False),
            quarantined_times_ns=q_times,
        )
        frames.append(windows)
        del df, kept, windows
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result.attrs["n_dropped_events"] = dropped_total
    result.attrs["n_kept_events"] = kept_total
    return result
