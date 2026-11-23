# KindWindow data dictionary — frozen Step 02

Status: **frozen at Step 02**. `configs/study.json` is the frozen
template from the handoff kit (`~/workspace/kindwindow-kit/data/study_config.json`,
protocol "KindWindow proposed freeze v1"); its values were **not**
changed in Step 02. The freeze of the interpretation below is Step 02
itself: after this point, changes to any rule in this document require
a dated amendment, preserved prior outputs, and a logged rerun
(see `05_EXPERIMENTS.md` post-test rules). Where the kit left a choice
open, the decision taken is marked **Decision (Step 02)**.

Source: Diane Cook's CASAS v1 mapped-name release, DOI
`10.5281/zenodo.15708568` (2025-06-20); only `data.zip` is used for
forecasting. Activity annotations and floorplans are out of scope.
The 49-home raw-identifier edition (different DOI) is not used.

## 1. Frozen source schema

Each source record is a line with four fields, in source order:

| Field | Meaning | Notes |
|---|---|---|
| `date` | Local calendar date, source text preserved | e.g. `2030-02-01` in fixtures; actual CASAS serialization is inspected per home before ingest |
| `time` | Local wall-clock time, source text preserved | e.g. `10:00:00`; source precision preserved, seconds aligned to zero only for *boundaries*, never by rounding event times |
| `sensor` | Mapped sensor identifier (pseudonymous in the release) | Mapped names can denote rooms. Do **not** infer device type from an `M`/`D` prefix or from room text alone — type comes from the validated sensor map |
| `message` | Sensor message / state | See §2. Only the mapped pairs in `configs/sensor-map.csv` are interpretable |

Normalized event (what the pipeline stores):

| Field | Type | Notes |
|---|---|---|
| release | string | CASAS release identifier (e.g. `casas-v1-mapped`, DOI `10.5281/zenodo.15708568`) |
| member path | string | ZIP member path as archived |
| member checksum | string | SHA-256 of the ZIP member (recorded at ingest; MD5 is the published archive check, SHA-256 is the recorded member hash) |
| original row number | integer | 1-based line number in the member, captured *before* filtering; headers are lines too (see §5) |
| home | pseudonymous home ID | |
| sensor | pseudonymous sensor ID | |
| sensor type | `motion` \| `door` | from the validated sensor map, never inferred from the ID |
| message | string | source message text, preserved |
| local timestamp | local wall-clock datetime | date + time as recorded; no timezone conversion without documented zone + unambiguous offset/fold |
| UTC timestamp | datetime, optional | only when the zone is verified and the offset/fold is unambiguous; otherwise absent, never guessed |
| mapping version | string | version of `configs/sensor-map.csv` applied |
| quality flags | list | e.g. `warmup`, `gap-adjacent`, `quarantine-adjacent` |

## 2. Message mappings

Authoritative table: `configs/sensor-map.csv`. Summary:

| Sensor type | Message | Mapped meaning | Positive flag | Counts for history |
|---|---|---|---|---|
| motion | `ON` | Motion activation (movement detected) | true | yes |
| motion | `OFF` | Motion deactivation (sensor reset / no movement) | false | yes |
| door | `OPEN` | Door opened | true | yes |
| door | `CLOSE` | Door closed | true | yes |

**Positive-label rule.** A target window's label is 1 iff at least one
mapped positive-state message falls in the target interval (see §6).
Motion `ON`, door `OPEN` and door `CLOSE` are positive. Motion `OFF`
is a valid mapped deactivation with positive flag **false**: an
OFF-only target has label 0, and OFF never creates a positive label
on its own.

**History counting.** History features count *all* accepted physical
messages, including motion `OFF` and both door states. So OFF counts
for history but not for labels, and door `OPEN` and `CLOSE` both count
for door features. The input mix reported for any run must state
whether valid OFFs count toward the history feature in question —
under this contract they always do.

