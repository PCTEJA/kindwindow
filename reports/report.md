# KindWindow — Household Event Prediction and Scalable Sensor Data Processing

**UNT CSCE 5300 Introduction to Big Data and Data Science, Fall 2026**

**Author:** Teja Piepur Chakravarthi

**Project:** KindWindow — 30-minute-ahead forecast of any sensor event in previously observed CASAS smart homes, and the cost of computing it at scale.

**Release:** Step 15 (final report and reproduction), built on `main` at `21ce26a66cd9e03fa72c674c7e86b5cbdf3521ec`. See `docs/release.md` for the release notes, the verified commit ledger, and the fresh-clone verification outcome. The evidence index for every claim in this report is `reports/evidence.csv`.

**Data:** CASAS Smart Home dataset v1 (Cook), DOI `10.5281/zenodo.15708568`, CC BY 4.0. Only `data.zip` is used. Cohort: 20 documented single-resident older-adult homes (aruba, hh101–hh122), each with at least 90 days of interpretable logs.

**Group number:** not in the handoff packet and not confirmed on Canvas at the time of writing — see §9 and `docs/release.md` (OPEN). No group number is asserted anywhere in this report.

## Contents

1. Abstract
2. Introduction
3. Problem statement and existing methods
4. Method / algorithm
5. Strengths, weaknesses, applications
6. Own simulations vs paper results (RQ1, RQ2, RQ3, parser, demo)
7. Conclusion
8. References
9. Reproduction, evidence, and submission status

> **How to read the numbers.** Every figure and table in §6 names the command that regenerates it. All headline numbers are the verified frozen results of Steps 11–13; nothing in this report was re-derived. The test split was scored exactly once (Step 11); this report re-states those saved aggregates and never re-scores test. Brier score is unitless on [0, 1], lower is better; average precision (AP) is unitless on [0, 1], higher is better. "Macro" means the mean of the 20 per-home scores, so every home counts equally regardless of size.

---

## 1 Abstract

Can recent sensor history forecast whether any sensor event will occur in the next 30 minutes in a previously observed smart home, and what does it cost to compute such forecasts at scale? We study 20 single-resident CASAS homes (aruba, hh101–hh122) with a per-home chronological 60/10/10/20 split and a 24-hour purge at each boundary (n_train = 622,817; n_selection = 102,023; n_calibration = 102,026; n_test = 206,953 windows, 107,232 positives, prevalence 0.518). A frozen random forest (200 trees, depth 10, leaf 10, seed 2718), sigmoid-calibrated on the calibration split only and scored on test exactly once, reaches macro Brier **0.1410** (AP 0.806), beating the strongest baseline, a recency-only logistic model (0.1846), by **0.0436 (23.6%)** in 19 of 20 homes. Withholding the most uncertain half of windows under frozen abstention gates cuts the loss on retained windows to **0.0691** at 0.4871 coverage, and losing 30% of input events degrades Brier by only **+0.0016**. On one machine (AMD EPYC 9D25, 4 cores, 8.32 GB RAM), pandas is faster than Spark where both finish (1M synthetic events: 44.2 s vs 60.8 s; real 78.3M-event cohort: 109.8 s vs 305.4 s; medians of n = 3 parity-passing pairs), while Spark alone completes the 10M-event workload (228.1 s; pandas 0/3, memory limit). No published Brier or AP score exists for this task: 29 literature measurements across 6 papers were compared and **0 are genuinely comparable**, so within-study baselines are the valid comparison. Key limitations: known homes only (no unseen-home claim), a reduced Step 11 scope (gap 6 h/24 h, degradation 10%/25%-sensors/other seeds, abstention 75%, and notes-eval omitted), and frozen artifacts that are not byte-reproducible across refits (the hashed artifact of record is the frozen one).

## 2 Introduction

A resident who wants to invite a contact at a good time should be the one who decides when — and a forecast can only ever inform that decision, never make it. KindWindow is built around that boundary: it asks whether recent household sensor history can forecast, as a probability, whether *any* sensor event will occur in the next 30 minutes, and it embeds that probability in a resident-controlled invitation app in which approval is always an explicit resident action, quiet hours and pauses are honoured, and the approval path works without any probability at all.

The research gap is a practical one. Smart-home activity prediction usually predicts *named activities* (what happens next) from activity labels, or recognises activities already in progress. KindWindow instead forecasts a *label-free binary event*: any sensor event in a fixed 30-minute window, read directly off the raw event stream, with no activity annotation anywhere in the pipeline. That makes the target cheap to define and free of recognition error, but it also means there is no published Brier score or average-precision anchor for this exact task — a finding this report documents rather than works around (§6.6). The contribution is therefore threefold: (i) a sealed, chronological evaluation of a calibrated forest against three baselines on 20 homes; (ii) a measured, parity-checked pandas-vs-Spark comparison on the same machine at 1M, 10M, and 78.3M events; and (iii) a selective-prediction (abstention) and degradation analysis, plus a working consent-first demo, all reproduced from a fresh clone.

The resident controls: preferred days and hours, quiet periods, which contacts may be invited, pause/resume, postpone, reject, revoke, and delete. The forecast never contacts anyone, never infers health, consent, or willingness from sensors, and is shown only on the resident view as a synthetic experimental value in the demo.

## 3 Problem statement and existing methods

### 3.1 The problem

Given the sensor-event history of one known home up to time `t`, estimate P(any sensor event in `[t, t+30m)`). The unit of analysis is the 30-minute forecast window, anchored at half-hour boundaries inside a home's observed span, with two hours of earlier history required. The target is binary and label-free: 1 if at least one mapped positive-state message (motion activation, door open/close) falls in the target interval, 0 otherwise. Motion OFF is a valid deactivation and is not a positive. The forecast is evaluated as a probability (Brier score, average precision), not as a hard classification, because the downstream use is a resident's judgement, not an automated action.

