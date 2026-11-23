"""Real CASAS ingest for KindWindow (Step 08).

Streams the private CASAS v1 ``data.zip`` member-by-member (``ZipFile.open``,
bounded, original physical line number preserved) and classifies every
physical line. Local work only; nothing here commits, pushes, or touches
``data/heldout/``.

Source facts (verified by the coordinator by streaming; see
``docs/cohort-audit.md`` SS5-6):

* Sensor tokens are ROOM names (``Bedroom``, ``Kitchen``, ``OutsideDoor``,
  ...), not device codes. The event type comes from the message, never
  from the room name: ``ON``/``OFF`` -> motion, ``OPEN``/``CLOSE`` -> door.
* Two serializations, never mixed within a home, no header lines:
  comma ``YYYY-MM-DD,HH:MM:SS[.frac],Sensor,Message`` (``aruba``, ``ihs*``,
  ``rw*``, ``tm*``) or space ``YYYY-MM-DD HH:MM:SS[.frac] Sensor Message``
  (``hh*``). Fractional seconds are preserved (epoch nanoseconds).
* Message classification: strip whitespace first (this also strips the
  tab-terminated ``ON\\t`` / ``OFF\\t`` / ... tokens), then accept exactly
  ``ON`` / ``OFF`` / ``OPEN`` / ``CLOSE``. Anything else is quarantined,
  never guessed: numeric payloads (``0``, ``100``, ``14.5``), button
  tokens (``DEPRESS``, ``TAP``, ``HOLD_*``, ...), bare ``OF`` (never
  guessed to be ``OFF``), and anything else unmapped.

Quarantine / window-exclusion decision (Step 08, recorded here because it
is a judgment call against ``docs/data-dictionary.md`` SS5-6):

* A row whose *structure* is broken (unparseable timestamp, wrong field
  count, empty sensor/message) is quarantined as malformed; windows
  intersecting its reliable time (or its day, when only the date parses)
  are excluded, and an unlocatable malformed row excludes its segment.
  The Step 03 audit found zero malformed rows in the 20-home cohort, so
  this path is expected to be empty.
* A row that is structurally sound but carries an unmapped message is a
  *known non-activity reading* (a temperature/humidity-style numeric
  payload, a button event, ...), not ambiguous or corrupt data. It is
  counted as quarantined in the ledger (it is not an accepted event, it
  contributes nothing to history counts, recency, or labels), but it does
  NOT exclude windows: its time is reliable and it says nothing about
  the validity of the motion/door stream. Applying the dictionary's
  "windows intersecting quarantined events are excluded" rule literally
  to these rows would exclude nearly every window in any home whose
  numeric sensor reports regularly, which would destroy the cohort on a
  reading the dictionary never contemplated (it was written for the
  JSONL pipeline, where quarantined rows are corrupt/ambiguous). The
  counterfactual -- the per-home fraction of windows that *would* be
  excluded under the strict reading -- is computed and recorded in the
  run manifest so the decision is auditable and reversible.
"""

from __future__ import annotations

import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Message (after strip) -> (sensor type, positive flag). Type comes from
# the message alone; the sensor token is a room name, never a device code.
_VALID: dict[bytes, tuple[str, bool]] = {
    b"ON": ("motion", True),
    b"OFF": ("motion", False),
    b"OPEN": ("door", True),
    b"CLOSE": ("door", True),
}

_BUTTON_PREFIXES = (b"DEPRESS", b"RELEASE", b"TAP", b"HOLD")

