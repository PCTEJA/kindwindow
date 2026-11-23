"""Frozen contracts for KindWindow (Step 02).

Pure functions only: no I/O, no file or network access, no randomness,
no timezone conversion. All times are naive local wall-clock datetimes;
see ``docs/data-dictionary.md`` for the frozen rules these functions
implement. Anything not decided here is an ambiguity to record in the
data dictionary, not something to guess at call time.

Conventions implemented here (frozen Step 02):

* Half-hour origins: ``t`` is a wall-clock boundary (``:00`` / ``:30``),
  seconds and microseconds zero.
* Target interval is half-open ``[t, t + 30m)``: an event exactly at
  ``t`` belongs to the target; an event exactly at ``t + 30m`` belongs
  to the next target.
* Feature history is half-open ``[t - w, t)`` for ``w`` in (5, 30, 120)
  minutes. History counts *all* accepted physical messages, including
  motion OFF; labels use only mapped positive-state messages.
* Positive messages: motion ``ON``, door ``OPEN``, door ``CLOSE``.
  Motion ``OFF`` is a valid mapped deactivation with positive flag
  false. Any other message is unmapped and raises ``ValueError`` here;
  upstream code must quarantine such rows, never fabricate an event.
* Recency is seconds since the most recent earlier accepted event
  strictly before ``t``; ``None`` when there is none (missing plus
  indicator upstream, never an invented zero interval).
* Splits are per home, chronological by eligible-window count, with
  cut indices ``floor(0.60n)``, ``floor(0.70n)``, ``floor(0.80n)``.
* Purge: origins within ``<= 24h`` on either side of a split boundary
  are excluded, and any window whose label interval crosses a boundary
  is excluded. (With a 24 h purge the crossing rule is subsumed by the
  purge, because a 30-minute target cannot cross a boundary from
  farther than 24 h away; it is implemented separately anyway so the
  rule survives any future purge change.)
* Gap sensitivity: a window is excluded when its full span
  ``[t - 120m, t + 30m)`` intersects an event-free gap *strictly*
  longer than the threshold (6 / 12 / 24 h, run separately).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

TARGET_MINUTES = 30
HISTORY_MINUTES = (5, 30, 120)
WARMUP_MINUTES = 120
SPLIT_FRACTIONS = (0.60, 0.70, 0.80)
PURGE_HOURS = 24
GAP_SENSITIVITY_HOURS = (6, 12, 24)

# Message -> positive flag, for the mapped (type, message) pairs in
# configs/sensor-map.csv. Lookup is by message alone here because the
# frozen mapping assigns each message to exactly one meaning; the full
# (sensor_type, message) validation lives in the sensor map, and a
# mismatched pair (e.g. door + ON) is unmapped upstream.
_POSITIVE_BY_MESSAGE: dict[str, bool] = {
    "ON": True,
    "OPEN": True,
    "CLOSE": True,
    "OFF": False,
}


@dataclass(frozen=True)
class Event:
    """One accepted (validated, mapped) sensor event.

    ``time`` is the local wall-clock timestamp with seconds as recorded
    (source precision preserved upstream). ``message`` is the raw
    source message (e.g. ``"ON"``). ``sensor_type`` is ``"motion"`` or
    ``"door"`` for validated events; empty string means the caller did
    not supply a type, in which case door features exclude the event.
    """

    time: datetime
    message: str
    sensor_type: str = ""


@dataclass(frozen=True)
class HistoryCounts:
    """History features for one forecast origin ``t``.

    ``counts[w]`` is the number of accepted events in ``[t - w, t)``.
    ``door_counts[w]`` is the same count restricted to door-type
    events (both OPEN and CLOSE). ``recency_seconds`` is seconds from
    the most recent accepted event strictly before ``t`` to ``t``,
    or ``None`` when no earlier accepted event exists.
    """

    counts: dict[int, int] = field(default_factory=dict)
    door_counts: dict[int, int] = field(default_factory=dict)
    recency_seconds: int | None = None


@dataclass(frozen=True)
class ScoredOption:
    """One candidate for model selection, for deterministic tie-breaking.

    ``loss`` is mean of per-home Brier on selection (lower is better).
    ``complexity`` is a smaller-is-simpler rank (e.g. 0 = per-home
    constant, 1 = hour-of-week, 2 = recency-only logistic, 3 = full
    logistic, 4 = random forest; within one family, fewer/smaller
    hyperparameters rank simpler). ``order`` is the candidate's index
    in config order (0-based).
    """

    name: str
    loss: float
    complexity: int
    order: int


def half_hour_floor(dt: datetime) -> datetime:
    """Floor a local wall-clock datetime to the :00 / :30 boundary.

    Seconds and microseconds are zeroed; the date, hour and tzinfo
    are carried through unchanged. No timezone conversion is done:
    the input is already local wall-clock time.
    """
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def _normalize_message(message: str) -> str:
    return message.strip().upper()


def is_positive(message: str) -> bool:
    """Positive-label flag for a mapped message.

    Motion ``ON`` and door ``OPEN`` / ``CLOSE`` are positive;
    motion ``OFF`` is a valid mapped deactivation and is not
    positive. Matching is case-insensitive after stripping
    whitespace. Raises ``ValueError`` for any unmapped message
    (including empty): unmapped messages are quarantined upstream,
    never silently treated as negative.
    """
    key = _normalize_message(message)
    try:
        return _POSITIVE_BY_MESSAGE[key]
    except KeyError:
        raise ValueError(f"unmapped sensor message: {message!r}") from None


def window_label(
    events: Sequence[Event],
    t: datetime | None = None,
    target_minutes: int = TARGET_MINUTES,
) -> int:
    """Binary label for the target interval starting at ``t``.

    Returns 1 if any mapped positive event falls in ``[t, t +
    target_minutes)``, else 0. An event exactly at ``t`` counts; an
    event exactly at ``t + target_minutes`` does not (it belongs to
    the next target). If ``t`` is ``None``, ``events`` are taken as
    already restricted to the target interval and the label is 1 if
    any of them is positive. Motion OFF alone in the target yields 0.
    Unmapped messages raise ``ValueError`` via :func:`is_positive`.
    """
    if t is None:
        window = list(events)
    else:
        end = t + timedelta(minutes=target_minutes)
        window = [e for e in events if t <= e.time < end]
    return 1 if any(is_positive(e.message) for e in window) else 0


def history_counts(
    events: Sequence[Event],
    t: datetime,
    windows: Sequence[int] = HISTORY_MINUTES,
) -> HistoryCounts:
    """History features for forecast origin ``t``.

    For each ``w`` in ``windows`` (minutes), counts accepted events
    in the half-open interval ``[t - w, t)``; an event exactly at
    ``t`` is target, not history, and an event exactly at ``t - w``
    is included. ``door_counts[w]`` restricts to events whose
    ``sensor_type`` normalizes to ``"door"`` (both OPEN and CLOSE
    count). ``recency_seconds`` is the whole-second gap from the
    most recent accepted event strictly before ``t`` to ``t``, or
    ``None`` when no earlier accepted event exists.
    """
    counts: dict[int, int] = {}
    door_counts: dict[int, int] = {}
    for w in windows:
        start = t - timedelta(minutes=w)
        in_window = [e for e in events if start <= e.time < t]
        counts[w] = len(in_window)
        door_counts[w] = sum(1 for e in in_window if e.sensor_type.strip().lower() == "door")
    earlier = [e.time for e in events if e.time < t]
    recency = int((t - max(earlier)).total_seconds()) if earlier else None
    return HistoryCounts(counts=counts, door_counts=door_counts, recency_seconds=recency)


def split_indices(n: int) -> tuple[int, int, int]:
    """Per-home chronological split cuts for ``n`` eligible windows.

    Returns ``(floor(0.60n), floor(0.70n), floor(0.80n))``: training
    is ``[0, c1)``, selection ``[c1, c2)``, calibration ``[c2, c3)``,
    test ``[c3, n)``. ``n`` must be non-negative.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    import math

    return tuple(math.floor(f * n) for f in SPLIT_FRACTIONS)  # type: ignore[return-value]