**Ambiguous / unmapped examples (synthetic form only).** The rows in
`configs/sensor-map.csv` with disposition `quarantined` are synthetic
examples, not real CASAS records: `motion + OPEN`, `motion + CLOSE`,
`door + ON`, `door + OFF`, and `UNKNOWN` on either type. Any such
(type, message) pair in real data is quarantined, never mapped by
analogy, never treated as negative, and never used to fabricate an
event. Message matching for the mapped pairs is case-insensitive
after stripping surrounding whitespace (`is_positive` in
`src/kindwindow/contracts.py`); anything else is unmapped.

## 3. Time units and boundaries

* All times are **local wall-clock** times for the home. No UTC
  conversion, no Spark session-timezone reinterpretation: pipelines
  use local date/time semantics (or agreed integer local ticks)
  inside each valid segment.
* Forecast origins are **half-hour boundaries**: `:00` and `:30`,
  seconds and microseconds zero (`half_hour_floor`).
* Event times keep source precision; boundaries are generated, never
  obtained by rounding an event time.
* Target interval is half-open **`[t, t + 30m)`**; feature history is
  half-open **`[t − w, t)`** for `w` in (5, 30, 120) minutes.
  An event exactly at `t` belongs to the target; an event exactly at
  `t + 30m` belongs to the next target; an event exactly at `t − w`
  is included in that history window.
* History windows: **5, 30, 120 minutes**. Warmup: **120 minutes** —
  a window is scored only when 120 minutes of earlier available
  history exist inside the same valid segment (see §6).
* Recency: seconds since the most recent earlier accepted event
  strictly before `t`. Unknown recency is **missing plus an
  indicator**, never an invented zero interval.
* Prior frequency (predeclared): accepted events per elapsed observed
  hour within the prior 120 minutes, i.e. `count_120m / 2`. Its
  denominator is 2 observed hours. It is redundant with `count_120m`
  by construction (a fixed rescaling); it is kept as a named feature
  for interpretability and must not be presented as independent
  evidence. Fitted only on training data, like all encoders/scales.

## 4. Row identity and replay

Row identity is the tuple:

```
(release, member path, member checksum, original row number)
```

* A repeated ingest of the **same tuple is a replay**, not a new
  event: it is counted as a replay after classification and does not
  create a duplicate event.
* Two distinct rows with equal timestamp, sensor and payload are
  **legitimate distinct events** (different identity tuples); they
  are not deduplicated against each other.
* Normalized events carry the full identity tuple so any derived
  row can be traced back to its exact source line.

## 5. Error dispositions and reconciliation

Every physical input line is classified into exactly one of:

| Disposition | Meaning |
|---|---|
| **accepted** | First-seen data row, valid and mapped; becomes an event |
| **quarantined** | First-seen data row that cannot be interpreted (ambiguous/unmapped message, unparseable date/time, unknown zone behaviour, unlocatable time); stored under `local/quarantine/` with its identity and reason; never mapped by guessing |
| **explicitly ignored** | Non-data lines, e.g. **headers — counted separately** — and byte-identical duplicate members (see §9); recorded with a reason, never silently dropped |
| **replay** | A repeat of an already-classified identity tuple; counted after classification, not as a new accepted/quarantined row |

Ledger identities (reconciled per member, per batch, and in total):

```
physical input lines = accepted + quarantined + explicitly ignored
data-row attempts    = first-seen accepted + first-seen quarantined
                       + replays after classification of non-data lines
```

State the denominator in every report. Headers are counted
separately from data rows and never enter the data-row denominator.

Quarantine consequences:

* Target windows intersecting quarantined events **with reliable
  times** are excluded.
* If a quarantined time is unlocatable, exclude the affected
  home-day(s) when identifiable; if the day cannot be bounded,
  flag/exclude the affected file/segment rather than guessing.
* History validity is carried separately from label validity; which
  prior quality warnings produce inference-time flags vs. invalid
  history is recorded in the run manifest.
* The nominal analysis does **not** declare an outage from silence
  alone (see §8).

## 6. Windows and eligibility

A forecast window is defined by its origin `t` (a half-hour
boundary) and its home + valid segment. It is eligible iff **all**
of the following hold:

1. `t` is a half-hour boundary inside the file's observed span.
2. The complete target `[t, t + 30m)` lies inside the file's
   observed span.