# Days in month for validation (index 1..12); February handled separately.
_DAYS_IN_MONTH = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 (Howard Hinnant's algorithm)."""
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = (month + 9) % 12
    doy = (153 * mp + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def parse_timestamp_ns(date_b: bytes, time_b: bytes) -> int | None:
    """Epoch nanoseconds for ``YYYY-MM-DD`` + ``HH:MM:SS[.frac]``.

    Returns ``None`` when the date/time is structurally invalid (bad
    separators, non-digits, out-of-range fields, non-digit fraction).
    Fractional seconds are preserved to nanosecond precision.
    """
    if len(date_b) != 10 or date_b[4:5] != b"-" or date_b[7:8] != b"-":
        return None
    if len(time_b) < 8 or time_b[2:3] != b":" or time_b[5:6] != b":":
        return None
    if not (
        date_b[0:4].isdigit()
        and date_b[5:7].isdigit()
        and date_b[8:10].isdigit()
        and time_b[0:2].isdigit()
        and time_b[3:5].isdigit()
        and time_b[6:8].isdigit()
    ):
        return None
    year = int(date_b[0:4])
    month = int(date_b[5:7])
    day = int(date_b[8:10])
    hour = int(time_b[0:2])
    minute = int(time_b[3:5])
    second = int(time_b[6:8])
    if not 1 <= month <= 12 or hour >= 24 or minute >= 60 or second >= 60:
        return None
    dim = _DAYS_IN_MONTH[month] + (1 if month == 2 and _is_leap(year) else 0)
    if not 1 <= day <= dim:
        return None
    frac_ns = 0
    if len(time_b) > 8:
        if time_b[8:9] != b".":
            return None
        frac = time_b[9:]
        if not frac or not frac.isdigit() or len(frac) > 9:
            return None
        frac_ns = int(frac) * (10 ** (9 - len(frac)))
    days = days_from_civil(year, month, day)
    seconds = days * 86400 + hour * 3600 + minute * 60 + second
    return seconds * 1_000_000_000 + frac_ns


def quarantine_category(message: bytes) -> str:
    """Bucket for an unmapped (stripped) message token."""
    if message == b"OF":
        return "bare_OF"
    if message[:1].isdigit() or message[:1] in (b"-", b"+", b"."):
        try:
            float(message)
            return "numeric"
        except ValueError:
            pass
    if message.startswith(_BUTTON_PREFIXES):
        return "button"
    return "other"


@dataclass
class ParsedLine:
    """One classified physical line. ``kind`` is one of blank / malformed /
    accepted / quarantined."""

    kind: str
    line_no: int
    epoch_ns: int | None = None
    sensor: str = ""
    message: str = ""
    event_type: str = ""
    is_positive: bool = False
    category: str = ""  # quarantine bucket for kind == "quarantined"
    reason: str = ""  # detail for kind == "malformed"
    day: date | None = None  # best-effort date for malformed rows


def _best_effort_day(date_b: bytes) -> date | None:
    """Parse ``YYYY-MM-DD`` leniently (date only, for malformed rows)."""
    if len(date_b) != 10 or date_b[4:5] != b"-" or date_b[7:8] != b"-":
        return None
    if not (date_b[0:4].isdigit() and date_b[5:7].isdigit() and date_b[8:10].isdigit()):
        return None
    try:
        return date(int(date_b[0:4]), int(date_b[5:7]), int(date_b[8:10]))
    except ValueError:
        return None


def parse_casas_line(line: bytes, line_no: int) -> ParsedLine:
    """Classify one raw physical line (bytes, newline included or not)."""
    stripped = line.strip()
    if not stripped:
        return ParsedLine(kind="blank", line_no=line_no)
    # Format is decided by the separator after the 10-char date.
    sep = stripped[10:11]
    if sep == b",":
        parts = stripped.split(b",", 3)
    elif sep == b" ":
        parts = stripped.split(None, 3)
    else:
        return ParsedLine(
            kind="malformed",
            line_no=line_no,
            reason="bad date separator",
            day=_best_effort_day(stripped[0:10]),
        )
    if len(parts) != 4:
        return ParsedLine(
            kind="malformed",
            line_no=line_no,
            reason="wrong field count",
            day=_best_effort_day(parts[0]) if parts else None,
        )
    date_b, time_b, sensor_b, message_b = parts
    epoch_ns = parse_timestamp_ns(date_b, time_b)
    if epoch_ns is None:
        return ParsedLine(
            kind="malformed",
            line_no=line_no,
            reason="unparseable timestamp",
            day=_best_effort_day(date_b),
        )
    if not sensor_b:
        return ParsedLine(
            kind="malformed",
            line_no=line_no,
            reason="empty sensor",
            epoch_ns=epoch_ns,
            day=_best_effort_day(date_b),
        )
    message = message_b.strip()
    if not message:
        return ParsedLine(
            kind="malformed",
            line_no=line_no,
            reason="empty message",
            epoch_ns=epoch_ns,
            day=_best_effort_day(date_b),
        )
    sensor = sensor_b.decode("utf-8", "replace")
    message_s = message.decode("utf-8", "replace")
    mapped = _VALID.get(message)
    if mapped is not None:
        event_type, is_positive = mapped
        return ParsedLine(
            kind="accepted",
            line_no=line_no,
            epoch_ns=epoch_ns,
            sensor=sensor,
            message=message_s,
            event_type=event_type,
            is_positive=is_positive,
        )
    return ParsedLine(
        kind="quarantined",
        line_no=line_no,
        epoch_ns=epoch_ns,
        sensor=sensor,
        message=message_s,
        category=quarantine_category(message),
    )


@dataclass
class HomeLedger:
    """Reconciliation ledger for one home (raw CSV; replays n/a).

    total = accepted + quarantined + malformed + ignored; replays are 0
    for raw CSV (distinct rows with the same timestamp/sensor/payload
    are legitimate events, not replays).
    """

    home: str
    total: int = 0
    accepted: int = 0
    quarantined: int = 0
    malformed: int = 0
    ignored: int = 0
    replays: int = 0
    quarantine_by_category: Counter = field(default_factory=Counter)

    @property
    def reconciles(self) -> bool:
        return (
            self.total
            == self.accepted + self.quarantined + self.malformed + self.ignored + self.replays
        )


@dataclass
class VocabSurvey:
    """Aggregates only: (sensor, message) counts per home and overall."""

    per_home: dict[str, Counter] = field(default_factory=dict)
    overall: Counter = field(default_factory=Counter)
    ledgers: dict[str, HomeLedger] = field(default_factory=dict)

    def as_rows(self) -> list[dict]:
        rows: list[dict] = []
        for home in sorted(self.per_home):
            for (sensor, message), count in sorted(self.per_home[home].items()):
                rows.append(
                    {"home": home, "sensor": sensor, "message": message, "count": count}
                )
        return rows


def member_name(home: str) -> str:
    return f"data/{home}.csv"


def survey_vocabulary(
    archive_path: Path, homes: list[str]
) -> VocabSurvey:
    """Stream the cohort members once; tally (sensor, message) counts.

    Aggregates only -- no raw rows are retained. Also produces the
    per-home ledger (accepted / quarantined-by-category / malformed /
    ignored) that the full ingest must later reproduce exactly.
    """
    survey = VocabSurvey()
    with zipfile.ZipFile(archive_path) as zf:
        for home in homes:
            counter: Counter = Counter()
            ledger = HomeLedger(home=home)
            with zf.open(member_name(home)) as fh:
                for line_no, line in enumerate(fh, start=1):
                    ledger.total += 1
                    parsed = parse_casas_line(line, line_no)
                    if parsed.kind == "blank":
                        ledger.ignored += 1
                    elif parsed.kind == "malformed":
                        ledger.malformed += 1
                    elif parsed.kind == "accepted":
                        ledger.accepted += 1
                        counter[(parsed.sensor, parsed.message)] += 1
                    else:
                        ledger.quarantined += 1
                        ledger.quarantine_by_category[parsed.category] += 1
                        counter[(parsed.sensor, parsed.message)] += 1
            assert ledger.reconciles, f"survey ledger does not reconcile for {home}: {ledger}"
            survey.per_home[home] = counter
            survey.overall.update(counter)
            survey.ledgers[home] = ledger
    return survey


# ---------------------------------------------------------------------------
# Full ingest: stream one home, write normalized Parquet, keep arrays
# ---------------------------------------------------------------------------

NORMALIZED_SCHEMA = pa.schema(
    [
        ("home", pa.string()),
        ("member", pa.string()),
        ("row", pa.int64()),
        ("time", pa.timestamp("ns")),
        ("sensor", pa.string()),
        ("type", pa.string()),
        ("message", pa.string()),
        ("is_positive", pa.bool_()),
        ("release", pa.string()),
        ("checksum", pa.string()),
    ]
)

CHUNK_ROWS = 500_000


@dataclass
class IngestHomeResult:
    home: str
    ledger: HomeLedger
    epoch_ns: np.ndarray  # accepted events, file order
    is_door: np.ndarray  # bool, file order
    is_positive: np.ndarray  # bool, file order
    malformed_times_ns: np.ndarray  # malformed rows with a reliable time
    malformed_days: set  # date objects for malformed rows (time unreliable)
    malformed_unlocatable: bool
    quarantined_times_ns: np.ndarray  # unmapped-message rows (counterfactual only)
    normalized_files: list[Path] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def _flush_chunk(
    chunk: dict,
    home: str,
    member: str,
    writers: dict[str, pq.ParquetWriter],
    normalized_dir: Path,
    written: list[Path],
) -> None:
    """Write one accepted-events chunk, split by year_month partition."""
    if not chunk["epoch_ns"]:
        return
    times = pd.to_datetime(np.array(chunk["epoch_ns"], dtype="int64"))
    df = pd.DataFrame(
        {
            "home": home,
            "member": member,
            "row": np.array(chunk["row"], dtype="int64"),
            "time": times,
            "sensor": chunk["sensor"],
            "type": chunk["type"],
            "message": chunk["message"],
            "is_positive": np.array(chunk["is_positive"], dtype=bool),
            "release": "",
            "checksum": "",
        }
    )
    df["year_month"] = times.strftime("%Y-%m")
    for ym, group in df.groupby("year_month", sort=True):
        table = pa.Table.from_pandas(
            group.drop(columns=["year_month"]),
            schema=NORMALIZED_SCHEMA,
            preserve_index=False,
        )
        writer = writers.get(ym)
        if writer is None:
            out = normalized_dir / f"home={home}" / f"year_month={ym}" / "events.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            writer = pq.ParquetWriter(out, NORMALIZED_SCHEMA, compression="snappy")
            writers[ym] = writer
            written.append(out)
        writer.write_table(table)


def ingest_home(
    archive_path: Path,
    home: str,
    normalized_dir: Path,
    *,
    chunk_rows: int = CHUNK_ROWS,
) -> IngestHomeResult:
    """Stream one home from the zip; write normalized Parquet; keep arrays.

    One home at a time; chunk arrays are converted to NumPy and freed as
    the home is processed. Accepted events are accumulated as int64 epoch
    nanoseconds + bool flags only -- no per-event Python dataclasses.
    """
    import time

    t0 = time.time()
    ledger = HomeLedger(home=home)
    member = member_name(home)
    epoch_chunks: list[np.ndarray] = []
    door_chunks: list[np.ndarray] = []
    pos_chunks: list[np.ndarray] = []
    malformed_times: list[int] = []
    malformed_days: set = set()
    malformed_unlocatable = False
    quarantined_times: list[int] = []
    writers: dict[str, pq.ParquetWriter] = {}
    normalized_files: list[Path] = []
    chunk: dict = {
        "epoch_ns": [],
        "row": [],
        "sensor": [],
        "type": [],
        "message": [],
        "is_positive": [],
    }

    def flush() -> None:
        _flush_chunk(chunk, home, member, writers, normalized_dir, normalized_files)
        if chunk["epoch_ns"]:
            epoch_chunks.append(np.array(chunk["epoch_ns"], dtype=np.int64))
            # door flags are derived from type at flush time to keep the
            # hot loop allocation-free; recompute from stored types.
            door_chunks.append(np.array([t == "door" for t in chunk["type"]], dtype=bool))
            pos_chunks.append(np.array(chunk["is_positive"], dtype=bool))
        for key in chunk:
            chunk[key].clear()

    with zipfile.ZipFile(archive_path) as zf:
        with zf.open(member) as fh:
            for line_no, line in enumerate(fh, start=1):
                ledger.total += 1
                parsed = parse_casas_line(line, line_no)
                if parsed.kind == "blank":
                    ledger.ignored += 1
                elif parsed.kind == "malformed":
                    ledger.malformed += 1
                    if parsed.epoch_ns is not None:
                        malformed_times.append(parsed.epoch_ns)
                    elif parsed.day is not None:
                        malformed_days.add(parsed.day)
                    else:
                        malformed_unlocatable = True
                elif parsed.kind == "accepted":
                    ledger.accepted += 1
                    chunk["epoch_ns"].append(parsed.epoch_ns)
                    chunk["row"].append(line_no)
                    chunk["sensor"].append(parsed.sensor)
                    chunk["type"].append(parsed.event_type)
                    chunk["message"].append(parsed.message)
                    chunk["is_positive"].append(parsed.is_positive)
                    if len(chunk["epoch_ns"]) >= chunk_rows:
                        flush()
                else:  # quarantined: known non-activity reading
                    ledger.quarantined += 1
                    ledger.quarantine_by_category[parsed.category] += 1
                    if parsed.epoch_ns is not None:
                        quarantined_times.append(parsed.epoch_ns)
    flush()
    for writer in writers.values():
        writer.close()
    assert ledger.reconciles, f"ingest ledger does not reconcile for {home}: {ledger}"
    epoch_ns = np.concatenate(epoch_chunks) if epoch_chunks else np.empty(0, dtype=np.int64)
    is_door = np.concatenate(door_chunks) if door_chunks else np.empty(0, dtype=bool)
    is_positive = np.concatenate(pos_chunks) if pos_chunks else np.empty(0, dtype=bool)
    return IngestHomeResult(
        home=home,
        ledger=ledger,
        epoch_ns=epoch_ns,
        is_door=is_door,
        is_positive=is_positive,
        malformed_times_ns=np.array(sorted(malformed_times), dtype=np.int64),
        malformed_days=malformed_days,
        malformed_unlocatable=malformed_unlocatable,
        quarantined_times_ns=np.array(sorted(quarantined_times), dtype=np.int64),
        normalized_files=normalized_files,
        elapsed_seconds=time.time() - t0,
    )