def purge_mask(
    times: Sequence[datetime],
    boundaries: Sequence[datetime],
    target_minutes: int = TARGET_MINUTES,
    purge_hours: int = PURGE_HOURS,
) -> list[bool]:
    """Exclusion mask for forecast origins around split boundaries.

    Returns one bool per origin in ``times``: ``True`` means exclude
    (purge) that window. A window is purged when its origin is within
    ``<= purge_hours`` on either side of any boundary, or when its
    label interval ``[t, t + target_minutes)`` crosses a boundary
    (``t < b < t + target_minutes``; a boundary exactly at the end
    of the target does not cross it). With the frozen 24 h purge the
    crossing rule is subsumed, but it is applied independently so a
    future purge change cannot silently re-admit crossing windows.
    """
    purge = timedelta(hours=purge_hours)
    target = timedelta(minutes=target_minutes)
    mask: list[bool] = []
    for t in times:
        excluded = False
        for b in boundaries:
            if abs(t - b) <= purge:
                excluded = True
                break
            if t < b < t + target:
                excluded = True
                break
        mask.append(excluded)
    return mask


def intersects_long_gap(
    event_times: Sequence[datetime],
    t: datetime,
    threshold_hours: float,
    history_minutes: int = WARMUP_MINUTES,
    target_minutes: int = TARGET_MINUTES,
) -> bool:
    """Gap-sensitivity exclusion for one forecast origin ``t``.

    Returns ``True`` when the window's full span ``[t - history, t +
    target)`` intersects an event-free gap between consecutive
    accepted events that is *strictly* longer than ``threshold_hours``
    (a gap of exactly the threshold does not exclude). Gaps before
    the first or after the last event are not event-free gaps between
    events and do not exclude on their own; the window must still
    satisfy warmup/span eligibility upstream. Leading/trailing
    open-ended silence is handled by warmup and span rules, not here.
    """
    span_start = t - timedelta(minutes=history_minutes)
    span_end = t + timedelta(minutes=target_minutes)
    threshold = timedelta(hours=threshold_hours)
    ordered = sorted(event_times)
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt - prev > threshold and prev < span_end and nxt > span_start:
            return True
    return False