3. Two hours (120 min) of earlier available history exist inside
   the **same** valid segment (warmup). History never bridges files,
   segments, or unresolved clock reversals.
4. The window does not intersect a quarantined event with a
   reliable time, an excluded outage, or an unlocatable-time
   home-day/segment (see §5).
5. The window survives split purge / boundary-crossing removal (§7)
   and, for gap-sensitivity runs, the gap rule (§8).

Features per eligible window (see `history_counts`):

* `count_5m`, `count_30m`, `count_120m`: accepted events in
  `[t−w, t)`, all types, OFF included.
* `door_30m` (and analogously per window): door-type events in the
  history window, both `OPEN` and `CLOSE`.
* `recency_seconds`: seconds since most recent earlier accepted
  event; `None` + indicator when none exists.
* Local hour, local weekday (from `t`), prior frequency (§3).
* Label `y`: 1 iff a mapped positive event falls in `[t, t+30m)`.

Work is per home, per file span, per continuous interpretable
segment. Never bridge files by accident.

## 7. Split rules

Splits are **per home, chronological by eligible-window count** `n`
(sorted by origin time; ties are impossible because origins are
distinct half-hour boundaries within a home):

```
c1 = floor(0.60n), c2 = floor(0.70n), c3 = floor(0.80n)
train      = windows [0, c1)
selection  = windows [c1, c2)
calibration = windows [c2, c3)
test       = windows [c3, n)
```

* The **unpurged** time boundaries (the origin times at `c1`, `c2`,
  `c3`) are saved before purging.
* **Purge:** forecast origins within **≤ 24 hours on both sides**
  of each boundary are removed (inclusive: exactly 24 h away is
  purged — the conservative `≤24h` convention). Purge counts and
  assignments are kept.
* **Label-crossing-boundary removal:** any window whose label
  interval `[t, t+30m)` crosses a boundary (`t < b < t+30m`) is
  removed. **Decision (Step 02):** with a 24 h purge this rule is
  subsumed (a 30-minute target cannot reach a boundary from more
  than 24 h away), but it is implemented independently in
  `purge_mask` so a future purge change cannot silently re-admit
  crossing windows. A boundary exactly at `t+30m` does not cross
  the target (the target ends there).
* Validation after split: disjoint keys, increasing times within
  each segment, availability, and no target crossover between
  segments. A sklearn random split or pooled time fold does **not**
  implement this proposal.
* If a training split has one class, or a segment becomes empty,
  document what is estimable; do not silently swap homes after the
  cohort freeze.

## 8. Gap sensitivity

**Decision (Step 02):** a "window intersecting a gap" is judged on
the window's **full span `[t − 120m, t + 30m)`** (history plus
target), not the target alone — a gap inside the history corrupts
the features the forecast is conditioned on, which is the
sensitivity being measured. A window is excluded from a
gap-sensitivity run iff that span intersects an event-free gap
between consecutive accepted events that is **strictly longer**
than the threshold. Gaps of exactly the threshold do **not**
exclude. Runs are separate for thresholds **6 h, 12 h, 24 h**;
each run reports the change in `n`, home coverage, prevalence and
each metric, and how long-gap exclusion favours active periods.
Leading/trailing open-ended silence (before the first / after the
last event) is not an inter-event gap and does not exclude on its
own; it is handled by the warmup (§6) and span rules. Future gaps
are retrospective masks only: no post-`t` records, future outage
knowledge, or label-valid flags enter model inputs.

## 9. DST, cross-month, and overlap rules

**DST.**

* Boundaries are generated on the **local wall clock**. On a
  spring-forward day the nonexistent hour (e.g. 02:00–03:00) simply
  has no boundaries and no events; nothing is interpolated.
* On a fall-back day the repeated hour is ambiguous in wall-clock
  terms: an event whose offset/fold cannot be resolved from the
  source is **quarantined** (unlocatable time, §5), and the
  affected home-day(s) are excluded when identifiable. Never
  resolve a fold by guessing, and never convert to UTC without a
  documented timezone and unambiguous offset/fold.
* Clock reversals that are not documented DST transitions are
  unresolved reversals: they end the current valid segment and
  start a new one; windows never bridge them.

