"""Step 08: vectorized windows, splits, and baseline tests.

* Vectorized windows are checked against the pandas engine's fast path
  and against the independent slow oracle on the golden fixture, and
  against the pandas engine on a small real slice (first 30 days of
  hh112, smallest cohort home) when normalized real data is present.
* Split/purge rules are checked on synthetic windows (frozen cuts,
  24 h purge both sides, label-crossing removal).
* Baseline formulas are checked on hand-computed synthetic data.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kindwindow.engines.common import Event, assign_segments, generate_window_keys
from kindwindow.engines.pandas_engine import compute_windows, ingest_jsonl
from kindwindow.features.features import (
    assign_segments_from_epochs,
    assign_splits,
    build_recency_design,
    compute_windows_vectorized,
    fit_recency_scaler,
    training_eda,
)
from kindwindow.models.baselines import (
    brier_score,
    equal_home_weights,
    evaluate_baselines,
    fit_constant,
    fit_hour_of_week,
    fit_recency_logistic,
    hour_of_week,
    macro_brier,
    predict_constant,
    predict_hour_of_week,
    predict_recency_logistic,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
REAL_NORMALIZED = ROOT / "local" / "normalized"


def events_to_arrays(events: list[Event]):
    """File-order numpy arrays for the vectorized path."""
    epoch = np.array([int(pd.Timestamp(e.time).value) for e in events], dtype=np.int64)
    door = np.array([e.type == "door" for e in events], dtype=bool)
    pos = np.array([e.is_positive for e in events], dtype=bool)
    return epoch, door, pos


def rows_of(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.itertuples(index=False):
        recency = r.recency_seconds
        out.append(
            {
                "home": r.home,
                "t": pd.Timestamp(r.t).to_pydatetime(),
                "count_5m": int(r.count_5m),
                "count_30m": int(r.count_30m),
                "count_120m": int(r.count_120m),
                "door_5m": int(r.door_5m),
                "door_30m": int(r.door_30m),
                "door_120m": int(r.door_120m),
                "recency_seconds": None if pd.isna(recency) else int(recency),
                "recency_missing": bool(r.recency_missing),
                "hour": int(r.hour),
                "weekday": int(r.weekday),
                "prior_freq_per_hour": float(r.prior_freq_per_hour),
                "label": int(r.label),
                "member": r.member,
                "segment": int(r.segment),
            }
        )
    return sorted(out, key=lambda d: (d["home"], d["t"]))


def oracle_windows(events: list[Event], quarantined=None) -> list[dict]:
    """Independent slow oracle (same as tests/test_pandas_engine.py)."""
    quarantined = quarantined or []
    segments = sorted(assign_segments(events), key=lambda s: (s.member, s.index, s.span_start))
    rows: list[dict] = []
    seen: set[tuple[str, datetime]] = set()
    for seg in segments:
        ordered = sorted(seg.events, key=lambda e: e.time)
        member_q = [q for q in quarantined if q.member == seg.member]
        bad_times = [q.time for q in member_q if q.time is not None]
        bad_days = {q.day for q in member_q if q.day is not None}
        if any(q.time is None and q.day is None for q in member_q):
            continue
        for t in generate_window_keys(seg.span_start, seg.span_end):
            end = t + timedelta(minutes=30)
            if any(t <= bad < end for bad in bad_times):
                continue
            if t.date() in bad_days:
                continue
            if (seg.home, t) in seen:
                continue
            seen.add((seg.home, t))

            def count(w: int, *, door_only: bool = False) -> int:
                lo = t - timedelta(minutes=w)
                return sum(
                    1 for e in ordered if lo <= e.time < t and (not door_only or e.type == "door")
                )

            earlier = [e.time for e in ordered if e.time < t]
            recency = int((t - max(earlier)).total_seconds()) if earlier else None
            label = 1 if any(e.is_positive and t <= e.time < end for e in ordered) else 0
            rows.append(
                {
                    "home": seg.home,
                    "t": t,
                    "count_5m": count(5),
                    "count_30m": count(30),
                    "count_120m": count(120),
                    "door_5m": count(5, door_only=True),
                    "door_30m": count(30, door_only=True),
                    "door_120m": count(120, door_only=True),
                    "recency_seconds": recency,
                    "recency_missing": recency is None,
                    "hour": t.hour,
                    "weekday": t.weekday(),
                    "prior_freq_per_hour": count(120) / 2.0,
                    "label": label,
                    "member": seg.member,
                    "segment": seg.index,
                }
            )
    return sorted(rows, key=lambda r: (r["home"], r["t"]))


# ---------------------------------------------------------------------------
# Vectorized windows vs pandas engine / oracle on the golden fixture
# ---------------------------------------------------------------------------


def test_vectorized_matches_pandas_engine_and_oracle_on_golden():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    epoch, door, pos = events_to_arrays(ingest.events)
    vec_df, report = compute_windows_vectorized("fixture-A", "fabricated.jsonl", epoch, door, pos)
    fast_df, fast_report = compute_windows(ingest.events, ingest.quarantined)
    assert report["n_excluded_windows"] == 0
    assert report["n_discarded_duplicate_windows"] == 0
    assert rows_of(vec_df) == rows_of(fast_df)
    assert rows_of(vec_df) == oracle_windows(ingest.events, ingest.quarantined)


def test_vectorized_matches_golden_csv():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    epoch, door, pos = events_to_arrays(ingest.events)
    vec_df, _ = compute_windows_vectorized("fixture-A", "fabricated.jsonl", epoch, door, pos)
    expected = [
        ("fixture-A", datetime(2030, 2, 1, 10, 0), 2, 3, 4, 2, 1, 0),
        ("fixture-A", datetime(2030, 2, 1, 10, 30), 0, 1, 4, 0, 1800, 1),
        ("fixture-A", datetime(2030, 2, 1, 11, 0), 0, 2, 6, 1, 1800, 0),
    ]
    got = [
        (
            r["home"],
            r["t"],
            r["count_5m"],
            r["count_30m"],
            r["count_120m"],
            r["door_30m"],
            r["recency_seconds"],
            r["label"],
        )
        for r in rows_of(vec_df)
    ]
    assert got == expected


def test_vectorized_segments_match_common_assign_segments():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    epoch, _, _ = events_to_arrays(ingest.events)
    assert assign_segments_from_epochs(epoch) == [(0, len(epoch))]
    # Hand-built reversal: times 08:00, 09:00, 08:30 (corroborated by 08:45),
    # then 10:00. The 08:30 event starts a new segment.
    base = datetime(2030, 3, 1, 8, 0)
    times = [
        base,
        base + timedelta(hours=1),
        base + timedelta(minutes=30),
        base + timedelta(minutes=45),
        base + timedelta(hours=2),
    ]
    arr = np.array([int(pd.Timestamp(t).value) for t in times], dtype=np.int64)
    assert assign_segments_from_epochs(arr) == [(0, 2), (2, 5)]
    # Single out-of-order row with no continuation stays in its segment.
    times2 = [
        base,
        base + timedelta(hours=1),
        base + timedelta(minutes=30),
        base + timedelta(hours=2),
    ]
    arr2 = np.array([int(pd.Timestamp(t).value) for t in times2], dtype=np.int64)
    assert assign_segments_from_epochs(arr2) == [(0, 4)]


def _segments_common(times: list[datetime]):
    events = [
        Event(
            home="h",
            member="m",
            row=i + 1,
            time=t,
            sensor="M1",
            type="motion",
            message="ON",
            is_positive=True,
        )
        for i, t in enumerate(times)
    ]
    return [(s.events[0].row - 1, s.events[-1].row - 1) for s in assign_segments(events)]


def test_vectorized_segments_match_common_on_fold_pattern():
    """DST-fold pattern: the high-water mark resets at each split.

    Regression test: an earlier implementation compared every event
    against the *global* running maximum, over-splitting a repeated
    hour into one segment per event. The frozen rule resets the high
    after a split, so a repeated hour is one new segment.
    """
    base = datetime(2030, 3, 1, 10, 0)
    times = [
        base,
        base + timedelta(hours=1),
        base + timedelta(minutes=30),
        base + timedelta(minutes=31),
        base + timedelta(minutes=32),
        base + timedelta(hours=2),
        base + timedelta(hours=3),
    ]
    arr = np.array([int(pd.Timestamp(t).value) for t in times], dtype=np.int64)
    vec = [(s, e - 1) for s, e in assign_segments_from_epochs(arr)]
    assert vec == _segments_common(times) == [(0, 1), (2, 6)]
    # Permanent setback (clock goes back and never recovers): one split.
    times = [
        base,
        base + timedelta(hours=2),
        base + timedelta(minutes=30),
        base + timedelta(minutes=31),
        base + timedelta(minutes=32),
    ]
    arr = np.array([int(pd.Timestamp(t).value) for t in times], dtype=np.int64)
    vec = [(s, e - 1) for s, e in assign_segments_from_epochs(arr)]
    assert vec == _segments_common(times) == [(0, 1), (2, 4)]


def test_vectorized_segments_match_common_randomized():
    """Randomized property test vs common.assign_segments (seed 2718)."""
    import random

    rng = random.Random(2718)
    base = datetime(2030, 3, 1, 10, 0)
    for _ in range(300):
        t = base
        times = [t]
        for _ in range(rng.randint(1, 25)):
            step = rng.choice([-3600, -1800, -60, -1, 0, 1, 60, 1800, 3600])
            t = t + timedelta(seconds=step)
            times.append(t)
        arr = np.array([int(pd.Timestamp(x).value) for x in times], dtype=np.int64)
        vec = [(s, e - 1) for s, e in assign_segments_from_epochs(arr)]
        assert vec == _segments_common(times)


def test_vectorized_malformed_time_excludes_only_that_window():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    epoch, door, pos = events_to_arrays(ingest.events)
    # Malformed row at 10:15: only the 10:00 window's target contains it.
    bad = np.array([int(pd.Timestamp(datetime(2030, 2, 1, 10, 15)).value)], dtype=np.int64)
    vec_df, report = compute_windows_vectorized(
        "fixture-A",
        "fabricated.jsonl",
        epoch,
        door,
        pos,
        malformed_times_ns=bad,
    )
    assert report["n_excluded_windows"] == 1
    assert len(vec_df) == 2
    assert datetime(2030, 2, 1, 10, 0) not in set(vec_df["t"])


def test_vectorized_matches_real_slice_hh112_first_30_days():
    """Exact key/count/label equality on a small real slice.

    Skipped when the normalized real data is not present (ingest-real
    has not run yet). Uses only the first 30 days of hh112 events.
    """
    files = sorted(REAL_NORMALIZED.rglob("*.parquet"))
    hh112 = [f for f in files if "home=hh112" in str(f)]
    if not hh112:
        pytest.skip("no normalized hh112 data yet; run kindwindow ingest-real first")
    df = pd.concat([pd.read_parquet(f) for f in hh112], ignore_index=True)
    df = df.sort_values(["time", "row"], kind="stable")
    cutoff = df["time"].min() + pd.Timedelta(days=30)
    df = df[df["time"] < cutoff]
    assert len(df) > 1000
    events = [
        Event(
            home=str(r.home),
            member=str(r.member),
            row=int(r.row),
            time=pd.Timestamp(r.time).to_pydatetime(),
            sensor=str(r.sensor),
            type=str(r.type),
            message=str(r.message),
            is_positive=bool(r.is_positive),
        )
        for r in df.itertuples(index=False)
    ]
    epoch, door, pos = events_to_arrays(events)
    vec_df, vec_report = compute_windows_vectorized("hh112", "data/hh112.csv", epoch, door, pos)
    fast_df, fast_report = compute_windows(events, [])
    assert rows_of(vec_df) == rows_of(fast_df)
    assert vec_report["n_windows"] == len(fast_df)
    assert vec_report["n_excluded_windows"] == fast_report["excluded_windows"]


# ---------------------------------------------------------------------------
# Splits / purge
# ---------------------------------------------------------------------------


def _synthetic_windows(n=100, home="h1", start=datetime(2030, 1, 1)):
    times = [start + timedelta(minutes=30 * i) for i in range(n)]
    return pd.DataFrame(
        {
            "home": home,
            "t": pd.to_datetime(times),
            "count_5m": 0,
            "count_30m": 0,
            "count_120m": 0,
            "door_5m": 0,
            "door_30m": 0,
            "door_120m": 0,
            "recency_seconds": pd.array([60] * n, dtype="Int64"),
            "recency_missing": False,
            "hour": [t.hour for t in times],
            "weekday": [t.weekday() for t in times],
            "prior_freq_per_hour": 0.0,
            "label": [1 if i % 3 == 0 else 0 for i in range(n)],
            "member": "m",
            "segment": 0,
        }
    )


def test_split_cuts_are_frozen_floors():
    windows = _synthetic_windows(100)
    assigned, summary = assign_splits(windows)
    row = summary.iloc[0]
    assert (row["cut_train_end"], row["cut_selection_end"], row["cut_calibration_end"]) == (
        60,
        70,
        80,
    )
    # 24 h purge around boundaries at indices 60/70/80 (hours 30/35/40):
    # the union of |t-b| <= 24 h covers indices 12..99, i.e. 88 windows.
    assert row["n_purged"] == 88
    assert row["n_train"] == 12
    assert row["n_selection"] == 0 and row["n_calibration"] == 0
    assert row["n_test"] == 0


def test_purge_is_24h_both_sides_and_crossing_removed():
    # 10 windows, 30 min apart: cuts floor(.6*10)=6, floor(.7*10)=7,
    # floor(.8*10)=8. Boundaries at t[6], t[7], t[8]; every window is
    # within 24 h of a boundary, so all are purged.
    windows = _synthetic_windows(10)
    assigned, summary = assign_splits(windows)
    assert summary.iloc[0]["n_purged"] == 10
    assert set(assigned["split"]) == {"purged"}


def test_split_assignment_order_and_boundaries():
    windows = _synthetic_windows(1000)
    assigned, summary = assign_splits(windows)
    assert assigned["split"].iloc[0] == "train"
    # First surviving selection window is the first index >= 60 that is
    # more than 24 h after the t[60] boundary.
    sel = assigned[assigned["split"] == "selection"]
    assert len(sel) > 0
    assert pd.Timestamp(sel["t"].min()) > pd.Timestamp(
        summary.iloc[0]["boundary_train_selection"]
    ) + pd.Timedelta(hours=24)
    # Chronological order is preserved.
    assert assigned["t"].is_monotonic_increasing
    # Split labels follow the unpurged cut order.
    unpurged = assigned[assigned["split"] != "purged"]
    assert unpurged["split"].iloc[0] == "train"
    assert unpurged["split"].iloc[-1] == "test"


def test_training_eda_train_only():
    windows = _synthetic_windows(100)
    assigned, _ = assign_splits(windows)
    eda = training_eda(assigned)
    total = eda[eda["hour"] == "all"].iloc[0]
    assert total["n"] == int((assigned["split"] == "train").sum())
    assert total["positives"] == int(assigned.loc[assigned["split"] == "train", "label"].sum())
    assert total["prevalence"] == pytest.approx(total["positives"] / total["n"])
    hourly = eda[eda["hour"] != "all"]
    assert hourly["n"].sum() == total["n"]


# ---------------------------------------------------------------------------
# Baselines on synthetic data (hand-checked formulas)
# ---------------------------------------------------------------------------


def _toy_frames():
    train = pd.DataFrame(
        {
            "home": ["a"] * 4 + ["b"] * 4,
            "t": pd.to_datetime(["2030-01-07 00:00"] * 8),  # a Monday
            "count_5m": 0,
            "count_30m": 0,
            "count_120m": 0,
            "door_5m": 0,
            "door_30m": 0,
            "door_120m": 0,
            "recency_seconds": pd.array([60, 120, 180, 240, 30, 60, 90, 120], dtype="Int64"),
            "recency_missing": [False] * 8,
            "hour": [0, 0, 1, 1, 0, 0, 1, 1],
            "weekday": [0] * 8,
            "prior_freq_per_hour": 0.0,
            "label": [1, 0, 1, 0, 0, 0, 0, 1],
            "member": "m",
            "segment": 0,
            "split": "train",
        }
    )
    selection = train.copy()
    selection["split"] = "selection"
    return train, selection


def test_constant_baseline_is_train_prevalence():
    train, selection = _toy_frames()
    model = fit_constant(train)
    assert model.home_rates == {"a": 0.5, "b": 0.25}
    p = predict_constant(model, selection)
    assert list(p) == [0.5, 0.5, 0.5, 0.5, 0.25, 0.25, 0.25, 0.25]
    # Brier for home a on train: mean((0.5-y)^2) = 0.25.
    assert brier_score(train[train["home"] == "a"]["label"], p[:4]) == 0.25


def test_hour_of_week_pseudocount_20():
    train, selection = _toy_frames()
    model = fit_hour_of_week(train)
    # Home a, bucket Monday 00:00 (0): n=2, pos=1, home_rate=0.5 ->
    # (1 + 20*0.5) / (2 + 20) = 11/22 = 0.5.
    assert model.table[("a", 0)] == pytest.approx(0.5)
    # Home b, bucket Monday 01:00 (1): n=2, pos=1, home_rate=0.25 ->
    # (1 + 5) / 22 = 6/22.
    assert model.table[("b", 1)] == pytest.approx(6 / 22)
    # Unseen bucket falls back to the home rate.
    p = predict_hour_of_week(model, selection.assign(hour=5))
    assert list(p[:4]) == [0.5] * 4  # home a fallback
    assert hour_of_week(selection).tolist() == [0, 0, 1, 1, 0, 0, 1, 1]


def test_recency_logistic_missing_handling_and_weights():
    train, selection = _toy_frames()
    # Equal-home weights: N=8, H=2, n_home=4 -> 8/(2*4) = 1.0 each.
    assert list(equal_home_weights(train)) == [1.0] * 8
    scaler = fit_recency_scaler(train)
    assert scaler["mean"] == pytest.approx(np.mean([60, 120, 180, 240, 30, 60, 90, 120]))
    model = fit_recency_logistic(train)
    p = predict_recency_logistic(model, selection)
    assert len(p) == 8 and all(0.0 <= v <= 1.0 for v in p)
    # Missing recency -> scaled 0 + indicator 1; design has 2 + 2 columns.
    missing = selection.copy()
    missing.loc[0, "recency_missing"] = True
    missing.loc[0, "recency_seconds"] = pd.NA
    X, names = build_recency_design(missing, ["a", "b"], scaler)
    assert names == ["recency_scaled", "recency_missing", "home_a", "home_b"]
    assert X[0, 0] == 0.0 and X[0, 1] == 1.0
    assert X[1, 1] == 0.0


def test_evaluate_baselines_reports_train_and_selection_macro():
    train, selection = _toy_frames()
    result = evaluate_baselines(train, selection)
    assert set(result["baseline"]) == {"constant", "hour_of_week", "recency_logistic"}
    assert set(result["split"]) == {"train", "selection"}
    macro = result[result["home"] == "MACRO"]
    assert len(macro) == 6  # 3 baselines x 2 splits
    # Macro is the mean of the two per-home Briers.
    for _, row in macro.iterrows():
        per_home = result[
            (result["baseline"] == row["baseline"])
            & (result["split"] == row["split"])
            & (result["home"] != "MACRO")
        ]
        assert row["brier"] == pytest.approx(
            macro_brier(dict(zip(per_home["home"], per_home["brier"])))
        )
    assert "test" not in set(result["split"])