### 3.2 Research questions

- **RQ1:** Can recent sensor features improve future recorded-event probability estimates over constant-rate, hour-of-week, and recency baselines?
- **RQ2:** At what tested event volumes, if any, is PySpark faster than equivalent pandas transformations on the same machine?
- **RQ3:** Can predictive loss be reduced by withholding uncertain predictions under degraded inputs, and how much prediction coverage would be lost?

### 3.3 Existing methods (summary; full comparison in §6.6)

Activity prediction on CASAS data is dominated by label-dependent methods. Minor, Doppa & Cook (KDD 2015) predict the *time until each named activity recurs* (regression in seconds; RAP MAE 8,433.38 s vs independent predictor 19,050.85 s, Table 3) on 24 testbeds, using activity labels from a recognition algorithm. Cook et al. (2013) evaluate *activity recognition* itself (84% weighted average accuracy on observed events). Dunne, Morris & Harper (2022) predict the *next labeled activity* (multi-class accuracy; semantic-blocks model 74.55% mean vs CRAFFT 66.91%) on HH101–HH103 with a 90/10 split. None of these reports a Brier score or average precision for a binary any-event forecast, and none shares KindWindow's task, unit, metric, and split simultaneously. The selective-prediction literature (El-Yaniv & Wiener 2010; Geifman & El-Yaniv 2017) supplies the risk–coverage vocabulary KindWindow's abstention analysis uses, but its guarantees are for 0/1 error on image classification, not observed Brier loss on sensor windows. A full paper-by-paper verdict is in `reports/method_comparison.md` and `reports/literature_comparison.csv` (29 measurements, 6 papers, 0 genuinely comparable).

### 3.4 Scope

Cohort: the first 20 homes satisfying documented single-resident older-adult evidence and ≥ 90 distinct interpretable dates with a ≥ 90-day span, in casefolded lexical order (aruba precedes hh101). 54 homes qualified; 34 further qualifiers are preserved as `eligible_beyond_cap`. Exclusions: 35 candidates failing the duration rule, 4 ambiguous mn/mv rows, 96 archive members with no demographic evidence in the kit, 1 evidence row (`mv101`) absent from the archive. The audit streamed 89 candidates (183,102,524 rows, 1 malformed row) and is recorded in `docs/cohort-audit.md` and `data/manifests/cohort.csv` (SHA-256 `407d8ecf…`). The study is within-home: every home is trained and tested on itself via a per-home chronological split, so no claim is made about unseen homes. Invitation *utility* — whether forecasts help real scheduling — is untested; the demo shows consent mechanics, not outcomes.

## 4 Method / algorithm

### 4.1 Dataflow (F1)

```
CASAS v1 data.zip (private, local only; MD5/SHA-256 verified)
  -> bounded streaming audit (ZipFile.open, 8 MiB chunks; raw logs never extracted)
  -> cohort freeze (20 homes, cohort.csv SHA-256 407d8ecf...)
  -> ingest: validate + reconcile -> normalized Parquet (local/normalized)
  -> vectorized windows + labels (pandas) / dense 5-minute grid (Spark)
  -> per-home chronological split 60/10/10/20, 24 h purge (split.csv SHA-256 65f5a997...)
  -> train (7 candidates) -> selection -> calibration -> freeze (frozen_model.json SHA-256 b2989ae5...)
  -> test scored exactly once (Step 11, reduced scope)
  -> systems study (generators + bench, 18 timed attempts, parity)
  -> literature comparison, demo, slides, this report
```

Regenerating command (fixture path): `python3 scripts/reproduce.py --profile fixture`. Real-data path (verification + documented commands only): `python3 scripts/reproduce.py --profile real`.

### 4.2 Acquisition and windows

Source fields are date, time, sensor identifier, and message, in two observed serializations (comma and space separated). An interpretable record is a physical line matching one of those serializations with a valid date/time and non-empty sensor/message; no device type is inferred from sensor names or room text. Windows are half-open `[t, t+30m)`: an event exactly at `t+30m` belongs to the next window (the golden fixture demonstrates this live in the demo). History is `[t-w, t)` for w in 5/30/120 minutes, plus seconds since the most recent earlier accepted event (missing + indicator, never an invented zero), door counts, local hour/weekday, prior frequency (accepted events per elapsed observed hour within the prior 120 minutes), and home indicators. Windows require a complete target interval inside a file's observed span with two hours of earlier history; clock-reversal segments are boundaries, never bridged. Feature history counts all accepted physical messages; door features count both door states; labels use only mapped positive-state messages. Two engines implement the same contract: pandas (stable sort once per home, typed columns, NumPy `searchsorted` + prefix sums) and Spark (dense per-home 5-minute grid including empty buckets, native DataFrame expressions, no Python UDFs in the hot path, no full-driver collection). Both were validated against an independent slow oracle on fixtures before timing, and every completed pair was parity-checked (chunked sorted merge, 1e-8 absolute tolerance, relative tolerance 0).

### 4.3 Split, estimators, calibration, abstention

Eligible windows are sorted per home and allocated chronologically at floor(0.60n), floor(0.70n), floor(0.80n); origins within 24 hours on both sides of each boundary are purged and windows whose label crosses a boundary are removed. Training rows are weighted per home, `N/(H·n_home)`, so each home counts equally. Seven candidates were fit on training only: three L2 logistic models (C ∈ {0.1, 1, 10}) and four random forests (200 trees × depth {5, 10} × leaf {10, 50}); the recency-only logistic baseline (C = 1, recency + home indicators) is fixed. Selection picks the lowest mean of per-home Brier on the selection split; ties prefer the simpler model, then config order. Calibration fits a single sigmoid on the frozen candidate's raw positive-class probabilities using only the calibration split (102,026 windows; 52,068 positives, 49,958 negatives — gate passed), with equal-home weights. Abstention ranks by `u = p_raw·(1−p_raw)` (low retained) with gates frozen on selection only (50% cutoff 0.203018, 75% cutoff 0.241647, 100% bypassed); gates are applied to raw probabilities and retained windows are scored with calibrated probabilities. Gap sensitivity excludes windows intersecting event-free gaps strictly longer than the threshold; degradation drops a deterministic hash-keyed fraction of input events (or sensors) and rescores with the frozen model, clean labels preserved.