**Cross-month.**

* History, recency and labels are computed on the continuous event
  stream; month boundaries are **not** resets. An event in the
  prior month changes the first next-month output (recency and
  120-minute counts carry across the month). Partitions (home /
  year-month) must carry a forward label halo and the needed
  lookback across physical partitions, dropped after the output
  keys are computed. The golden fixture tests this with a
  January→February crossing (see `tests/test_contracts.py`).

**Overlaps.**

* **Decision (Step 02):** overlapping file spans for the same home
  are treated as **separate segments**; a window's history and
  label use only events from its own segment, and windows never
  bridge the overlap. If two members are byte-identical (same
  checksum), the member that sorts later by casefolded member path
  is **explicitly ignored in full** and recorded as a discarded
  alias in the manifest — this is the deterministic,
  non-duplicating rule. Distinct rows with equal payload remain
  legitimate (§4); overlap handling never deduplicates by payload.
* A home's segments are processed in deterministic order
  (casefolded member path, then row number).

## 10. Tie rules

**Model selection (frozen).** Candidates are ranked by lowest mean
of per-home Brier loss on selection. Ties are broken by **preferring
the simpler model, then config order** (`choose_best` in
`src/kindwindow/contracts.py`). Simplicity rank (smaller = simpler):
per-home constant (0), per-home hour-of-week (1), recency-only
logistic (2), full L2 logistic (3, ordered by C in config order
0.1, 1, 10), random forest (4, ordered by (max depth, min leaf) in
config order). Do not refit using selection, calibration or test.

**Retention / abstention (frozen).** On selection only, rank windows
by `p_raw * (1 - p_raw)` (low values retained). Freeze the 50%,
75% and all-retain rules from the clean selection cohort using
**equal-home-weighted empirical quantiles**: weights are
`N / (H * n_home)` per training row (mean training weight one).
The frozen cutoff is the smallest uncertainty whose cumulative
weight reaches the target fraction of total weight
(`retention_threshold`).

* **Decision (Step 02) — cutoff-tie protocol:** all windows with
  uncertainty strictly below the cutoff are retained; windows
  exactly at the cutoff are ordered by a stable hash of their
  (home, origin) key and retained in that order only until the
  nominal retained weight is reached. The fraction of cutoff ties
  retained is recorded alongside the frozen threshold. At 100%
  retention, thresholding is bypassed explicitly (all pass).
* The exact numerical gates are applied to raw candidate
  probabilities in every test condition; retained windows are then
  scored with calibrated probabilities (or raw if the calibration
  gate failed). Test coverage can differ from nominal retention
  and must be measured (overall, per-home, and macro coverage).
* The chance comparator retains exactly the same number of windows
  within each home, using the separate deterministic seed
  (`random_retention_seed` 6701).

## 11. What is *not* decided here

* Cohort roster and per-home inclusion evidence (Step 03+):
  ≥90 distinct local dates with interpretable records **and**
  span ≥90 inclusive days (conservative rule, dated in the study
  configuration when frozen); missing ages / unclear resident
  status → pending evidence / excluded with a reason.
* Calibration gate: sigmoid on the frozen candidate's scores using
  only the distinct calibration segment, only when it has ≥50
  positives **and** ≥50 negatives across homes; otherwise identity
  calibration with counts/reason disclosed.
* Degradation conditions (10% / 30% events gone, 25% sensors
  gone, five fixed seeds) and their rounding/fraction basis are
  predeclared in the experiment protocol, not in this dictionary.
* Any rule above may only change by dated amendment with preserved
  prior outputs; a post-test implementation bug fix is logged as
  such and the rerun is labelled.

## 12. Golden fixture (verification)

`tests/fixtures/golden_events.jsonl` (9 fabricated events) and
`tests/fixtures/golden_windows.csv` (3 windows) encode this
dictionary end-to-end: OFF counts in history but not in labels,
the `t` / `t+30m` endpoint rule, simultaneous events at one
origin, and recency of 1 s / 1800 s / 1800 s. `tests/test_contracts.py`
recomputes all 3 windows exactly from the events file; any change
to the rules above must update the fixture and the test together.
