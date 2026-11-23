"""Shared, engine-agnostic KindWindow event logic.

This module is intentionally free of pandas / NumPy / Spark imports: the
pandas engine and the future Spark engine both build on it, so a change here
changes both lanes identically. It owns:

* event parsing and validation (``parse_record``),
* the positive-label rule (``is_positive``),
* half-hour window-key generation (``generate_window_keys``),
* segmentation of one file's events at unresolved clock reversals
  (``assign_segments``), and
* the reconciliation ledger (``Ledger``).

Time model
----------
Event timestamps are *naive local wall-clock* datetimes, exactly as recorded
in the source. No UTC conversion is performed here: a UTC conversion is only
valid with a documented timezone and an unambiguous offset/fold, and the
source fixtures carry none. When a timezone name is supplied to
``parse_record``, wall-clock times that are nonexistent (spring-forward gap)
or ambiguous (fall-back fold) in that zone are quarantined instead of being
guessed at; a record may carry an explicit ``fold`` of 0 or 1 to resolve an
ambiguous time. Without a timezone, wall-clock arithmetic is used throughout
and DST transitions are therefore wall-clock transitions (documented
limitation: absolute elapsed time across a DST boundary can differ from
wall-clock elapsed time by the DST shift).

Rules (from ``04_DATA_AND_PIPELINES.md`` and ``study_config.json``)
-----------------------------------------------------------------
* Window step is 30 minutes; boundaries are half-hour local boundaries with
  seconds aligned to zero.
* A window ``[t, t+30m)`` is scored only when it is complete inside a file's
  observed span and ``t - 120m`` of earlier history is available.
* History is ``[t-w, t)`` for ``w`` in 5/30/120 minutes; ``t`` belongs to
  the target, exactly ``t+30m`` belongs to the next target.
* Label is positive iff any positive message falls in the target:
  motion ON, door OPEN, door CLOSE. Motion OFF is a valid event and is
  *not* positive.
* A replay is the same ``(release, member, checksum, row)`` tuple seen
  again. Two distinct rows with the same timestamp, sensor and payload are
  legitimate events, not replays.
* Ambiguous rows are quarantined, never fabricated into events.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WINDOW_MINUTES = 30
HISTORY_MINUTES = (5, 30, 120)
WARMUP_MINUTES = 120
SCHEMA_EVENTS = "kindwindow.events/v1"
SCHEMA_WINDOWS = "kindwindow.windows/v1"

# Valid (type, message) pairs, after case normalisation.
_VALID_MESSAGES: dict[str, frozenset[str]] = {
    "motion": frozenset({"ON", "OFF"}),
    "door": frozenset({"OPEN", "CLOSE"}),
}

_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def is_positive(event_type: str, message: str) -> bool:
    """Positive-label rule: motion ON, door OPEN, door CLOSE.

    Motion OFF is a valid mapped deactivation with a positive flag of False.
    Unknown pairs are not positive; they never reach this function as events
    because ``parse_record`` quarantines them first.
    """
    return (event_type, message) in {
        ("motion", "ON"),
        ("door", "OPEN"),
        ("door", "CLOSE"),
    }


# --- Spark dense-grid constants (Step 05; same contract as above) ---
# Bucket / window sizes, in seconds.
BUCKET_SECONDS = 300  # 5-minute dense-grid bucket
ORIGIN_SECONDS = 1800  # 30-minute scoring origins
WARMUP_SECONDS = 7200  # 120 minutes of earlier history required
TARGET_SECONDS = 1800  # 30-minute target [t, t+30m)

# History windows, in buckets of BUCKET_SECONDS: 5m=1, 30m=6, 120m=24.
HISTORY_BUCKETS = {"count_5m": 1, "count_30m": 6, "count_120m": 24}
DOOR_BUCKETS = 6  # door_30m over the preceding 6 buckets
TARGET_BUCKETS = 6  # current + next five 5-minute buckets

OUTPUT_COLUMNS = [
    "home",
    "t",
    "count_5m",
    "count_30m",
    "count_120m",
    "door_30m",
    "recency_seconds",
    "y",
]

PARQUET_CODEC = "snappy"
PARTITION_COLUMNS = ["home", "year_month"]


def is_positive_message(event_type: str, message: str) -> bool:
    """Alias of is_positive for the Spark engine (label definition)."""
    return is_positive(event_type, message)


def is_door_event(event_type: str) -> bool:
    """True for door events (both OPEN and CLOSE count for door features)."""
    return event_type == "door"


@dataclass(frozen=True)
class Event:
    """One accepted, validated event (local wall-clock time)."""

    home: str
    member: str
    row: int
    time: datetime  # naive local wall clock
    sensor: str
    type: str  # "motion" | "door"
    message: str  # "ON" | "OFF" | "OPEN" | "CLOSE"
    is_positive: bool
    release: str = ""
    checksum: str = ""
    fold: int | None = None


@dataclass(frozen=True)
class QuarantinedRow:
    """One first-seen row that could not be accepted as an event."""

    home: str | None
    member: str | None
    row: int | None
    reason: str
    raw: str
    # Best-effort location for window exclusion (see module docstring and
    # pandas_engine.compute_windows): a parseable datetime excludes only
    # windows whose target contains it; a parseable date only excludes that
    # date's windows; neither parseable means the day cannot be bounded and
    # the whole affected file/segment is excluded.
    time: datetime | None = None
    day: date | None = None


@dataclass
class Ledger:
    """Reconciliation ledger for one ingest batch.

    Physical input = accepted + quarantined + explicitly ignored (blank
    lines) + replays. Data-row attempts = first-seen accepted + first-seen
    quarantined + replays. ``reconciles`` must hold for every batch.
    """

    total_lines: int = 0
    accepted_first: int = 0
    quarantined_first: int = 0
    replays: int = 0
    ignored: int = 0

    @property
    def reconciles(self) -> bool:
        return (
            self.total_lines
            == self.accepted_first + self.quarantined_first + self.replays + self.ignored
        )


@dataclass
class Segment:
    """A continuous interpretable run of events for one (home, member).

    A new segment starts after a corroborated unresolved clock reversal
    (see :func:`assign_segments`) and at every file boundary. History,
    recency and spans never bridge segments.
    """

    home: str
    member: str
    index: int
    events: list[Event] = field(default_factory=list)

    @property
    def span_start(self) -> datetime:
        return min(e.time for e in self.events)

    @property
    def span_end(self) -> datetime:
        return max(e.time for e in self.events)


def _wall_time_status(dt: datetime, tz: ZoneInfo) -> str:
    """Return "ok", "ambiguous" or "nonexistent" for a naive wall time.

    Nonexistence is tested first: a nonexistent wall time also yields
    different fold offsets, so testing ambiguity first misclassifies it.
    """
    fold0 = dt.replace(tzinfo=tz, fold=0)
    # Round-trip through UTC: a nonexistent wall time does not survive it.
    back = fold0.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if back != dt:
        return "nonexistent"
    fold1 = dt.replace(tzinfo=tz, fold=1)
    if fold0.utcoffset() != fold1.utcoffset():
        return "ambiguous"
    return "ok"


def _extract_location(raw_time: object) -> tuple[datetime | None, date | None]:
    """Best-effort (datetime, date) from a raw ``time`` value, or (None, None)."""
    if not isinstance(raw_time, str):
        return None, None
    m = _TIME_RE.match(raw_time)
    if m:
        try:
            return datetime(*[int(g) for g in m.groups()]), None
        except ValueError:
            pass
    dm = _DATE_RE.match(raw_time)
    if dm:
        try:
            return None, date(*[int(g) for g in dm.groups()])
        except ValueError:
            return None, None
    return None, None


def parse_record(
    record: object,
    *,
    release: str = "",
    member_default: str | None = None,
    row_default: int | None = None,
    timezone_name: str | None = None,
    raw: str = "",
) -> Event | QuarantinedRow:
    """Validate one decoded JSON record.

    Returns an :class:`Event` or a :class:`QuarantinedRow`. Nothing is
    invented: any missing, mistyped, unparseable or unmapped field
    quarantines the row.
    """
    home = member = None
    row = row_default
    if isinstance(record, dict):
        home = record.get("home")
        member = record.get("member") or member_default
        row = record.get("row", row_default)

    def quarantine(reason: str) -> QuarantinedRow:
        t, d = _extract_location(record.get("time") if isinstance(record, dict) else None)
        return QuarantinedRow(
            home=home if isinstance(home, str) else None,
            member=member if isinstance(member, str) else None,
            row=row if isinstance(row, int) and not isinstance(row, bool) else None,
            reason=reason,
            raw=raw,
            time=t,
            day=d,
        )

    if not isinstance(record, dict):
        return quarantine("record is not a JSON object")
    if not isinstance(home, str) or not home:
        return quarantine("missing or non-string home")
    if not isinstance(member, str) or not member:
        return quarantine("missing or non-string member")
    if not isinstance(row, int) or isinstance(row, bool):
        return quarantine("missing or non-integer row")
    sensor = record.get("sensor")
    if not isinstance(sensor, str) or not sensor:
        return quarantine("missing or non-string sensor")
    raw_type = record.get("type")
    raw_message = record.get("message")
    if not isinstance(raw_type, str) or not isinstance(raw_message, str):
        return quarantine("missing or non-string type/message")
    event_type = raw_type.strip().lower()
    message = raw_message.strip().upper()
    if event_type not in _VALID_MESSAGES or message not in _VALID_MESSAGES[event_type]:
        return quarantine(f"unmapped type/message pair: {raw_type!r}/{raw_message!r}")
    raw_time = record.get("time")
    if not isinstance(raw_time, str):
        return quarantine("missing or non-string time")
    m = _TIME_RE.match(raw_time)
    if not m:
        return quarantine(f"unparseable time: {raw_time!r}")
    try:
        dt = datetime(*[int(g) for g in m.groups()])
    except ValueError:
        return quarantine(f"invalid calendar time: {raw_time!r}")

    fold = record.get("fold")
    if fold is not None and fold not in (0, 1) or isinstance(fold, bool):
        return quarantine(f"invalid fold: {fold!r}")
    if timezone_name:
        status = _wall_time_status(dt, ZoneInfo(timezone_name))
        if status == "nonexistent":
            return quarantine(f"nonexistent local time in {timezone_name}: {raw_time!r}")
        if status == "ambiguous" and fold is None:
            return quarantine(f"ambiguous local time in {timezone_name}: {raw_time!r}")

    checksum = record.get("checksum")
    if checksum is not None and not isinstance(checksum, str):
        return quarantine("non-string checksum")
    return Event(
        home=home,
        member=member,
        row=row,
        time=dt,
        sensor=sensor,
        type=event_type,
        message=message,
        is_positive=is_positive(event_type, message),
        release=release,
        checksum=checksum or "",
        fold=fold,
    )


def canonical_hash(record: dict) -> str:
    """Stable hash of a record's content, for replay detection."""
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def ceil_to_half_hour(dt: datetime) -> datetime:
    """Smallest half-hour boundary (seconds aligned to zero) >= ``dt``."""
    base = dt.replace(second=0, microsecond=0)
    if dt.second or dt.microsecond:
        base += timedelta(minutes=1)
    remainder = base.minute % WINDOW_MINUTES
    if remainder:
        base += timedelta(minutes=WINDOW_MINUTES - remainder)
    return base