### 4.4 Systems method

Both engines read identical immutable input bytes, normalize, write Parquet, reread, produce history features and labels, and complete output writes, with identical columns, precision, codec (Snappy), and partitioning. Generator parameters are fit from training data only (seed 941). A parent monitor outside each timed child starts a monotonic clock before launch, records SparkSession readiness separately, and stops after output is committed; startup is included in end-to-end time and reported separately. The aggregate process-tree memory ceiling is 70% of installed RAM (5,823,928,320 bytes here); the sampler is a 0.2 s tree-RSS sampler with a `/proc` fallback (psutil is absent on this machine — shared pages are double-counted and short peaks can be missed). Three measured attempts per engine per core workload (18 planned), alternating engine order, fresh processes, Spark caching disabled, quiet machine, one attempt at a time; the time cap is 1,800 s. Parity is compared outside timed intervals; interpretation uses only parity-passing pairs.

### 4.5 Parser and demo

The preference parser (v1.0.0) is a bounded token/regex grammar over 40 development notes (8 template families): day aliases/groups, AM/PM/24 h ranges, quiet markers/negation, cross-midnight intervals. Unclear or conflicting phrases are left unparsed for the resident to review; a partially parsed note never silently overwrites live preferences, and text-derived changes stay pending until explicit confirmation. The held-out evaluation (20 notes, 4 different families) was **not run** in the reduced Step 11 scope — see §6.7 and the omissions list. The demo app is a loopback FastAPI + SQLite app with synthetic identities only (resident Rosa, Contact A, Contact B), server-validated sessions, whitelisted contact response fields, expiring opaque invitations, and a parameterized clock for expiry tests. Latency is measured with a monotonic client timer, request start to response fully read, 10 warm-ups + 100 measured calls per endpoint at concurrency 1, against the packet's p95 ≤ 200 ms budget.

### 4.6 Code listing (illustrative)

The window contract is the one place where a short listing carries the whole study, so it is reproduced here (from `src/kindwindow/features/features.py`, vectorized path; the Spark engine implements the same half-open semantics on its 5-minute grid):

```python
# Half-open windows: history is [t - w, t); the target is [t, t + 30m).
# An event exactly at t + 30m belongs to the NEXT window, never this one.
# Counts use NumPy left insertion positions (searchsorted, side="left")
# plus prefix sums over the sorted per-home event times.
```

Full source is in the repo; the CLI contract is `uv run --locked kindwindow doctor|pipeline|features|baselines|train|freeze|demo|notes-parse`, with `evaluate` sealed (see §9). Note: `kindwindow report|audit|cohort|generate|benchmark|parity|notes-eval` are registered stubs that exit 2 — the Step 12 systems work used `generators.synthetic` and `local/runs/step12/*.py` directly, as documented in `reports/benchmarks/README.md`.

## 5 Strengths, weaknesses, applications

**Strengths.** The evaluation is sealed and chronological: the test split was never read, fit, tuned, or scored before Step 11, and was scored exactly once after the freeze, so the RQ1 numbers cannot be the product of test peeking. Per-home macro averaging prevents the largest homes from dominating. Baselines share the task, split, homes, and metric exactly, so the 0.0436 improvement is a like-for-like comparison. The systems comparison is parity-gated: only pairs whose outputs match exactly are interpreted, and every failed attempt is preserved in the record. The abstention analysis measures coverage instead of assuming it (nominal 50% → actual 0.4871). The demo enforces consent in code (409/403 with reasons, never invented times) rather than in prose.

**Weaknesses.** (i) Known homes only; per-home Brier varies from 0.0573 (hh116) to 0.2057 (hh114), and the model loses to recency in one home (hh119). (ii) The target is "any event", which is dominated by frequent sensors in active homes; prevalence ranges from 0.056 (hh116) to 0.839 (hh104) across homes. (iii) Frozen artifacts are not byte-reproducible across refits: parallel forest refits differ at last-bit float level (tree-averaging summation order varies with job scheduling), so a refit changes the artifact hash; the artifact of record is the hashed one, and its predictions agree with the recorded selection scores to 1e-12. (iv) The real pipeline has a +3 window discrepancy vs Step 08 (1,039,642 floored windows vs 1,039,639; one extra window in each of hh104, hh111, hh116 at exact hour boundaries; mechanism not isolated — an open note, not a claim). (v) psutil is absent, so benchmark memory uses a `/proc` fallback sampler. (vi) The reduced Step 11 scope (§6.7) leaves gap 6 h/24 h, degradation 10%/25%-sensors/other seeds, abstention 75%, and notes-eval unmeasured. (vii) The paired household-bootstrap intervals suggested by the packet were not computed; no confidence interval is claimed for the paired improvement. (viii) Reliability bins (F4) were not produced; calibration is evidenced only by the raw-vs-calibrated Brier change (0.141939 → 0.140965).

**Applications.** The only application demonstrated is the consent-first invitation flow: a resident sets hours, the app proposes only inside them, and the resident approves explicitly. Future invitation *utility* — whether forecasts actually help scheduling — is untested and would need a separate study with real residents, consent, and outcome measures. Nothing in this study supports health inference, occupancy inference for other parties, or deployment beyond the studied homes.

## 6 Own simulations vs paper results

