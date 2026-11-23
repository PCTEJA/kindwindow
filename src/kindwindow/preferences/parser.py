"""Conservative preference parser (Step 06).

Parses a short free-text note into structured resident preferences::

    {"days": [1, 2, ...] | None,
     "preferred_range": ["HH:MM", "HH:MM"] | None,
     "quiet_range": ["HH:MM", "HH:MM"] | None}

Rules (see ``rules.md`` for the full written grammar):

* Conservative Unicode normalisation (NFKC, curly quotes/apostrophes and
  dashes folded to ASCII, whitespace collapsed, case folded).
* One day selector (a single day, a comma/``and`` list, a ``Mon-Fri`` style
  range, or a group: weekdays / weekends / every day). Two different day
  selectors, or an ``except`` / ``every other`` style qualifier, leave
  ``days`` empty.
* At most one interval of each kind (preferred / quiet). A range is two
  time tokens joined by ``to`` / ``-`` / ``until`` / ``till`` or
  ``between X and Y``. Times are ``noon`` / ``midnight``, 24-hour
  ``HH:MM``, or 12-hour ``H(:MM) am|pm`` (a bare hour may inherit the
  partner endpoint's am/pm marker). Cross-midnight intervals are kept
  as-is: end earlier than start means overnight, end-exclusive.
* Quiet markers (``quiet``, ``do not / don't ring|call|disturb``,
  ``never ring|call``, ``no ring|calls``) classify an interval as quiet;
  everything else with a range defaults to preferred. Markers are read
  from the clause/segment before the interval (falling back to the same
  clause after it only when the segment before it carries no marker).
* Unclear, conflicting, hedged (``around``/``about``/``approx``) or
  out-of-grammar phrases leave the affected field empty (refusal) --
  the parser never guesses. A bare hour with no am/pm and no colon
  (``9 to 11``), a single stray time, more than one interval of a kind,
  more than two intervals, or identical preferred and quiet intervals
  all refuse the affected range field(s).

``PARSER_VERSION`` is bumped only when the grammar changes.
"""

from __future__ import annotations

import re
import unicodedata

PARSER_VERSION = "1.0.0"

RULES = [
    "Normalize with NFKC; fold curly quotes/apostrophes and all dashes "
    "to ASCII; collapse whitespace; match case-insensitively.",
    "Days are ISO weekdays 1-7 (Mon=1 .. Sun=7), sorted ascending. One day "
    "selector only: single day, comma/and list, Mon-Fri style range, or "
    "group (weekdays=[1-5], weekends=[6,7], every day/daily=[1-7]).",
    "Two different day selectors, or except/every-other/alternate "
    "qualifiers, leave days empty (refusal).",
    "Times: noon=12:00, midnight=00:00, 24h HH:MM, or 12h H(:MM) am/pm. "
    "A bare hour may inherit its partner endpoint's am/pm marker; a bare "
    "hour with no marker anywhere is out of grammar.",
    "Ranges: TIME to/-/until/till TIME, or between TIME and TIME. At most "
    "one preferred and one quiet interval; more intervals, or a second "
    "interval of the same kind, leave that range empty.",
    "Quiet markers: quiet, do not/don't ring|call|disturb, never ring|call, "
    "no ring|calls. Other intervals default to preferred. Markers are read "
    "from the segment before the interval within its clause.",
    "Cross-midnight intervals are kept as [start, end] with end < start "
    "(overnight, end-exclusive). start == end is refused.",
    "Hedges (around/about/approx/roughly/maybe), stray single times, and "
    "identical preferred==quiet intervals refuse the affected range field. "
    "Unclear/conflicting/out-of-grammar text leaves the field empty; the "
    "parser never guesses.",
    "Null means unspecified. Output is exactly {days, preferred_range, "
    "quiet_range} with ranges as [HH:MM start, HH:MM end].",
]

__all__ = ["PARSER_VERSION", "RULES", "parse_note", "normalize_text"]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_DASHES = "‐‑‒–—―−"
_QUOTES = "“”«»"
_APOSTROPHES = "‘’‚‛"


def normalize_text(text: str) -> str:
    """Conservative Unicode normalisation for matching.

    NFKC, fold curly quotes/apostrophes and dashes to ASCII, collapse
    whitespace, strip, lower-case. No stemming, no synonym expansion.
    """
    if not isinstance(text, str):
        raise TypeError("parse_note expects a str")
    out = unicodedata.normalize("NFKC", text)
    for ch in _DASHES:
        out = out.replace(ch, "-")
    for ch in _QUOTES:
        out = out.replace(ch, '"')
    for ch in _APOSTROPHES:
        out = out.replace(ch, "'")
    out = out.replace("\u00a0", " ").replace("\u200b", "")
    out = re.sub(r"\s+", " ", out).strip().lower()
    return out


