# KindWindow Step 08 — Message vocabulary survey

- **Survey run:** 2026-11-23 01:39–01:53 UTC (811.8 s, one streaming pass
  over the 20 cohort members). Aggregates only — no raw rows retained.
- **Source:** the local CASAS v1 `data.zip` (private, never in git),
  members `data/<home>.csv` for the 20 selected homes.
- **Method:** `kindwindow.real_ingest.survey_vocabulary` — `ZipFile.open`
  bounded streaming, original physical line number preserved, strip
  whitespace first, then accept exactly `ON` / `OFF` / `OPEN` / `CLOSE`.
  Full table: `reports/tables/message_vocabulary.csv` (2,002
  (home, sensor, message) rows, counts only).

## Headline totals (20 homes)

| bucket | rows | share |
|---|---|---|
| total physical lines | 78,880,583 | 100% |
| accepted (mapped motion/door) | 78,275,425 | 99.23% |
| quarantined (unmapped message) | 605,158 | 0.77% |
| malformed (unparseable structure) | 0 | 0% |
| ignored (blank lines) | 0 | 0% |

Row total matches the Step 03 audit exactly (78,880,583). Every home's
ledger reconciles (`total = accepted + quarantined + malformed +
ignored`; replays n/a for raw CSV). Per-home ledgers are in
`local/runs/step08/survey.json`.

## Accepted messages

| message | rows | meaning |
|---|---|---|
| `ON` | 38,839,598 | motion positive |
| `OFF` | 38,844,866 | motion deactivation (not positive) |
| `OPEN` | 295,446 | door positive |
| `CLOSE` | 295,515 | door positive |

## Quarantined messages (605,158 rows, 0.77%)

120 distinct messages overall. Quarantine buckets (see
`real_ingest.quarantine_category`):

| bucket | rows | examples |
|---|---|---|
| numeric | 434,106 | `0` (190,081), `100` (187,519), `1`–`99` and decimals such as `14.5` (56,506 combined) |
| button | 171,052 | `TAP` (73,107), `TAP_COUNT_01` (67,018), `TAP_COUNT_02` (2,830), `DEPRESS` (13,667), `RELEASE` (12,035), `HOLD_RELEASE` (1,558), `HOLD_DEPRESS` (179), `TAP_COUNT_00`–`TAP_COUNT_09` |

No other bucket occurs in the cohort:

- **bare `OF`: 0 rows.** The truncated-`OFF` anomaly noted in the
  Step 03 audit (`tm005`, `tm014`) is not in the 20-home cohort; no
  `OF` token was guessed to be `OFF` anywhere.
- **other (unknown tokens, lowercase, etc.): 0 rows.** Every
  unmapped message in the cohort is numeric or a button token.
- **tab-terminated tokens** (`ON\t`, `OFF\t`, ...) are stripped before
  matching and accepted as their plain form; they do not appear as
  separate vocabulary entries.

Per-home quarantine rates: aruba, hh101, hh122 have 0 quarantined rows.
Highest rates are hh111 (98,088 / 6,980,777 = 1.41%), hh114 (96,772 /
7,686,879 = 1.26%), hh108 (78,931 / 6,930,755 = 1.14%), hh105 (60,066 /
6,430,307 = 0.93%). All other homes are below 1%.

## Why type comes from the message, never the room name

Non-standard (numeric/button) payloads arrive on the same room tokens
as motion/door events — e.g. `OutsideDoor` carries 77,544 non-standard
rows, `Bathroom` 159,426, `Kitchen` 136,637, `Bedroom` 111,103. A room
name therefore cannot imply a device type. The frozen rule is applied
as specified: `ON`/`OFF` → motion, `OPEN`/`CLOSE` → door, everything
else quarantined. Numeric/button rows contribute nothing to history
counts, recency, or labels.

## Quarantine / window-exclusion decision (Step 08)

Quarantined rows here are *known non-activity readings* (a numeric
payload or a button event) with reliable timestamps — not ambiguous or
corrupt data. They are counted in the ledger and excluded from events,
but they do **not** exclude forecast windows: their time is reliable
and they say nothing about the validity of the motion/door stream.
Applying `docs/data-dictionary.md` §5 ("windows intersecting
quarantined events are excluded") literally to these rows would
exclude windows on the basis of a temperature-style reading or a
button press, which that rule (written for the JSONL pipeline, where
quarantined rows are corrupt/ambiguous) never contemplated. The
counterfactual — the per-home fraction of windows that *would* be
excluded under the strict reading — is computed in the features step
and recorded in `local/runs/step08/windows_reports.json`, so the
decision is auditable and reversible. Malformed rows (0 in the cohort)
would exclude windows per the dictionary rule.