### 6.1 Cohort, split, and training description (T1, T2)

**Table T1 — cohort flow and audit.** Regenerating command: `python3 scripts/reproduce.py --profile verify` (archive hashes); full audit narrative: `docs/cohort-audit.md`.

| Stage | Count |
|---|---:|---:|
| Archive file members (CASAS v1 `data.zip`) | 189 |
| Evidence rows in kit (`resident_evidence.csv`) | 94 (93 present, 1 absent: `mv101`) |
| Candidate single-resident homes audited by streaming | 89 |
| Rows audited | 183,102,524 (1 malformed: `ihs07` line 123429) |
| Qualifiers (≥ 90 dates and ≥ 90-day span) | 54 |
| Selected cohort (first 20, casefolded order) | 20 |
| Eligible beyond cap (qualifiers 21–54) | 34 |
| Excluded: duration / ambiguous mn / no evidence / absent | 35 / 4 / 96 / 1 |

**Table T2 — splits and prevalence by home (test).** Source: `reports/tables/test_scores.csv` and `reports/tables/split_summary.csv`. Command: `sha256sum data/manifests/split.csv reports/tables/split_summary.csv` (split SHA-256 `65f5a997…`).

| Home | n_test | n_pos | Prevalence | Frozen Brier | Frozen AP | Recency Brier |
|---|---:|---:|---:|---:|---:|---:|
| aruba | 2,062 | 1,434 | 0.695 | 0.130081 | 0.950838 | 0.195987 |
| hh101 | 3,521 | 2,395 | 0.680 | 0.153006 | 0.914627 | 0.186469 |
| hh102 | 9,902 | 6,276 | 0.634 | 0.128833 | 0.937750 | 0.180676 |
| hh103 | 20,651 | 11,985 | 0.580 | 0.180075 | 0.857530 | 0.211756 |
| hh104 | 7,751 | 6,501 | 0.839 | 0.099701 | 0.967077 | 0.119201 |
| hh105 | 20,180 | 11,097 | 0.550 | 0.187832 | 0.849651 | 0.233715 |
| hh106 | 12,433 | 6,545 | 0.526 | 0.163169 | 0.875050 | 0.220602 |
| hh108 | 20,659 | 11,233 | 0.544 | 0.159602 | 0.878129 | 0.201303 |
| hh109 | 15,938 | 10,323 | 0.648 | 0.191538 | 0.871339 | 0.213310 |
| hh111 | 20,660 | 12,691 | 0.614 | 0.180379 | 0.872278 | 0.216068 |
| hh112 | 1,279 | 878 | 0.686 | 0.145353 | 0.921927 | 0.197082 |
| hh113 | 4,840 | 2,988 | 0.617 | 0.156315 | 0.915851 | 0.218904 |
| hh114 | 20,612 | 10,411 | 0.505 | 0.205718 | 0.795468 | 0.238709 |
| hh115 | 3,268 | 1,439 | 0.440 | 0.097348 | 0.924624 | 0.235056 |
| hh116 | 1,451 | 81 | 0.056 | 0.057295 | 0.105471 | 0.099198 |
| hh117 | 3,268 | 236 | 0.072 | 0.077488 | 0.262158 | 0.151895 |
| hh118 | 11,198 | 4,001 | 0.357 | 0.089604 | 0.919724 | 0.123038 |
| hh119 | 18,472 | 2,790 | 0.151 | 0.093179 | 0.591415 | 0.092833 |
| hh120 | 6,819 | 2,637 | 0.387 | 0.134585 | 0.833834 | 0.145047 |
| hh122 | 1,989 | 1,291 | 0.649 | 0.188193 | 0.873449 | 0.210393 |
| **MACRO** | **206,953** | **107,232** | **0.518** | **0.140965** | **0.805909** | **0.184562** |

Split totals: n_train = 622,817; n_selection = 102,023; n_calibration = 102,026; n_test = 206,953. All 20 homes have both classes in test, so every per-home AP is defined. Note the AP extremes: hh116 (prevalence 0.056) has AP 0.105 and hh117 (0.072) has AP 0.262 — in near-silent homes the ranking task is nearly degenerate even though Brier is low, because a mostly-correct "almost never" forecast still ranks the rare positives poorly. Brier and AP disagree about which homes are "easy"; both are reported so neither is hidden.

**Figure F2 — aggregate training hourly counts.** Present as a table, not a plot: `reports/tables/training_eda.csv` (500 rows: per-home hourly n, positives, prevalence on train). No F2 plot was drawn; the table is the exhibit. Command: `sha256sum reports/tables/training_eda.csv`.

### 6.2 RQ1 — model selection and test-once results (T3, T4, F3)

**Table T3 — model selection (selection split only).** Source: `reports/tables/selection_scores.csv`, `docs/freeze-receipt.md`. Command: `sha256sum reports/tables/selection_scores.csv docs/freeze-receipt.md`.

| Rank | Candidate | Macro Brier (selection) |
|---:|---|---:|
| 1 | **forest-d10-leaf10** (200 trees, depth 10, leaf 10) | **0.151403** |
| 2 | forest-d10-leaf50 | 0.151520 |
| 3 | forest-d5-leaf50 | 0.161105 |
| 4 | forest-d5-leaf10 | 0.161203 |
| 5 | full-logit-C10 | 0.168686 |
| 6 | full-logit-C1 | 0.170000 |
| 7 | full-logit-C0.1 | 0.173833 |
| — | recency baseline (fixed, C = 1) | 0.193048 |

The winner beat the runner-up by 0.000117 — a near-tie resolved by the predeclared rule (lowest selection macro Brier; the two forests differ only in leaf size). Calibration: sigmoid fit on the calibration split only, coef 6.911216, intercept −3.445529, input pinned to raw positive-class probability, equal-home weights. Frozen artifact: `local/models/frozen_model.json`, SHA-256 `b2989ae57cd8c7c5d64f8913b60299910dbacd24f490775a90c7e84354527a81`; split SHA-256 `65f5a997…`; config SHA-256 `628db5f2…`; code revision at freeze `c4dfa98`.

