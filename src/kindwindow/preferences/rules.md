# Preference parser — grammar and rules (Step 06)

Parser version: `1.0.0` (`PARSER_VERSION` in `parser.py`).
Output: exactly `{days, preferred_range, quiet_range}`.
A range is `[HH:MM start, HH:MM end]`, end-exclusive; end earlier than
start means overnight into the next day. `null` means unspecified.
Day codes are ISO weekdays 1–7 (Mon=1 … Sun=7); the day list applies to
all intervals in the note. The grammar covers **one day selector and at
most one interval of each kind**. Anything else is a refusal: the
affected field is left empty, never guessed.

## 1. Normalisation

Conservative Unicode normalisation only: NFKC, curly quotes/apostrophes
(`‘ ’ “ ”`) and all dashes (`‐ ‑ ‒ – — ― −`) folded to ASCII, non-breaking
and zero-width spaces removed, whitespace collapsed, case folded for
matching. No stemming, no synonym expansion, no spell correction.

## 2. Days — one selector

| Form | Example | Result |
|---|---|---|
| Single day, full or abbreviated, singular or plural | `Monday`, `Mon`, `Fridays` | `[1]`, `[5]` |
| Comma / `and` / `&` list | `Mon and Wed`, `Mon, Thu and Sat` | `[1, 3]`, `[1, 4, 6]` |
| Day range | `Mon-Fri`, `Monday to Friday`, `Monday through Friday` | `[1, 2, 3, 4, 5]` (inclusive; wraps forward, e.g. `Fri-Mon` → `[1, 5, 6, 7]`) |
| Group | `weekdays`, `weekends`, `every day` / `everyday` / `daily` / `all days` | `[1–5]`, `[6, 7]`, `[1–7]` |

Aliases: `tue`/`tues`, `thu`/`thur`/`thurs`. Days are sorted ascending
and de-duplicated.

**Refusals (days → empty):** two different day selectors in one note
(`Monday … Tuesday` in separate clauses); `except` / `excluding` /
`but not` / `other than` / `unless`; `every other` / `alternate`
days; a degenerate range (`Monday to Monday`).

## 3. Times

| Form | Example | Result |
|---|---|---|
| Named | `noon`, `midnight` | `12:00`, `00:00` |
| 24-hour | `08:30`, `18:00`, `14:15` | as written |
| 12-hour | `9 am`, `9:30 pm`, `4:45 pm` | `09:00`, `21:30`, `16:45` (`12 am` → `00:00`, `12 pm` → `12:00`) |
| Shared marker | `9 to 11 am` | both inherit `am` → `09:00–11:00` |

A bare hour with no `am`/`pm` anywhere (`9 to 11`), an hour outside
1–12 with a marker, a 24-hour hour above 23, or minutes above 59 is
out of grammar. `start == end` is refused (zero-length interval).

## 4. Ranges

Two time tokens joined by `to`, `-`, `until`, `till`, `through`, or
`between X and Y`:

- `9 am to 11 am`, `08:30 to 09:45`, `09:00–12:00`, `10 pm to 2 am`
  (overnight: end < start), `between 11 pm and 6 am`, `noon to 2 pm`,
  `9:30 pm to midnight`, `midnight to 7 am`.

At most one interval of each kind. More than two intervals, a second
interval of the same kind, or a stray time-like token outside any range
(`call 9–11, and also at 2 pm`) refuses the range fields. A hedge
(`around`, `about`, `approx`, `roughly`, `maybe`) in the interval's
clause refuses that interval's kind only.

## 5. Preferred vs quiet

Quiet markers, read from the segment before the interval within its
clause (`;` is a clause boundary): `quiet`, `do not` / `don't` /
`never` + `ring` / `call` / `disturb`, `no ring` / `no calls` /
`no ringing`, `silent` / `silence`. If no marker precedes the interval
and none of the preferred markers (`available`, `call`, `ring`, `good`,
`works`, `prefer`) precedes it either, a quiet marker in the same
clause *after* the interval still classifies it as quiet
(`9–11 is quiet time`). Any other interval defaults to preferred.
Examples: `Do not ring … between 11 pm and 6 am` → quiet;
`Monday: available 09:00–12:00; quiet 10:00–10:30` → preferred
`09:00–12:00`, quiet `10:00–10:30` (overlap is allowed; identical
preferred and quiet intervals are contradictory and refuse both).

## 6. Refusal policy

Unclear, conflicting, hedged or out-of-grammar phrases leave the
affected field empty. The parser never guesses a day, a time, or a
kind. A partially parsed note must never silently overwrite existing
preferences — the caller must show the extracted fields beside the
original note and require explicit confirmation.