# ---------------------------------------------------------------------------
# Days
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tues": 2,
    "tue": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thur": 4,
    "thurs": 4,
    "thu": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
    "sunday": 7,
    "sun": 7,
}
# Longest names first so "monday" wins over "mon", "tues" over "tue", etc.
_DAY_ALT = "|".join(sorted(_DAY_NAMES, key=len, reverse=True))
_DAY = rf"(?:{_DAY_ALT})s?\b"
_GROUP = (
    r"(?:weekdays?|weekends?|every\s+day|everyday|daily|all\s+days?"
    r"|each\s+day|every\s+weekday|every\s+weekend)"
)
_DAY_RANGE = rf"{_DAY}\s*(?:-|\bto\b|\bthrough\b|\bthru\b)\s*{_DAY}"
_DAY_LIST = rf"{_DAY}(?:\s*,\s*{_DAY}|\s+and\s+{_DAY}|\s*&\s*{_DAY}|\s*,\s*and\s+{_DAY})*"
_DAY_EXPR = re.compile(rf"\b(?:{_GROUP}|{_DAY_RANGE}|{_DAY_LIST})", re.IGNORECASE)
_DAY_TOKEN = re.compile(rf"\b({_DAY_ALT})s?\b", re.IGNORECASE)

_GROUPS = {
    "weekday": [1, 2, 3, 4, 5],
    "weekdays": [1, 2, 3, 4, 5],
    "weekend": [6, 7],
    "weekends": [6, 7],
    "every day": [1, 2, 3, 4, 5, 6, 7],
    "everyday": [1, 2, 3, 4, 5, 6, 7],
    "daily": [1, 2, 3, 4, 5, 6, 7],
    "all day": [1, 2, 3, 4, 5, 6, 7],
    "all days": [1, 2, 3, 4, 5, 6, 7],
    "each day": [1, 2, 3, 4, 5, 6, 7],
    "every weekday": [1, 2, 3, 4, 5],
    "every weekend": [6, 7],
}

_DAY_REFUSAL = re.compile(
    r"\b(except|excluding|excluded|but\s+not|other\s+than|every\s+other"
    r"|alternate|alternating|unless|apart\s+from)\b"
)


def _expand_day_expr(expr: str) -> list[int] | None:
    """Expand one day-selector expression; None if it cannot be expanded."""
    expr = expr.strip()
    group = _GROUPS.get(expr)
    if group is not None:
        return list(group)
    # Range: "mon-fri", "monday to friday", "monday through friday".
    tokens = _DAY_TOKEN.findall(expr)
    if re.search(r"(-|\bto\b|\bthrough\b|\bthru\b)", expr) and len(tokens) == 2:
        start = _DAY_NAMES[tokens[0].lower()]
        end = _DAY_NAMES[tokens[1].lower()]
        if start == end:
            return None
        days: list[int] = []
        cur = start
        while True:
            days.append(cur)
            if cur == end:
                break
            cur = cur % 7 + 1
        return sorted(days)
    if tokens:
        days = sorted({_DAY_NAMES[t.lower()] for t in tokens})
        return days
    return None


def _parse_days(text: str) -> list[int] | None:
    if _DAY_REFUSAL.search(text):
        return None
    matches = list(_DAY_EXPR.finditer(text))
    if not matches:
        return None
    expanded = [_expand_day_expr(m.group(0)) for m in matches]
    if any(e is None for e in expanded):
        return None
    first = expanded[0]
    if all(e == first for e in expanded):
        return first
    # Two different day selectors in one note: conflicting -> refuse.
    return None


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

_TIME = r"(?:noon|midnight|\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)?)"
_TIME_TOKEN = re.compile(rf"\b({_TIME})\b")
_RANGE = re.compile(rf"\b({_TIME})\s*(?:to|until|till|through|-)\s*({_TIME})")
_BETWEEN = re.compile(rf"\bbetween\s+({_TIME})\s+and\s+({_TIME})")
# A time-like token that is not part of a range (colon time, am/pm time,
# noon/midnight). Bare numbers are NOT counted: "9 to 11" is already
# refused as an unparseable range, and bare numbers appear in non-time
# contexts.
_STRAY_TIME = re.compile(r"\b(?:noon|midnight|\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b")
_HEDGE = re.compile(r"\b(around|about|approx(?:imately)?|roughly|maybe|~)\b")

_QUIET_MARKER = re.compile(
    r"(?:\bquiet\b"
    r"|\bdo\s+not\s+(?:ring|call|disturb)"
    r"|\bdon'?t\s+(?:ring|call|disturb)"
    r"|\bnever\s+(?:ring|call|disturb)"
    r"|\bno\s+(?:ringing|calls?|ring\b)"
    r"|\bdo\s+not\s+disturb\b"
    r"|\bsilent\b|\bsilence\b)"
)
_PREFERRED_MARKER = re.compile(
    r"(?:\bavailable\b|\bavailability\b|\bcall\b|\bring\b"
    r"|\bgood\b|\bworks?\b|\bprefer\w*\b|\bwelcome\b)"
)