**Table T4 — paired test loss and AP (test-once).** Source: `reports/tables/test_scores.csv`. Command: `sha256sum reports/tables/test_scores.csv` (189 rows: 9 model variants × 21 home/MACRO rows).

| Model | Macro Brier | Macro AP |
|---|---:|---:|
| **Frozen forest, calibrated** | **0.140965** | **0.805909** |
| Frozen forest, raw | 0.141939 | 0.805909 |
| Recency baseline | 0.184562 | 0.787817 |
| Hour-of-week baseline | 0.225579 | 0.608696 |
| Constant baseline | 0.237200 | 0.511600 |

Paired improvement vs the strongest baseline (recency): **+0.043597 (23.6%)** in macro Brier. The frozen calibrated model beats recency in **19/20 homes**; the sole exception is hh119 (0.093179 vs 0.092833, a difference of 0.000346 in recency's favour). Best home hh116 (0.057295), worst hh114 (0.205718). Calibration helps slightly (raw 0.141939 → calibrated 0.140965) and does not change AP. No bootstrap interval is reported for the paired improvement (not computed — see §5(vii)).

**Figure F3 — per-home Brier differences.** `reports/figures/per_home_brier.svg` (frozen calibrated vs recency, per home plus MACRO pair). Verification command: `python3 scripts/make_figures.py --check` (validates every bar height against `test_scores.csv`; PASS in Step 15). The figure was drawn by hand in SVG in Step 11 and is verified, never rewritten, here.

**Answer to RQ1: yes.** Recent sensor history improves the 30-minute event forecast well beyond every baseline on the same homes and keys — by 0.0436 Brier (23.6%) over the strongest baseline, consistently across 19 of 20 homes.

### 6.3 RQ3 — abstention, gap sensitivity, degradation (T5, F5)

**Table T5 — degradation and retained denominators.** Source: `reports/tables/test_scores.csv`, `local/runs/step11/results.json`. Command: `sha256sum reports/tables/test_scores.csv`.

| Condition | Macro Brier | Denominator / coverage |
|---|---:|---:|
| Clean, 100% retention | 0.140965 | 206,953 windows |
| Abstention, 50% retention (frozen gates) | 0.069071 | 100,812 / 206,953 retained; coverage 0.4871; retained prevalence 0.575; retained AP 0.886979 |
| Gap 12 h, flagged windows excluded | 0.162033 | 176,506 kept of 206,953; 30,447 flagged; kept prevalence 0.604; AP 0.813272 |
| Degradation, 30% of events gone, seed 2207 | 0.142559 | 206,953 windows rescored, 0 missing; 23,475,783 of 78,275,425 events dropped (actual fraction 0.29991); AP 0.802757 |

**Figure F5 — risk against achieved coverage.** `reports/figures/risk_coverage.svg`. Regenerating command: `python3 scripts/make_figures.py` (reads `test_scores.csv`). Only the two measured retentions (50%, 100%) are plotted; the 75% point was omitted in the reduced scope and is not interpolated.

Abstention: withholding the most uncertain half more than halves the loss on what is kept (0.1410 → 0.0691) at the price of answering only ~49% of windows; the coverage shortfall (nominal 0.50 → actual 0.4871) is itself a measured result of applying selection-frozen gates to a new split, exactly as the protocol expects. Gap 12 h: excluding the 30,447 flagged windows *raises* Brier to 0.162033 — the flagged windows are disproportionately easy/silent periods (kept prevalence 0.604 vs 0.518 overall), so this is a harder subset, not a worse model. Flagged windows concentrate in hh119 (11,331), hh118 (4,819), hh120 (2,668), hh108 (2,660); aruba and hh113 have none. Degradation: losing 30% of events moves Brier by only +0.001595 — the frozen model is nearly insensitive to losing almost a third of the event stream, because the features are counts over 5/30/120-minute histories and recency, which degrade gracefully when events are thinned uniformly at random. Failed cases: none — every test window was scored in every reported condition.

**Answer to RQ3: yes, with the stated scope.** Abstention reduces loss on retained predictions (0.0691 at 0.4871 coverage), and the model is robust to substantial event loss (+0.0016 at 30% gone, seed 2207). The unmeasured conditions (gap 6 h/24 h, 10% events, 25% sensors, other seeds, 75% retention) remain open — see §6.7.

### 6.4 RQ2 — measured systems study (T6, F6, F7)

**Table T6 — pipeline parity and all timed attempts.** Sources: `reports/benchmarks/attempts.csv` (18 rows), `reports/benchmarks/summary.csv`, `reports/benchmarks/parity.json`. Commands: `sha256sum reports/benchmarks/attempts.csv reports/benchmarks/summary.csv reports/benchmarks/parity.json`; full rerun (about 48 min, exclusive machine): `python3 scripts/reproduce.py --profile systems --full`.

Machine: AMD EPYC 9D25, 4 logical cores, 8,319,897,600 bytes RAM (70% cap 5,823,928,320), Linux 7.0.0-44-generic, Python 3.12.3, pandas 2.2.3, PySpark 3.5.5, Java OpenJDK 17.0.21.1 (workspace install, on PATH). Threads: pandas/Arrow/BLAS 2; Spark `local[2]`, shuffle partitions 4, AQE off. Sampler: 0.2 s tree-RSS, `/proc` fallback (psutil absent). Study wall time: 2026-11-23T03:02:37Z → 03:50:35Z.

| Workload | Engine | Attempts | Success | Median s (min–max) | Startup median s | Peak tree median (max) | Throughput ev/s | Parity |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Synthetic 1M (1,000,000 ev; 82,773 win) | pandas | 3 | 3/3 | **44.154** (43.621–44.206) | 0.626 | 1.27 GB (1.31 GB) | 22,648 | 3/3 pass |
| Synthetic 1M | spark | 3 | 3/3 | **60.790** (57.051–60.856) | 8.991 | 1.06 GB (1.89 GB) | 16,450 | 3/3 pass |
| Real (78,275,425 ev; 1,039,642 win) | pandas | 3 | 3/3 | **109.847** (109.354–112.332) | 1.302 | 1.24 GB (1.27 GB) | 712,586 | 3/3 pass |
| Real | spark | 3 | 3/3 | **305.412** (301.973–305.533) | 8.974 | 1.55 GB (2.60 GB) | 256,295 | 3/3 pass |
| Synthetic 10M (10,000,000 ev) | pandas | 3 | **0/3** | unavailable (memory_limit) | — | hit 5.82 GB cap | unavailable | not run |
| Synthetic 10M (829,024 win) | spark | 3 | 3/3 | **228.088** (225.321–240.610) | 9.139 | 1.73 GB (2.51 GB) | 43,843 | not run (no pair) |

18 attempts planned, 18 recorded, **15 successes, 3 failures** — all 3 failures are pandas synthetic-10M `memory_limit` at 167.7–178.8 s against the 5.82 GB cap (the pandas JSONL path materialises one dataclass per event during ingest). No timeouts, OOM kills, disk errors, or orphan Java processes occurred. All 6 completed pairs pass parity (1,039,642/1,039,642 real keys, 82,773/82,773 1M keys, 0 mismatches on all 6 compared columns).

**Figure F6 — runtime vs event count:** `reports/figures/runtime_vs_events.svg`. **Figure F7 — memory vs event count:** `reports/figures/memory_vs_events.svg`. Regenerating command for both: `python3 scripts/make_figures.py` (reads `reports/benchmarks/summary.csv`).

Interpretation (parity-passing comparisons only): pandas was faster at both matched sizes — Spark 1.38× slower at 1M and 2.78× slower on the real cohort. No ordering reversal was observed over the tested interval (1M synthetic to the 78M-event real cohort), and none is interpolated. At 10M, pandas cannot complete on this machine under the RAM cap while Spark completes in 225–241 s: a resource-feasibility boundary for the pandas JSONL pipeline, not a speed ordering — no pandas-vs-Spark ordering is claimed at 10M, and Spark's 10M outputs are uncompared (feasibility observation only). Throughput must not be compared across families (real reads columnar Parquet; synthetic parses JSONL and materialises dataclasses). Spark's ~9 s JVM startup is a fixed tax (15% of the 1M runtime, ~3% of real); excluding startup entirely, Spark is still ~8 s slower at 1M (51.8 s vs 43.5 s net). Scope asymmetry, documented not hidden: the synthetic pandas path additionally writes and rereads a normalized Parquet copy; the Spark path does not persist one. Runtime variance was small (pandas ranges 0.6 s at 1M, 3.0 s real; Spark 3.8 s, 3.6 s, 15.3 s at 10M).

**Answer to RQ2: on this machine, Spark is never faster at the tested volumes where a comparison exists; it is the only engine that finishes at 10M.** The tested interval is 1M–78.3M events; a crossover, if one exists, lies outside it or is masked by the 10M pandas feasibility boundary.

### 6.5 Parser results (T7)

**T7 — held-out note extraction: OMITTED, not measured.** The parser (v1.0.0) scored 40/40 exact on the 40 development notes (`tests/fixtures/notes_dev.jsonl`; 78 parser tests pass at that step), but the held-out evaluation on the 20 test notes (`data/heldout/notes_test.jsonl`) was not run in the reduced Step 11 scope, and no held-out precision/recall/exact-match numbers exist. The demo shows the frozen parser on a synthetic dev-style note only. Running notes-eval now would require the evaluation worker's sealed protocol and a fresh decision; it is recorded as open work in §9, not silently treated as done.

### 6.6 Literature comparison (F8, §3.3 continued)

**F8 — comparable paper results: no figure, because there is nothing to plot.** 29 measurements across 6 papers were compared (`reports/literature_comparison.csv`); **0 are genuinely comparable** to a 30-minute, any-event, Brier/AP forecast on CASAS. Command: `sha256sum reports/literature_comparison.csv reports/method_comparison.md`. The closest work in intent is Minor et al. (2015) — RAP MAE 8,433.38 s vs independent predictor 19,050.85 s on time-to-next-named-activity regression — but seconds-valued MAE on activity segments cannot be subtracted from a Brier loss on 30-minute windows, and the splits, labels, and units all differ. Cook et al. (2013) report 84% activity-recognition accuracy (recognition of the present, not forecasting of the future). Dunne et al. (2022) report 74.55% next-labeled-activity accuracy on HH101–HH103 (homes that overlap the cohort) with a 90/10 split — same homes, different task, metric, and split. Woodford & Ghandour (2025), the packet's subscription candidate for TA review, is unverified (closed access; metadata only) and no number from it is recorded. The selective-prediction papers supply vocabulary only. The full verdict, with per-row non-comparability reasons, is `reports/method_comparison.md`. Consequence: KindWindow's only valid quantitative comparisons are its within-study baselines (§6.2), which share task, split, homes, and metric exactly. No external bar was cleared or failed, because no external bar exists for this task.

### 6.7 Demo access and latency (T8)

**Table T8 — demo access and latency.** Source: `demo/demo_results.json`, `demo/demo_log.txt`, `demo/latency_samples.csv`, `docs/demo.md`. Regenerating command: `python3 scripts/reproduce.py --profile demo` (or `uv run --locked python demo/run_demo.py`).

Recorded run (22 Nov 2026, commit `2942dfb`): **52/52 checks passed**, 0 failures. Golden fixture through the clean pandas pipeline: 9 events → 3 windows, ledger reconciles. Cold launch to ready: **0.550 s**. Walkthrough (synthetic resident Rosa, Contact A/B): propose-without-preferences 409, note parse pending → cancel (unchanged) → confirm (live), quiet/outside-hours/wrong-day/ungranted-contact refusals (409/403), propose → approve (Contact A sees one whitelisted invitation; Contact B sees nothing), duplicate replay returns the same invitation, postpone needs fresh approval, reject cannot be revived, pause invalidates approved invitations (2), revoke immediate and idempotent, delete removes everything (0 remaining) and invalidates sessions. Latency (n = 100 measured + 10 warm-ups per endpoint, concurrency 1):

| Endpoint | p50 (ms) | p95 (ms) | Max (ms) |
|---|---:|---:|---:|
| GET / | 1.641 | 2.238 | 3.199 |
| GET /preferences | 2.814 | 3.376 | 3.977 |
| GET /invitations | 3.012 | 3.765 | 7.619 |
| GET /contact/invitations | 3.294 | 4.259 | 5.926 |
| POST /preferences | 3.332 | 3.841 | 4.160 |

Worst p95 **4.259 ms** vs the packet budget p95 ≤ 200 ms — far inside budget. The Step 15 fresh-clone rerun (see §9) reproduced the demo end to end: 52/52 checks, cold launch 0.801 s, worst p95 99.907 ms (POST /preferences under concurrent load on the VM) — still inside budget. No screen recording is included: this VM is headless, so a video would have been staged rather than real; the deliverable is the working demo plus its captured log, results JSON, and raw latency samples. Invite expiry is not shown live (real clock; covered by `tests/test_app.py::test_expiry_at_exact_expires_at` with a fake clock).

### 6.8 Reduced Step 11 scope — stated plainly

The test-once evaluation ran in reduced scope at the user's direction (timebox ~20–30 min). Omitted, not measured: gap sensitivity at 6 h and 24 h (only 12 h); degradation at 10% of events, 25% of sensors, and seeds other than 2207 (only 30% events, seed 2207); abstention at 75% retention (only 50%/100%); notes-eval on held-out notes (T7); paired household-bootstrap intervals; reliability bins (F4). A post-test bug fix is on the record: the first degradation run failed with `KeyError: 'label'` (degraded-stream label colliding with the clean label on merge); only the degradation condition was rerun (253 s) after fixing `src/kindwindow/evaluation/evaluation.py` to drop the degraded label before merging; clean test scores were never recomputed and previous outputs were preserved. Every number in §6.2–§6.4 is the reduced-scope result; nothing omitted is presented as measured.

## 7 Conclusion

**RQ1 — yes.** Recent sensor history improves the 30-minute any-event forecast well beyond every baseline: frozen calibrated forest macro Brier 0.140965 (AP 0.805909) vs recency 0.184562, a paired improvement of +0.043597 (23.6%), in 19 of 20 homes, on a test split scored exactly once after the freeze.

**RQ2 — pandas faster where both finish; Spark alone finishes at 10M.** On one machine, pandas beats Spark at 1M (44.154 s vs 60.790 s) and on the real 78.3M-event cohort (109.847 s vs 305.412 s), medians of n = 3 parity-passing pairs; at 10M pandas hits the 5.83 GB memory cap (0/3) while Spark completes in 228.088 s (feasibility only, no ordering claimed).

**RQ3 — yes, within the measured conditions.** Abstention at 50% retention cuts retained Brier to 0.069071 at 0.4871 coverage; the model loses only +0.0016 Brier when 30% of events are gone (seed 2207); long-gap windows are disproportionately easy, so excluding them raises Brier to 0.162033 on the harder kept subset.

**Null and negative findings, kept in the report.** No external Brier/AP anchor exists for this task (0 of 29 literature measurements genuinely comparable). The model loses to recency in hh119. Per-home quality varies fivefold in Brier and far more in AP. The parser's held-out accuracy is unmeasured. The +3 floored-window discrepancy is unexplained. These are limits of the study as run, not footnotes to be resolved by rewording.

**Future work.** (1) Run the omitted Step 11 conditions (gap 6 h/24 h, degradation 10%/25%-sensors/remaining seeds, abstention 75%, notes-eval) under a fresh, predeclared protocol — noting that any new test scoring must be justified against the test-once seal and recorded as a separate, labeled evaluation, not folded silently into the Step 11 numbers. (2) Compute the paired household-bootstrap intervals the packet suggests. (3) Produce reliability bins (F4) from saved predictions if row-level predictions are retained locally, or state their absence. (4) Isolate the +3 floored-window mechanism. (5) Test on unseen homes (a new cohort split) before any generalisation claim. (6) Evaluate invitation utility with real residents under consent. (7) If the TA requires the additional paper replication, pursue Woodford & Ghandour (2025) through the UNT library with a full paper card first — access is currently unverified.

## 8 References

Numbered IEEE-style, in order of first use.

[1] D. Cook, "CASAS Smart Home dataset — free living, motion, door, activity labels, v1," Zenodo, 2025. DOI: 10.5281/zenodo.15708568. CC BY 4.0. Dataset used by KindWindow; only `data.zip`.

[2] D. Cook, A. Crandall, B. Thomas, and N. Krishnan, "CASAS: A Smart Home in a Box," *Computer*, vol. 46, no. 7, pp. 62–69, 2013. DOI: 10.1109/MC.2012.328. Weighted average activity-recognition accuracy 84% (paragraph following Table 2).

[3] B. Minor, J. R. Doppa, and D. J. Cook, "Data-Driven Activity Prediction: Algorithms, Evaluation Methodology, and Applications," in *Proc. KDD'15*, 2015. DOI: 10.1145/2783258.2783408. Table 3: RAP MAE 8,433.38 s vs independent predictor 19,050.85 s; Table 4 AUETF; Table 6 CAFE.

[4] R. Dunne, T. Morris, and S. Harper, "A semantic blocks model for human activity prediction in smart environments using time-windowed contextual data," *Journal of Reliable Intelligent Environments*, 2022. DOI: 10.1007/s40860-022-00194-1. SBM 74.55% mean vs CRAFFT 66.91% (Tables 1, 5, 6); experiment materials DOI: 10.17632/jm28kgt7tm.1.

[5] R. El-Yaniv and Y. Wiener, "On the Foundations of Noise-free Selective Classification," *JMLR*, vol. 11, no. 53, pp. 1605–1641, 2010. Risk–coverage vocabulary for abstention.

[6] Y. Geifman and R. El-Yaniv, "Selective Classification for Deep Neural Networks," in *Proc. NeurIPS 2017*. SGR; 2% top-5 ImageNet error guaranteed with probability 99.9% at almost 60% coverage (abstract).

[7] B. J. Woodford and A. Ghandour, "Investigating activity recognition in smart homes through embedded feature selection," *Journal of Ambient Intelligence and Humanized Computing*, vol. 16, no. 6–7, pp. 699–712, 2025. DOI: 10.1007/s12652-025-04971-7. Closed access; unverified — no values recorded (see §6.6).

[8] F. Pedregosa et al., "Scikit-learn: Machine Learning in Python," *JMLR*, vol. 12, pp. 2825–2830, 2011. Random forest, logistic regression, probability calibration, Brier score, average precision. Version used: see `uv.lock`.

[9] pandas development team, "Scaling to large datasets," pandas documentation. Column projection, compact dtypes, chunking by key. https://pandas.pydata.org/docs/user_guide/scale.html

[10] Apache Spark, "Spark SQL Performance Tuning" and "PySpark Installation and Java Requirements," Spark documentation. Partitioning, adaptive query execution, Java prerequisites. Version used: PySpark 3.5.5, OpenJDK 17.0.21.1.

[11] FastAPI documentation, "Tutorial — Testing" and "Tutorial — SQL (Relational) Databases." https://fastapi.tiangolo.com/

[12] Astral, "uv — Locking and syncing." https://docs.astral.sh/uv/concepts/projects/sync/ Version used: uv 0.13.9, Python 3.12.3.

[13] KindWindow study artifacts (this repo): `docs/freeze-receipt.md` (Step 09 freeze), `reports/tables/step11_summary.md` and `reports/tables/test_scores.csv` (Step 11 test-once), `reports/benchmarks/README.md` (Step 12 systems study), `reports/method_comparison.md` and `reports/literature_comparison.csv` (Step 13), `docs/demo.md` and `slides/slides.md` (Step 14), `docs/release.md` and `reports/evidence.csv` (Step 15, this release).

*Assistance statement.* This work was produced by the student with Muse agent assistance under the coordinator/worker structure recorded in the repo (`AGENTS.md`); all Git commits are authored and committed by Teja Piepur Chakravarthi <pcteja2000@gmail.com> (verified PCTEJA, GitHub user ID 39472754). The TA's acceptance of the KindWindow proposal is student-confirmed (22 November 2026); no TA correspondence is in the handoff packet (`docs/course-decision.md`).

## 9 Reproduction, evidence, and submission status

**Reproduce it.** `sh scripts/reproduce.sh --profile fixture` (no private data), `python3 scripts/reproduce.py --profile verify` (hashes incl. the private CASAS archive), `--profile figures` (figures from committed tables), `--profile demo` (52-check demo), `--profile systems` (fixture-scale; `--full` for the 18-attempt study), `--profile real` (verification + documented commands only). PowerShell: `.\scripts\reproduce.ps1 -Profile fixture`. The test split is sealed: `reproduce.py` refuses `--run-evaluate` and never invokes `kindwindow evaluate`, `train`, or `freeze`.

**Evidence.** `reports/evidence.csv` indexes every committed artifact and every local study artifact by SHA-256, with the command that re-verifies each one (`python3 scripts/build_evidence.py --check`). The course matrix — every packet-required item, present or explicitly omitted with a reason — is `reports/course_matrix.md`.

**Submission status (OPEN items, not asserted).** Group number: unknown — slide/report PDF and PowerPoint export naming follows the Canvas/group-number convention and cannot be fixed until the group number is confirmed; recorded as OPEN in `docs/release.md`. Canvas destination, deadline, timezone, and required formats: to be confirmed from current Fall 2026 Canvas at submission time (the undated brief's Sept 30 / Oct 31 / Nov 8 dates conflict with the accepted proposal's Oct 24–30 / Nov 21–29 / Week 16 schedule; nothing is backdated and no extension is inferred). Turnitin value: only the actual Canvas result can be recorded; none exists yet. Submission receipt: to be retained privately by the student at submission. Presentation: 15 minutes + up to 5 minutes Q&A; single presenter Teja Piepur Chakravarthi (`slides/slides.md`).

**Fresh-clone verification (Step 15, this release).** Cloned at `21ce26a` to a fresh directory, `uv sync --locked --group dev --group spark`, `kindwindow doctor` OK, pytest 212 passed / 1 failed / 2 skipped, demo 52/52 checks passed (see `docs/release.md` for the one failure's cause and the full numbers). Note: the first fresh-clone attempt failed on disk space (`/tmp` is a 512 MB tmpfs); the retry ran under `~/workspace/kw-work/` with `TMPDIR` pointed there. Verified-data rerun: CASAS archive MD5 and SHA-256 re-verified and matched; frozen model, split, cohort manifest, and config hashes re-verified and matched (see `docs/release.md`).
