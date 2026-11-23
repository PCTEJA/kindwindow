"""Step 02 contract tests.

Every rule here is frozen in docs/data-dictionary.md and implemented
in src/kindwindow/contracts.py. The golden fixture test recomputes
all 3 windows in tests/fixtures/golden_windows.csv exactly from
tests/fixtures/golden_events.jsonl; if a frozen rule changes, the
fixture and this test must change together.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kindwindow.contracts import (
    Event,
    HistoryCounts,
    ScoredOption,
    choose_best,
    half_hour_floor,
    history_counts,
    intersects_long_gap,
    is_positive,
    purge_mask,
    retention_threshold,
    split_indices,
    window_label,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def ev(time: str, message: str, sensor_type: str = "motion") -> Event:
    return Event(time=dt(time), message=message, sensor_type=sensor_type)


# ---------------------------------------------------------------- half-hour floor


def test_half_hour_floor():
    assert half_hour_floor(dt("2030-02-01T10:00:00")) == dt("2030-02-01T10:00:00")
    assert half_hour_floor(dt("2030-02-01T10:29:59")) == dt("2030-02-01T10:00:00")
    assert half_hour_floor(dt("2030-02-01T10:30:00")) == dt("2030-02-01T10:30:00")
    assert half_hour_floor(dt("2030-02-01T10:59:59")) == dt("2030-02-01T10:30:00")
    assert half_hour_floor(dt("2030-02-01T00:00:01")) == dt("2030-02-01T00:00:00")
    # Seconds / microseconds are zeroed, never rounded up.
    assert half_hour_floor(dt("2030-02-01T23:45:30")).second == 0
    assert half_hour_floor(datetime(2030, 2, 1, 10, 15, 0, 123456)).microsecond == 0


# ---------------------------------------------------------------- is_positive


def test_is_positive_mapped_messages():
    assert is_positive("ON") is True
    assert is_positive("OPEN") is True
    assert is_positive("CLOSE") is True
    # Motion OFF is a valid mapped deactivation, not positive.
    assert is_positive("OFF") is False


def test_is_positive_normalizes_case_and_whitespace():
    assert is_positive(" on ") is True
    assert is_positive("off") is False


def test_is_positive_unmapped_raises():
    # Unmapped messages are quarantined upstream, never silently negative.
    for bad in ["UNKNOWN", "", "  ", "OPEN "[:4] + "X"]:
        with pytest.raises(ValueError):
            is_positive(bad)


# ---------------------------------------------------------------- window_label endpoints


def test_window_label_endpoint_t_belongs_to_target():
    events = [ev("2030-02-01T10:00:00", "ON")]
    assert window_label(events, dt("2030-02-01T10:00:00")) == 1


def test_window_label_endpoint_t_plus_30m_belongs_to_next_target():
    events = [ev("2030-02-01T10:30:00", "ON")]
    assert window_label(events, dt("2030-02-01T10:00:00")) == 0
    assert window_label(events, dt("2030-02-01T10:30:00")) == 1


def test_window_label_off_only_target_is_zero():
    events = [ev("2030-02-01T10:00:00", "OFF"), ev("2030-02-01T10:15:00", "OFF")]
    assert window_label(events, dt("2030-02-01T10:00:00")) == 0


def test_window_label_simultaneous_events():
    # Motion ON and door OPEN at the same instant: one positive is enough.
    events = [
        ev("2030-02-01T10:30:00", "ON", "motion"),
        ev("2030-02-01T10:30:00", "OPEN", "door"),
    ]
    assert window_label(events, dt("2030-02-01T10:30:00")) == 1


def test_window_label_empty_and_unrestricted():
    assert window_label([], dt("2030-02-01T10:00:00")) == 0
    # t=None: events are already restricted to the target interval.
    assert window_label([ev("2030-02-01T10:05:00", "CLOSE", "door")]) == 1
    assert window_label([ev("2030-02-01T10:05:00", "OFF")]) == 0


def test_window_label_unmapped_raises():
    with pytest.raises(ValueError):
        window_label([ev("2030-02-01T10:05:00", "UNKNOWN")], dt("2030-02-01T10:00:00"))


# ---------------------------------------------------------------- history_counts


def test_history_counts_half_open_intervals():
    events = [
        ev("2030-02-01T09:30:00", "OPEN", "door"),  # exactly t-30m: included in 30m
        ev("2030-02-01T09:55:00", "OFF"),
        ev("2030-02-01T09:59:59", "CLOSE", "door"),
        ev("2030-02-01T10:00:00", "OFF"),  # exactly t: target, not history
    ]
    h = history_counts(events, dt("2030-02-01T10:00:00"))
    assert h.counts == {5: 2, 30: 3, 120: 3}
    assert h.door_counts == {5: 1, 30: 2, 120: 2}
    assert h.recency_seconds == 1  # 09:59:59 -> 10:00:00


def test_history_counts_off_counts_for_history():
    events = [ev("2030-02-01T09:59:00", "OFF")]
    h = history_counts(events, dt("2030-02-01T10:00:00"))
    assert h.counts[5] == 1
    assert h.recency_seconds == 60


def test_history_counts_recency_missing_is_none():
    h = history_counts([], dt("2030-02-01T10:00:00"))
    assert h.counts == {5: 0, 30: 0, 120: 0}
    assert h.recency_seconds is None  # missing, never an invented zero


def test_history_counts_cross_month():
    # An event in the prior month changes the first next-month output:
    # month boundaries are not resets.
    events = [
        ev("2030-01-31T23:45:00", "ON"),
        ev("2030-02-01T00:15:00", "OFF"),
    ]
    h_midnight = history_counts(events, dt("2030-02-01T00:00:00"))
    assert h_midnight.counts[120] == 1  # 23:45 Jan 31 is inside [22:00, 00:00)
    assert h_midnight.recency_seconds == 900
    assert window_label(events, dt("2030-02-01T00:00:00")) == 0  # OFF-only target
    h_later = history_counts(events, dt("2030-02-01T00:30:00"))
    assert h_later.counts == {5: 0, 30: 1, 120: 2}
    assert h_later.recency_seconds == 900


# ---------------------------------------------------------------- golden fixture


def _load_golden_events() -> list[Event]:
    events = []
    for line in (FIXTURES / "golden_events.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        events.append(Event(time=dt(row["time"]), message=row["message"], sensor_type=row["type"]))
    return events


def test_golden_fixture_windows_exactly():
    events = _load_golden_events()
    assert len(events) == 9
    with (FIXTURES / "golden_windows.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3, "golden fixture must contain exactly 3 windows"
    for row in rows:
        t = dt(row["t"])
        h = history_counts(events, t)
        assert isinstance(h, HistoryCounts)
        assert h.counts[5] == int(row["count_5m"]), row
        assert h.counts[30] == int(row["count_30m"]), row
        assert h.counts[120] == int(row["count_120m"]), row
        assert h.door_counts[30] == int(row["door_30m"]), row
        assert h.recency_seconds == int(row["recency_seconds"]), row
        assert window_label(events, t) == int(row["y"]), row


# ---------------------------------------------------------------- split indices


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, (0, 0, 0)),
        (1, (0, 0, 0)),
        (3, (1, 2, 2)),
        (10, (6, 7, 8)),
        (100, (60, 70, 80)),
        (7, (4, 4, 5)),
    ],
)
def test_split_indices(n, expected):
    assert split_indices(n) == expected


def test_split_indices_segments_cover_all_and_disjoint():
    for n in (0, 1, 5, 33, 100, 1000):
        c1, c2, c3 = split_indices(n)
        assert 0 <= c1 <= c2 <= c3 <= n
        segments = [(0, c1), (c1, c2), (c2, c3), (c3, n)]
        covered = sum(b - a for a, b in segments)
        assert covered == n


def test_split_indices_negative_rejected():
    with pytest.raises(ValueError):
        split_indices(-1)


# ---------------------------------------------------------------- purge mask


def test_purge_mask_24h_both_sides_inclusive():
    boundary = dt("2030-02-10T12:00:00")
    times = [
        dt("2030-02-09T11:30:00"),  # 24h30m before: keep
        dt("2030-02-09T12:00:00"),  # exactly 24h before: purge (<=24h)
        dt("2030-02-10T11:30:00"),  # 30m before: purge
        dt("2030-02-10T12:00:00"),  # at boundary: purge
        dt("2030-02-11T12:00:00"),  # exactly 24h after: purge (<=24h)
        dt("2030-02-11T12:30:00"),  # 24h30m after: keep
    ]
    assert purge_mask(times, [boundary]) == [False, True, True, True, True, False]


def test_purge_mask_label_crossing_isolated_with_zero_purge():
    # purge_hours=0 isolates the crossing rule: with no purge window,
    # only the boundary itself and crossing windows are removed.
    boundary = dt("2030-02-10T12:00:00")
    times = [
        dt("2030-02-10T11:30:00"),  # target [11:30,12:00): ends at boundary, no cross
        dt("2030-02-10T11:45:00"),  # target [11:45,12:15): crosses boundary
        dt("2030-02-10T12:00:00"),  # at boundary: purged (|t-b| = 0)
        dt("2030-02-10T12:30:00"),  # after boundary: keep
    ]
    assert purge_mask(times, [boundary], purge_hours=0) == [False, True, True, False]


def test_purge_mask_counts_on_a_grid():
    # Half-hour origins over 5 days around one boundary: origins with
    # |t - b| <= 24h are purged. 24h each side = 48h span = 97 origins
    # (both endpoints inclusive) on a 30-minute grid.
    boundary = dt("2030-02-10T12:00:00")
    start = boundary - timedelta(days=3)
    times = [start + timedelta(minutes=30 * i) for i in range(12 * 24 + 1)]
    mask = purge_mask(times, [boundary])
    assert sum(mask) == 97
    assert len(mask) == len(times)


# ---------------------------------------------------------------- gap sensitivity


def test_gap_sensitivity_strictly_longer_only():
    event_times = [dt("2030-02-01T00:00:00"), dt("2030-02-01T12:00:00")]  # 12h gap
    # Window span [04:00, 06:30) intersects the gap.
    assert intersects_long_gap(event_times, dt("2030-02-01T06:00:00"), 6) is True
    assert intersects_long_gap(event_times, dt("2030-02-01T06:00:00"), 12) is False
    assert intersects_long_gap(event_times, dt("2030-02-01T06:00:00"), 24) is False


def test_gap_sensitivity_no_intersection_or_short_gap():
    # Short 1h gap, window far away: no exclusion.
    event_times = [dt("2030-02-01T00:00:00"), dt("2030-02-01T01:00:00")]
    assert intersects_long_gap(event_times, dt("2030-02-01T12:00:00"), 6) is False
    # Long gap entirely outside the window span: no exclusion.
    event_times = [dt("2030-02-01T00:00:00"), dt("2030-02-01T13:00:00")]  # 13h gap
    assert intersects_long_gap(event_times, dt("2030-02-01T20:00:00"), 6) is False
    # ... but a window whose history reaches into that gap is excluded.
    assert intersects_long_gap(event_times, dt("2030-02-01T13:30:00"), 6) is True


# ---------------------------------------------------------------- tie rules


def test_choose_best_lowest_loss_wins():
    options = [
        ScoredOption("forest", 0.20, 4, 0),
        ScoredOption("constant", 0.22, 0, 0),
    ]
    assert choose_best(options).name == "forest"


def test_choose_best_tie_prefers_simpler_then_config_order():
    tied = [
        ScoredOption("full-logit-C10", 0.20, 3, 2),
        ScoredOption("constant", 0.20, 0, 0),
        ScoredOption("hour-of-week", 0.20, 1, 0),
    ]
    assert choose_best(tied).name == "constant"
    same_complexity = [
        ScoredOption("full-logit-C1", 0.20, 3, 1),
        ScoredOption("full-logit-C0.1", 0.20, 3, 0),
    ]
    assert choose_best(same_complexity).name == "full-logit-C0.1"


def test_choose_best_empty_rejected():
    with pytest.raises(ValueError):
        choose_best([])


def test_retention_threshold_weighted_quantile():
    uncertainties = [0.1, 0.2, 0.2, 0.3]
    weights = [1.0, 1.0, 1.0, 1.0]
    assert retention_threshold(uncertainties, weights, 0.5) == 0.2
    assert retention_threshold(uncertainties, weights, 0.25) == 0.1
    assert retention_threshold(uncertainties, weights, 0.75) == 0.2
    # Home-equal weights: a heavy home shifts the quantile.
    assert retention_threshold([0.1, 0.5], [3.0, 1.0], 0.5) == 0.1
    assert retention_threshold([0.1, 0.5], [1.0, 3.0], 0.5) == 0.5


def test_retention_threshold_all_retain_bypasses():
    assert retention_threshold([0.1, 0.2], [1.0, 1.0], 1.0) == float("inf")
    assert retention_threshold([0.1, 0.2], [1.0, 1.0], 0.0) == float("-inf")


def test_retention_threshold_bad_inputs_rejected():
    with pytest.raises(ValueError):
        retention_threshold([0.1], [1.0, 1.0], 0.5)
    with pytest.raises(ValueError):
        retention_threshold([], [], 0.5)


# ---------------------------------------------------------------- sensor map + frozen config


def test_sensor_map_matches_contracts():
    with (ROOT / "configs" / "sensor-map.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows, "sensor-map.csv must not be empty"
    accepted = [r for r in rows if r["disposition"] == "accepted"]
    quarantined = [r for r in rows if r["disposition"] == "quarantined"]
    assert len(accepted) == 4  # motion ON/OFF, door OPEN/CLOSE
    assert quarantined, "sensor map must carry synthetic unmapped examples"
    for r in accepted:
        assert is_positive(r["message"]) == (r["is_positive"] == "true"), r
        assert r["counts_for_history"] == "true", r
    for r in quarantined:
        assert "synthetic" in r["mapped_meaning"].lower(), r
        assert r["counts_for_history"] == "false", r


def test_study_config_frozen_values():
    data = json.loads((ROOT / "configs" / "study.json").read_text(encoding="utf-8"))
    fixed = data["proposal_fixed"]
    assert data["protocol"] == "KindWindow proposed freeze v1"
    assert fixed["half_hour_minutes"] == 30
    assert fixed["history_minutes"] == [5, 30, 120]
    assert fixed["warmup_minutes"] == 120
    assert fixed["split_proportions"] == [0.6, 0.1, 0.1, 0.2]
    assert fixed["boundary_purge_hours_each_side"] == 24
    assert fixed["gap_sensitivity_hours"] == [6, 12, 24]
    assert fixed["max_homes"] == 20
