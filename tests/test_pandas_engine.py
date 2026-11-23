"""Step 04: pandas pipeline tests.

The golden fixture must reproduce ``tests/fixtures/golden_windows.csv``
exactly. Every other fixture is hand-checked below (expected values were
computed by hand from the event lists, not copied from engine output) and
the fast path is additionally checked against an independent slow oracle
implemented in this file: a naive per-window scan over the sorted events.
The oracle shares only ``common.generate_window_keys`` /
``common.assign_segments`` (the key/segment definitions); all counting,
recency and labelling logic is reimplemented with plain loops.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from kindwindow.cli import main as cli_main
from kindwindow.engines import common
from kindwindow.engines.common import Event, assign_segments, generate_window_keys
from kindwindow.engines.pandas_engine import (
    compute_windows,
    ingest_jsonl,
    mark_long_gap_windows,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Independent slow oracle (naive per-window scan)
# ---------------------------------------------------------------------------

def oracle_windows(events: list[Event], quarantined=None) -> list[dict]:
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
            continue  # unlocatable quarantine excludes the whole segment
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


def rows_of(df: pd.DataFrame) -> list[dict]:
    """Normalise a fast-path windows frame to oracle-shaped dicts."""
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


def assert_fast_matches_oracle(events, quarantined=None):
    fast_df, _report = compute_windows(events, quarantined)
    assert rows_of(fast_df) == oracle_windows(events, quarantined)


def write_jsonl(tmp_path: Path, name: str, records: list[dict | str]) -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec if isinstance(rec, str) else json.dumps(rec))
            fh.write("\n")
    return path


def ev(home, member, row, time, sensor="M1", type="motion", message="ON"):
    return {"home": home, "member": member, "row": row, "time": time,
            "sensor": sensor, "type": type, "message": message}


# ---------------------------------------------------------------------------
# Golden fixture
# ---------------------------------------------------------------------------

def test_golden_fixture_matches_expected_csv():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    assert ingest.ledger.reconciles
    assert ingest.ledger.accepted_first == 9
    assert ingest.ledger.quarantined_first == 0
    assert ingest.ledger.replays == 0
    windows, report = compute_windows(ingest.events, ingest.quarantined)
    assert report["excluded_windows"] == 0
    assert report["discarded_duplicate_windows"] == 0

    expected = []
    with (FIXTURES / "golden_windows.csv").open(newline="") as fh:
        for row in csv.DictReader(fh):
            expected.append(
                (
                    row["home"],
                    datetime.fromisoformat(row["t"]),
                    int(row["count_5m"]),
                    int(row["count_30m"]),
                    int(row["count_120m"]),
                    int(row["door_30m"]),
                    int(row["recency_seconds"]),
                    int(row["y"]),
                )
            )
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
        for r in rows_of(windows)
    ]
    assert got == expected  # exactly the 3 golden windows
    # Full hand-checked columns beyond the CSV: door counts, recency flag, hour.
    by_t = {r["t"]: r for r in rows_of(windows)}
    assert by_t[datetime(2030, 2, 1, 10, 0)]["door_5m"] == 1  # 09:59:59 CLOSE
    assert by_t[datetime(2030, 2, 1, 10, 0)]["door_120m"] == 2
    assert by_t[datetime(2030, 2, 1, 10, 30)]["door_120m"] == 2
    assert by_t[datetime(2030, 2, 1, 11, 0)]["door_120m"] == 3
    assert all(not r["recency_missing"] for r in by_t.values())
    assert by_t[datetime(2030, 2, 1, 10, 0)]["hour"] == 10
    assert by_t[datetime(2030, 2, 1, 10, 0)]["weekday"] == datetime(2030, 2, 1).weekday()
    assert by_t[datetime(2030, 2, 1, 10, 0)]["prior_freq_per_hour"] == 2.0  # 4 events / 2 h


def test_golden_fast_matches_oracle():
    ingest = ingest_jsonl(FIXTURES / "golden_events.jsonl")
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


# ---------------------------------------------------------------------------
# Replay + legitimate same-timestamp duplicates
# ---------------------------------------------------------------------------

def test_replay_counted_separately_and_same_timestamp_rows_are_legitimate():
    ingest = ingest_jsonl(FIXTURES / "replay_events.jsonl")
    assert ingest.ledger.total_lines == 8
    assert ingest.ledger.accepted_first == 7  # row 7 duplicates row 3's payload, new row
    assert ingest.ledger.replays == 1  # exact repeat of row 2
    assert ingest.ledger.quarantined_first == 0
    assert ingest.ledger.reconciles
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    got = [(r["t"], r["count_5m"], r["count_30m"], r["count_120m"],
            r["door_30m"], r["recency_seconds"], r["label"]) for r in rows_of(windows)]
    # Hand-checked: the two distinct 10:00 ON rows both count in history/labels.
    assert got == [
        (datetime(2030, 3, 1, 10, 0), 0, 0, 2, 0, 3600, 1),
        (datetime(2030, 3, 1, 10, 30), 0, 2, 3, 0, 1800, 1),
        (datetime(2030, 3, 1, 11, 0), 0, 1, 4, 1, 1800, 0),
    ]
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_conflicting_duplicate_row_number_is_quarantined_not_replayed():
    path_records = [
        ev("fixture-C", "conflict.jsonl", 1, "2030-03-01T08:00:00"),
        ev("fixture-C", "conflict.jsonl", 2, "2030-03-01T09:00:00", message="OFF"),
        ev("fixture-C", "conflict.jsonl", 3, "2030-03-01T10:00:00"),
        ev("fixture-C", "conflict.jsonl", 4, "2030-03-01T10:30:00",
           sensor="D1", type="door", message="OPEN"),
        ev("fixture-C", "conflict.jsonl", 5, "2030-03-01T11:00:00", message="OFF"),
        ev("fixture-C", "conflict.jsonl", 6, "2030-03-01T11:30:00", message="OFF"),
        # Same row 2, different time/payload: not a replay, an ambiguous conflict.
        ev("fixture-C", "conflict.jsonl", 2, "2030-03-01T09:30:00"),
    ]
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = write_jsonl(Path(td), "conflict.jsonl", path_records)
        ingest = ingest_jsonl(path)
    assert ingest.ledger.accepted_first == 6
    assert ingest.ledger.quarantined_first == 1
    assert ingest.ledger.replays == 0
    assert ingest.ledger.reconciles
    assert "conflicting duplicate" in ingest.quarantined[0].reason
    windows, report = compute_windows(ingest.events, ingest.quarantined)
    # Conflicting time 09:30 falls in no target, so no window is excluded.
    assert report["excluded_windows"] == 0
    assert len(windows) == 3
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


# ---------------------------------------------------------------------------
# Cross-month history, DST, adverse quarantine, gaps, reversals, overlaps
# ---------------------------------------------------------------------------

def test_cross_month_history_changes_first_next_month_output():
    ingest = ingest_jsonl(FIXTURES / "cross_month_events.jsonl")
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    got = [(r["t"], r["count_5m"], r["count_30m"], r["count_120m"],
            r["door_30m"], r["recency_seconds"], r["label"]) for r in rows_of(windows)]
    # Both windows are in February, but their history/recency come from
    # January events: without the prior month, counts would be 0/1 and
    # recency at 00:00 would be missing.
    assert got == [
        (datetime(2030, 2, 1, 0, 0), 0, 1, 2, 1, 1800, 0),
        (datetime(2030, 2, 1, 0, 30), 0, 1, 2, 0, 1800, 1),
    ]
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_dst_spring_forward_nonexistent_time_quarantined_with_timezone():
    ingest = ingest_jsonl(FIXTURES / "dst_events.jsonl", timezone_name="America/Chicago")
    assert ingest.ledger.accepted_first == 5
    assert ingest.ledger.quarantined_first == 1
    assert "nonexistent" in ingest.quarantined[0].reason
    windows, report = compute_windows(ingest.events, ingest.quarantined)
    got = [(r["t"], r["count_5m"], r["count_30m"], r["count_120m"],
            r["recency_seconds"], r["label"]) for r in rows_of(windows)]
    # 02:30 is quarantined *and* lies in target [02:30, 03:00), so that
    # window is excluded; wall-clock half-hour keys continue after the gap.
    assert got == [
        (datetime(2030, 3, 10, 3, 0), 0, 0, 1, 5400, 0),
        (datetime(2030, 3, 10, 3, 30), 0, 0, 1, 7200, 1),
        (datetime(2030, 3, 10, 4, 0), 0, 1, 1, 1800, 0),
    ]
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_dst_without_timezone_uses_wall_clock_and_accepts_0230():
    ingest = ingest_jsonl(FIXTURES / "dst_events.jsonl")  # no timezone: naive wall clock
    assert ingest.ledger.accepted_first == 6
    assert ingest.ledger.quarantined_first == 0
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    got = [(r["t"], r["count_120m"], r["recency_seconds"], r["label"]) for r in rows_of(windows)]
    assert got == [
        (datetime(2030, 3, 10, 2, 30), 2, 3600, 1),
        (datetime(2030, 3, 10, 3, 0), 2, 1800, 0),
        (datetime(2030, 3, 10, 3, 30), 2, 3600, 1),
        (datetime(2030, 3, 10, 4, 0), 2, 1800, 0),
    ]


def test_dst_fall_back_ambiguous_time_quarantined_unless_fold_given():
    records = [
        ev("fixture-D", "fall.jsonl", 1, "2030-11-03T00:30:00"),
        ev("fixture-D", "fall.jsonl", 2, "2030-11-03T01:30:00", message="OFF"),  # ambiguous
        ev("fixture-D", "fall.jsonl", 3, "2030-11-03T02:30:00"),
        ev("fixture-D", "fall.jsonl", 4, "2030-11-03T03:30:00", message="OFF"),
        ev("fixture-D", "fall.jsonl", 5, "2030-11-03T04:30:00", message="OFF"),
    ]
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = write_jsonl(Path(td), "fall.jsonl", records)
        ingest = ingest_jsonl(path, timezone_name="America/Chicago")
    assert ingest.ledger.quarantined_first == 1
    assert "ambiguous" in ingest.quarantined[0].reason
    # Same record with an explicit fold resolves the ambiguity.
    folded = dict(records[1], fold=1)
    parsed = common.parse_record(folded, timezone_name="America/Chicago")
    assert isinstance(parsed, Event)


def test_quarantined_event_in_target_excludes_only_that_window():
    ingest = ingest_jsonl(FIXTURES / "adverse_events.jsonl")
    assert ingest.ledger.accepted_first == 6
    assert ingest.ledger.quarantined_first == 1  # 10:15 motion UNKNOWN
    windows, report = compute_windows(ingest.events, ingest.quarantined)
    assert report["excluded_windows"] == 1  # target [10:00, 10:30) contains 10:15
    got = [(r["t"], r["count_5m"], r["count_30m"], r["count_120m"],
            r["door_30m"], r["recency_seconds"], r["label"]) for r in rows_of(windows)]
    assert got == [
        (datetime(2030, 4, 1, 10, 30), 0, 1, 1, 0, 1800, 1),
        (datetime(2030, 4, 1, 11, 0), 0, 1, 2, 0, 1800, 0),
        (datetime(2030, 4, 1, 11, 30), 0, 1, 3, 0, 1800, 1),
    ]
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_unknown_day_time_and_unmapped_rows_are_quarantined(tmp_path):
    records = [
        ev("fixture-U", "bad.jsonl", 1, "2030-05-01T08:00:00"),
        "",  # blank line: explicitly ignored, not quarantined
        "{not json",
        {"home": "fixture-U", "member": "bad.jsonl", "row": 4, "sensor": "M1",
         "type": "motion", "message": "ON"},  # missing time
        ev("fixture-U", "bad.jsonl", 5, "2030-05-01T09:00:00", message="OPEN"),  # motion OPEN
        ev("fixture-U", "bad.jsonl", 6, "2030-05-01T09:30:00",
           sensor="D1", type="door", message="ON"),
        ev("fixture-U", "bad.jsonl", 7, "2030-05-01T10:00:00", type="camera", message="ON"),
        {"home": "fixture-U", "member": "bad.jsonl", "row": 8,
         "time": "2030-05-01T10:30:00", "type": "motion", "message": "ON"},  # missing sensor
        ev("fixture-U", "bad.jsonl", 9, "2030-05-01T11:30:00", message="OFF"),
    ]
    path = write_jsonl(tmp_path, "bad.jsonl", records)
    ingest = ingest_jsonl(path)
    assert ingest.ledger.total_lines == 9
    assert ingest.ledger.accepted_first == 2
    assert ingest.ledger.quarantined_first == 6
    assert ingest.ledger.ignored == 1
    assert ingest.ledger.reconciles
    reasons = " ".join(q.reason for q in ingest.quarantined)
    assert "invalid JSON" in reasons and "unmapped" in reasons


def test_unlocatable_quarantine_excludes_the_whole_segment(tmp_path):
    records = [
        ev("fixture-U", "unloc.jsonl", 1, "2030-05-01T08:00:00"),
        ev("fixture-U", "unloc.jsonl", 2, "2030-05-01T10:00:00", message="OFF"),
        ev("fixture-U", "unloc.jsonl", 3, "2030-02-30T10:00:00"),  # impossible date
        ev("fixture-U", "unloc.jsonl", 4, "2030-05-01T10:30:00"),
        ev("fixture-U", "unloc.jsonl", 5, "2030-05-01T11:00:00", message="OFF"),
        ev("fixture-U", "unloc.jsonl", 6, "2030-05-01T11:30:00", message="OFF"),
    ]
    path = write_jsonl(tmp_path, "unloc.jsonl", records)
    ingest = ingest_jsonl(path)
    assert ingest.ledger.quarantined_first == 1
    assert ingest.quarantined[0].time is None and ingest.quarantined[0].day is None
    windows, report = compute_windows(ingest.events, ingest.quarantined)
    # The day cannot be bounded, so the segment is excluded rather than guessed.
    assert len(windows) == 0
    assert report["excluded_windows"] == 3  # keys 10:00, 10:30, 11:00 would exist


def test_silence_yields_clean_zeros_and_long_gap_mask(tmp_path):
    records = [
        ev("fixture-G", "gap.jsonl", 1, "2030-05-01T08:00:00"),
        ev("fixture-G", "gap.jsonl", 2, "2030-05-01T08:30:00", message="OFF"),
        ev("fixture-G", "gap.jsonl", 3, "2030-05-01T16:00:00"),
        ev("fixture-G", "gap.jsonl", 4, "2030-05-01T16:30:00", message="OFF"),
        ev("fixture-G", "gap.jsonl", 5, "2030-05-01T17:00:00", message="OFF"),
    ]
    path = write_jsonl(tmp_path, "gap.jsonl", records)
    ingest = ingest_jsonl(path)
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    by_t = {r["t"]: r for r in rows_of(windows)}
    noon = by_t[datetime(2030, 5, 1, 12, 0)]
    assert noon["count_5m"] == noon["count_30m"] == noon["count_120m"] == 0
    assert noon["recency_seconds"] == 12600  # 08:30 -> 12:00, no invented zero
    assert noon["recency_missing"] is False
    assert noon["label"] == 0
    assert by_t[datetime(2030, 5, 1, 16, 0)]["label"] == 1
    flagged_6h = mark_long_gap_windows(windows, ingest.events, threshold_hours=6)
    flagged_12h = mark_long_gap_windows(windows, ingest.events, threshold_hours=12)
    assert bool(flagged_6h[windows["t"] == pd.Timestamp("2030-05-01 12:00")].iloc[0]) is True
    assert bool(flagged_6h[windows["t"] == pd.Timestamp("2030-05-01 16:00")].iloc[0]) is False
    assert not flagged_12h.any()  # 7.5 h gap is not > 12 h
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_clock_reversal_starts_new_segment_and_history_does_not_bridge(tmp_path):
    records = [
        ev("fixture-V", "rev.jsonl", 1, "2030-05-01T08:00:00"),
        ev("fixture-V", "rev.jsonl", 2, "2030-05-01T09:00:00", message="OFF"),
        ev("fixture-V", "rev.jsonl", 3, "2030-05-01T10:00:00"),
        ev("fixture-V", "rev.jsonl", 4, "2030-05-01T10:30:00",
           sensor="D1", type="door", message="OPEN"),
        ev("fixture-V", "rev.jsonl", 5, "2030-05-01T11:00:00", message="OFF"),
        ev("fixture-V", "rev.jsonl", 6, "2030-05-01T09:30:00"),  # reversal: new segment
        ev("fixture-V", "rev.jsonl", 7, "2030-05-01T10:00:00", message="OFF"),
        ev("fixture-V", "rev.jsonl", 8, "2030-05-01T11:30:00", message="OFF"),
        ev("fixture-V", "rev.jsonl", 9, "2030-05-01T12:00:00", message="OFF"),
        ev("fixture-V", "rev.jsonl", 10, "2030-05-01T12:30:00", message="OFF"),
    ]
    path = write_jsonl(tmp_path, "rev.jsonl", records)
    ingest = ingest_jsonl(path)
    assert len(assign_segments(ingest.events)) == 2
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    got = [(r["t"], r["count_120m"], r["recency_seconds"], r["label"], r["segment"])
           for r in rows_of(windows)]
    assert got == [
        (datetime(2030, 5, 1, 10, 0), 2, 3600, 1, 0),
        (datetime(2030, 5, 1, 10, 30), 2, 1800, 1, 0),
        # Segment 2: recency at 11:30 is 10:00 (5400 s), not the 11:00 event
        # from segment 1 (which would give 1800 s if history bridged).
        (datetime(2030, 5, 1, 11, 30), 2, 5400, 0, 1),
        (datetime(2030, 5, 1, 12, 0), 2, 1800, 0, 1),
    ]
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_overlapping_file_spans_emit_each_window_key_once(tmp_path):
    records_a = [
        ev("fixture-O", "a.jsonl", 1, "2030-05-01T08:00:00"),
        ev("fixture-O", "a.jsonl", 2, "2030-05-01T10:00:00", message="OFF"),
        ev("fixture-O", "a.jsonl", 3, "2030-05-01T10:30:00"),
        ev("fixture-O", "a.jsonl", 4, "2030-05-01T11:00:00", message="OFF"),
        ev("fixture-O", "a.jsonl", 5, "2030-05-01T11:30:00", message="OFF"),
    ]
    records_b = [
        ev("fixture-O", "b.jsonl", 1, "2030-05-01T08:00:00", sensor="M2"),
        ev("fixture-O", "b.jsonl", 2, "2030-05-01T10:00:00", sensor="M2", message="OFF"),
        ev("fixture-O", "b.jsonl", 3, "2030-05-01T10:30:00", sensor="M2"),
        ev("fixture-O", "b.jsonl", 4, "2030-05-01T11:00:00", sensor="M2", message="OFF"),
        ev("fixture-O", "b.jsonl", 5, "2030-05-01T11:30:00", sensor="M2", message="OFF"),
    ]
    events = []
    for name, recs in (("a.jsonl", records_a), ("b.jsonl", records_b)):
        ingest = ingest_jsonl(write_jsonl(tmp_path, name, recs))
        events.extend(ingest.events)
    windows, report = compute_windows(events, [])
    assert len(windows) == 3  # keys 10:00, 10:30, 11:00, each once
    assert report["discarded_duplicate_windows"] == 3
    assert set(windows["member"]) == {"a.jsonl"}  # deterministic: first member wins
    assert_fast_matches_oracle(events, [])


def test_simultaneous_events_at_t_and_t_plus_30m(tmp_path):
    records = [
        ev("fixture-S", "sim.jsonl", 1, "2030-05-01T08:00:00"),
        ev("fixture-S", "sim.jsonl", 2, "2030-05-01T09:55:00", message="OFF"),
        ev("fixture-S", "sim.jsonl", 3, "2030-05-01T10:00:00"),  # at t: target, not history
        ev("fixture-S", "sim.jsonl", 4, "2030-05-01T10:30:00",
           message="OFF"),  # at t+30: next target
        ev("fixture-S", "sim.jsonl", 5, "2030-05-01T11:00:00", message="OFF"),
    ]
    path = write_jsonl(tmp_path, "sim.jsonl", records)
    ingest = ingest_jsonl(path)
    windows, _ = compute_windows(ingest.events, ingest.quarantined)
    by_t = {r["t"]: r for r in rows_of(windows)}
    assert by_t[datetime(2030, 5, 1, 10, 0)]["count_30m"] == 1  # only 09:55, not 10:00
    assert by_t[datetime(2030, 5, 1, 10, 0)]["label"] == 1  # 10:00 ON is in the target
    assert by_t[datetime(2030, 5, 1, 10, 30)]["count_30m"] == 1  # 10:00 now in history
    assert by_t[datetime(2030, 5, 1, 10, 30)]["label"] == 0  # 10:30 OFF only; 11:00 is next
    assert_fast_matches_oracle(ingest.events, ingest.quarantined)


def test_off_only_target_is_label_zero_and_off_counts_in_history(tmp_path):
    # Golden already covers this; assert the rule directly on the label rule.
    assert common.is_positive("motion", "OFF") is False
    assert common.is_positive("motion", "ON") is True
    assert common.is_positive("door", "OPEN") is True
    assert common.is_positive("door", "CLOSE") is True


def test_window_key_generation_boundaries():
    # Warmup: first key needs t-120m >= span start; target must end <= span end.
    keys = generate_window_keys(datetime(2030, 2, 1, 8, 0), datetime(2030, 2, 1, 11, 30))
    assert keys == [datetime(2030, 2, 1, 10, 0), datetime(2030, 2, 1, 10, 30),
                    datetime(2030, 2, 1, 11, 0)]
    # Non-aligned span start: first key is the next half-hour after start+120m.
    keys = generate_window_keys(datetime(2030, 2, 1, 8, 7), datetime(2030, 2, 1, 11, 30))
    assert keys[0] == datetime(2030, 2, 1, 10, 30)
    assert common.ceil_to_half_hour(datetime(2030, 2, 1, 9, 59, 59)) == datetime(2030, 2, 1, 10, 0)


# ---------------------------------------------------------------------------
# Parquet output + CLI
# ---------------------------------------------------------------------------

def test_pipeline_writes_partitioned_snappy_parquet_with_typed_columns(tmp_path):
    out = tmp_path / "out"
    result = run_pipeline(FIXTURES / "golden_events.jsonl", out)
    assert result.n_events == 9
    assert result.n_windows == 3
    assert (out / "_COMPLETE.json").is_file()
    manifest = json.loads((out / "_COMPLETE.json").read_text())
    assert manifest["ledger_reconciles"] is True
    assert manifest["schema_windows"] == "kindwindow.windows/v1"
    events_file = out / "normalized" / "home=fixture-A" / "year_month=2030-02" / "events.parquet"
    windows_file = out / "windows" / "home=fixture-A" / "year_month=2030-02" / "windows.parquet"
    assert events_file.is_file() and windows_file.is_file()
    import pyarrow.parquet as pq

    assert pq.read_metadata(windows_file).row_group(0).column(0).compression == "SNAPPY"
    events_df = pd.read_parquet(events_file)
    windows_df = pd.read_parquet(windows_file)
    assert str(events_df["time"].dtype) == "datetime64[ns]"
    assert str(events_df["row"].dtype) == "int64"
    assert str(windows_df["recency_seconds"].dtype) == "Int64"
    assert windows_df["label"].tolist() == [0, 1, 0]
    # Re-running republishes cleanly with no leftover temp directories.
    run_pipeline(FIXTURES / "golden_events.jsonl", out)
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]
    # Cross-month run partitions windows by the window's own year-month.
    out2 = tmp_path / "out2"
    run_pipeline(FIXTURES / "cross_month_events.jsonl", out2)
    assert (out2 / "windows" / "home=fixture-B" / "year_month=2030-02"
            / "windows.parquet").is_file()
    assert (out2 / "normalized" / "home=fixture-B" / "year_month=2030-01"
            / "events.parquet").is_file()


def test_cli_pipeline_fixture_and_real_workload(capsys, tmp_path):
    out = tmp_path / "cli-out"
    assert cli_main(["pipeline", "--engine", "pandas", "--workload", "fixture",
                     "--out", str(out)]) == 0
    assert (out / "windows" / "home=fixture-A" / "year_month=2030-02" / "windows.parquet").is_file()
    assert "ledger_reconciles: True" in capsys.readouterr().out
    # Real workload with no real normalized data: clean error, no fixture fallback.
    real_out = tmp_path / "real-out"
    code = cli_main(["pipeline", "--engine", "pandas", "--workload", "real",
                     "--out", str(real_out)])
    assert code == 2
    captured = capsys.readouterr()
    assert "no real normalized data" in captured.err
    assert not real_out.exists()
    # Spark engine is honestly not implemented yet.
    assert cli_main(["pipeline", "--engine", "spark", "--workload", "fixture"]) == 2
    assert "not implemented" in capsys.readouterr().err