def choose_best(options: Sequence[ScoredOption]) -> ScoredOption:
    """Deterministic model-selection winner under the frozen tie rule.

    Lowest selection loss wins; ties prefer the simpler model (lower
    ``complexity``), then earlier config order (lower ``order``).
    Raises ``ValueError`` when ``options`` is empty.
    """
    if not options:
        raise ValueError("choose_best requires at least one option")
    return min(options, key=lambda o: (o.loss, o.complexity, o.order))


def retention_threshold(
    uncertainties: Sequence[float],
    weights: Sequence[float],
    target_fraction: float,
) -> float:
    """Equal-home-weighted empirical quantile cutoff for abstention.

    ``uncertainties`` are ``p_raw * (1 - p_raw)`` values on the clean
    selection cohort (low values retained); ``weights`` are the
    per-window home-equal weights (``N / (H * n_home)`` for its home).
    Returns the smallest uncertainty value whose cumulative weight
    reaches ``target_fraction`` of the total weight, i.e. the frozen
    weighted quantile. At ``target_fraction >= 1`` every window is
    retained (returns ``inf``: thresholding is bypassed explicitly).
    At ``target_fraction <= 0`` nothing is retained (returns ``-inf``).

    Cutoff-tie protocol (frozen): all windows with uncertainty
    strictly below the cutoff are retained; windows exactly at the
    cutoff are ordered by a stable hash of their (home, origin) key
    and retained in that order only until the nominal retained weight
    is reached; the fraction of cutoff ties retained is recorded with
    the frozen threshold. Callers must apply the *same* numeric gate
    to raw candidate probabilities in every test condition.
    """
    if len(uncertainties) != len(weights):
        raise ValueError("uncertainties and weights must have equal length")
    if not uncertainties:
        raise ValueError("retention_threshold requires at least one value")
    if target_fraction >= 1:
        return float("inf")
    if target_fraction <= 0:
        return float("-inf")
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    ordered = sorted(zip(uncertainties, weights), key=lambda pair: pair[0])
    cumulative = 0.0
    needed = target_fraction * total
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= needed:
            return float(value)
    return float(ordered[-1][0])