def _parse_time_token(token: str, partner_ampm: str | None) -> int | None:
    """Parse one time token to minutes since midnight.

    ``partner_ampm`` is the other endpoint's am/pm marker, inherited by
    a bare hour (``9 to 11 am``). Returns None when undeterminable or
    out of range.
    """
    token = token.strip()
    if token == "noon":
        return 12 * 60
    if token == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)?", token)
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if minute > 59:
            return None
        if ampm:
            if hour < 1 or hour > 12:
                return None
            if ampm == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        else:
            if hour > 23:
                return None
        return hour * 60 + minute
    m = re.fullmatch(r"(\d{1,2})\s*(am|pm)?", token)
    if m:
        hour, ampm = int(m.group(1)), m.group(2) or partner_ampm
        if ampm is None:
            return None  # bare hour with no marker anywhere: out of grammar
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return hour * 60
    return None


def _ampm_of(token: str) -> str | None:
    m = re.search(r"\b(am|pm)\b", token)
    return m.group(1) if m else None


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_ranges(text: str) -> tuple[list[str] | None, list[str] | None]:
    """Extract preferred/quiet ranges; None means unspecified/refused."""
    # Collect non-overlapping range matches (between-form first).
    spans: list[tuple[int, int, str, str]] = []
    for m in _BETWEEN.finditer(text):
        spans.append((m.start(), m.end(), m.group(1), m.group(2)))
    for m in _RANGE.finditer(text):
        if any(s <= m.start() < e or s < m.end() <= e for s, e, _, _ in spans):
            continue
        spans.append((m.start(), m.end(), m.group(1), m.group(2)))
    spans.sort()

    if len(spans) > 2:
        return None, None

    # Stray time-like token outside every range span -> ambiguous.
    covered = [(s, e) for s, e, _, _ in spans]
    for m in _STRAY_TIME.finditer(text):
        if not any(s <= m.start() and m.end() <= e for s, e in covered):
            return None, None

    preferred: list[tuple[int, int]] = []
    quiet: list[tuple[int, int]] = []
    refused_preferred = False
    refused_quiet = False
    invalid_range = False

    for idx, (start, end, raw_a, raw_b) in enumerate(spans):
        a = _parse_time_token(raw_a, _ampm_of(raw_b))
        b = _parse_time_token(raw_b, _ampm_of(raw_a))
        if a is None or b is None or a == b:
            invalid_range = True
            continue
        # Clause segment before this interval: from the previous range
        # end (or start of text), so a quiet marker in an earlier clause
        # cannot leak into this one.
        prev_end = spans[idx - 1][1] if idx else 0
        before = text[prev_end:start]
        # Segment after, only up to the next ';' (clause boundary).
        after_end = text.find(";", end)
        after = text[end : after_end if after_end != -1 else len(text)]
        # Do not let the after-segment run into the next interval.
        if idx + 1 < len(spans):
            after = text[end : min(spans[idx + 1][0], len(text))] if after_end == -1 else after
        is_quiet = bool(_QUIET_MARKER.search(before))
        if not is_quiet and not _PREFERRED_MARKER.search(before):
            # No marker before it: a quiet marker in the same clause
            # after the interval still classifies it (e.g. "9-11 is quiet").
            if _QUIET_MARKER.search(after):
                is_quiet = True
        hedged = bool(_HEDGE.search(before)) or bool(_HEDGE.search(after))
        if hedged:
            # Hedge refuses this interval's kind, not the other kind.
            if is_quiet:
                refused_quiet = True
            else:
                refused_preferred = True
            continue
        if is_quiet:
            quiet.append((a, b))
        else:
            preferred.append((a, b))

    if invalid_range:
        return None, None
    if len(preferred) > 1:
        refused_preferred = True
    if len(quiet) > 1:
        refused_quiet = True

    pref_out = (
        None
        if refused_preferred or not preferred
        else [_fmt(preferred[0][0]), _fmt(preferred[0][1])]
    )
    quiet_out = None if refused_quiet or not quiet else [_fmt(quiet[0][0]), _fmt(quiet[0][1])]
    if pref_out is not None and pref_out == quiet_out:
        # Same slot claimed both available and quiet: contradictory.
        return None, None
    return pref_out, quiet_out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_note(text: str) -> dict:
    """Parse ``text`` into {days, preferred_range, quiet_range}.

    Unclear, conflicting or out-of-grammar parts are returned as None
    (refusal) rather than guessed.
    """
    norm = normalize_text(text)
    days = _parse_days(norm)
    preferred_range, quiet_range = _parse_ranges(norm)
    return {
        "days": days,
        "preferred_range": preferred_range,
        "quiet_range": quiet_range,
    }