def generate_window_keys(
    span_start: datetime,
    span_end: datetime,
    *,
    warmup_minutes: int = WARMUP_MINUTES,
    step_minutes: int = WINDOW_MINUTES,
) -> list[datetime]:
    """Half-hour window origins ``t`` for one segment.

    A key is emitted iff the target ``[t, t+30m)`` is complete inside the
    observed span (``t + 30m <= span_end``) and ``warmup_minutes`` of earlier
    history is available (``t - warmup >= span_start``).
    """
    first = ceil_to_half_hour(span_start + timedelta(minutes=warmup_minutes))
    keys: list[datetime] = []
    t = first
    while t + timedelta(minutes=step_minutes) <= span_end:
        keys.append(t)
        t += timedelta(minutes=step_minutes)
    return keys


def assign_segments(events: list[Event]) -> list[Segment]:
    """Split events into continuous segments, in file order.

    Grouped by ``(home, member)`` preserving first-seen order. A new
    segment starts at an *unresolved clock reversal*: an event whose time
    falls below the segment's high-water mark in file order **and** is
    corroborated by a later event in the same file that continues from the
    decreased time (at or after it, still below the old high-water mark).
    A single out-of-order row with no such continuation cannot establish a
    reversal, so it stays in its segment and is placed by the stable sort
    at compute time; two distinct rows at the same timestamp are therefore
    never split apart. Simultaneous events (equal times) never split.
    """
    groups: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        groups.setdefault((event.home, event.member), []).append(event)
    segments: list[Segment] = []
    for (home, member), group in groups.items():
        index = 0
        current: list[Event] = []
        high: datetime | None = None
        for j, event in enumerate(group):
            if high is not None and event.time < high:
                corroborated = any(
                    event.time <= later.time < high for later in group[j + 1 :]
                )
                if corroborated:
                    segments.append(Segment(home, member, index, current))
                    index += 1
                    current = []
                    high = None
            current.append(event)
            high = event.time if high is None else max(high, event.time)
        if current:
            segments.append(Segment(home, member, index, current))
    return segments
