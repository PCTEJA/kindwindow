"""Step 08: real CASAS ingest tests (synthetic lines / tiny temp zips only).

No real rows are used here; the real-archive checks live in the run
manifest. Covers the frozen mapping: type from the message (never the
room name), strip-then-exact-match, numeric / button / bare-OF
quarantine (never guessing OF=OFF), comma vs space formats, fractional
seconds, and ledger reconciliation.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from kindwindow.real_ingest import (
    HomeLedger,
    ingest_home,
    parse_casas_line,
    survey_vocabulary,
)


def epoch_ns(year, month, day, hour, minute, second, frac_ns=0):
    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000 + frac_ns


# ---------------------------------------------------------------------------
# Line parsing: formats, mapping, fractional seconds
# ---------------------------------------------------------------------------


def test_comma_format_aruba_fractional_seconds():
    p = parse_casas_line(b"2010-11-04,08:00:00.500,Bedroom,ON\n", 1)
    assert p.kind == "accepted"
    assert p.event_type == "motion" and p.is_positive is True
    assert p.sensor == "Bedroom" and p.message == "ON"
    assert p.epoch_ns == epoch_ns(2010, 11, 4, 8, 0, 0, 500_000_000)


def test_space_format_hh_fractional_seconds():
    p = parse_casas_line(b"2011-06-15 08:00:00.123456789 Kitchen OFF\n", 2)
    assert p.kind == "accepted"
    assert p.event_type == "motion" and p.is_positive is False
    assert p.epoch_ns == epoch_ns(2011, 6, 15, 8, 0, 0, 123_456_789)


def test_door_messages_come_from_message_not_room():
    # "OutsideDoor" is a room token; OPEN/CLOSE still map to door.
    p = parse_casas_line(b"2011-06-15 09:00:00 OutsideDoor OPEN\n", 1)
    assert p.kind == "accepted"
    assert p.event_type == "door" and p.is_positive is True
    p = parse_casas_line(b"2011-06-15,09:00:00,FrontDoor,CLOSE\n", 1)
    assert p.kind == "accepted"
    assert p.event_type == "door" and p.is_positive is True
    # A motion room token with OFF is motion, not positive.
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom OFF\n", 1)
    assert p.kind == "accepted"
    assert p.event_type == "motion" and p.is_positive is False


def test_tab_terminated_tokens_accepted_after_strip():
    for raw, message in ((b"ON\t", "ON"), (b"OFF\t", "OFF"),
                         (b"OPEN\t", "OPEN"), (b"CLOSE\t", "CLOSE")):
        p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom " + raw + b"\n", 1)
        assert p.kind == "accepted", raw
        assert p.message == message
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom  ON  \n", 1)
    assert p.kind == "accepted" and p.message == "ON"


def test_numeric_messages_quarantined_not_positive():
    for raw in (b"0", b"100", b"14.5", b"34.5", b"-3"):
        p = parse_casas_line(b"2011-06-15 09:00:00 Kitchen " + raw + b"\n", 1)
        assert p.kind == "quarantined", raw
        assert p.category == "numeric"
        assert p.epoch_ns == epoch_ns(2011, 6, 15, 9, 0, 0)


def test_button_tokens_quarantined():
    for raw in (b"DEPRESS", b"RELEASE", b"TAP", b"TAP_COUNT_00",
                b"TAP_COUNT_09", b"HOLD_DEPRESS", b"HOLD_RELEASE"):
        p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom " + raw + b"\n", 1)
        assert p.kind == "quarantined", raw
        assert p.category == "button"


def test_bare_of_quarantined_never_guessed_as_off():
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom OF\n", 1)
    assert p.kind == "quarantined"
    assert p.category == "bare_OF"
    # OFF itself is still accepted as motion deactivation.
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom OFF\n", 1)
    assert p.kind == "accepted" and p.is_positive is False


def test_lowercase_and_unknown_messages_quarantined():
    # Matching is exact after strip: lowercase is not ON.
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom on\n", 1)
    assert p.kind == "quarantined" and p.category == "other"
    p = parse_casas_line(b"2011-06-15 09:00:00 Bedroom AJAR\n", 1)
    assert p.kind == "quarantined" and p.category == "other"


def test_malformed_and_blank_lines():
    assert parse_casas_line(b"\n", 1).kind == "blank"
    assert parse_casas_line(b"   \n", 1).kind == "blank"
    # Invalid calendar date / clock time.
    assert parse_casas_line(b"2011-13-01 09:00:00 Bedroom ON\n", 1).kind == "malformed"
    assert parse_casas_line(b"2011-06-15 25:00:00 Bedroom ON\n", 1).kind == "malformed"
    assert parse_casas_line(b"2011-02-30 09:00:00 Bedroom ON\n", 1).kind == "malformed"
    # Wrong field count, empty sensor, empty message.
    assert parse_casas_line(b"2011-06-15 09:00:00 Bedroom\n", 1).kind == "malformed"
    assert parse_casas_line(b"2011-06-15 09:00:00  ON\n", 1).kind == "malformed"
    assert parse_casas_line(b"2011-06-15,09:00:00,,ON\n", 1).kind == "malformed"
    assert parse_casas_line(b"2011-06-15 09:00:00 Bedroom \n", 1).kind == "malformed"
    # Bad date separator.
    assert parse_casas_line(b"2011/06/15 09:00:00 Bedroom ON\n", 1).kind == "malformed"


def test_malformed_row_keeps_reliable_time_or_day():
    p = parse_casas_line(b"2011-06-15,09:00:00,,ON\n", 1)
    assert p.kind == "malformed"
    assert p.epoch_ns == epoch_ns(2011, 6, 15, 9, 0, 0)
    p = parse_casas_line(b"2011-06-15 25:00:00 Bedroom ON\n", 1)
    assert p.kind == "malformed"
    assert p.epoch_ns is None and str(p.day) == "2011-06-15"


# ---------------------------------------------------------------------------
# Ledger + survey over a tiny synthetic zip
# ---------------------------------------------------------------------------


def _make_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "data.zip"
    hh = (
        b"2011-06-15 08:00:00 Bedroom ON\n"
        b"2011-06-15 08:01:00 Bedroom OFF\n"
        b"2011-06-15 08:02:00 Kitchen 14.5\n"
        b"2011-06-15 08:03:00 Bedroom OF\n"
        b"2011-06-15 08:04:00 OutsideDoor OPEN\n"
        b"2011-06-15 25:00:00 Bedroom ON\n"
        b"\n"
    )
    aruba = b"2010-11-04,08:00:00,Bedroom,ON\n2010-11-04,08:01:00,Bedroom,OFF\n"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("data/hh999.csv", hh)
        zf.writestr("data/aruba.csv", aruba)
    return archive


def test_survey_vocabulary_tiny_zip(tmp_path):
    archive = _make_zip(tmp_path)
    survey = survey_vocabulary(archive, ["hh999", "aruba"])
    hh = survey.ledgers["hh999"]
    assert hh.total == 7
    assert hh.accepted == 3 and hh.quarantined == 2
    assert hh.malformed == 1 and hh.ignored == 1
    assert hh.reconciles
    assert hh.quarantine_by_category["numeric"] == 1
    assert hh.quarantine_by_category["bare_OF"] == 1
    assert survey.per_home["hh999"][("Bedroom", "ON")] == 1
    assert survey.per_home["hh999"][("Kitchen", "14.5")] == 1
    assert survey.per_home["aruba"][("Bedroom", "OFF")] == 1
    assert survey.ledgers["aruba"].total == 2
    assert survey.ledgers["aruba"].accepted == 2


def test_ingest_home_writes_normalized_parquet(tmp_path):
    archive = _make_zip(tmp_path)
    out = tmp_path / "normalized"
    result = ingest_home(archive, "hh999", out)
    assert result.ledger.total == 7
    assert result.ledger.accepted == 3
    assert result.ledger.reconciles
    assert len(result.epoch_ns) == 3
    assert result.epoch_ns[0] == epoch_ns(2011, 6, 15, 8, 0, 0)
    assert list(result.is_door) == [False, False, True]
    assert list(result.is_positive) == [True, False, True]
    assert len(result.malformed_times_ns) == 0  # bad clock: time unreliable
    assert len(result.malformed_days) == 1
    assert len(result.quarantined_times_ns) == 2
    assert len(result.normalized_files) == 1
    df = pd.read_parquet(result.normalized_files[0])
    assert list(df.columns) == [
        "home", "member", "row", "time", "sensor", "type",
        "message", "is_positive", "release", "checksum",
    ]
    assert len(df) == 3
    assert list(df["row"]) == [1, 2, 5]
    assert list(df["message"]) == ["ON", "OFF", "OPEN"]
    assert list(df["type"]) == ["motion", "motion", "door"]
    assert df["time"].dtype == "datetime64[ns]"


def test_ledger_dataclass_reconciles():
    ledger = HomeLedger(home="x", total=10, accepted=7, quarantined=2,
                        malformed=1, ignored=0)
    assert ledger.reconciles
    bad = HomeLedger(home="x", total=10, accepted=7, quarantined=2)
    assert not bad.reconciles
