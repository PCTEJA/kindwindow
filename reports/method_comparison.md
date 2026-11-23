# KindWindow Step 13 — method comparison (literature comparison)

Step 13 worker, literature comparison. Sources checked **22 November 2026 (US Central)**.
Machine-readable table: `reports/literature_comparison.csv` (29 rows, one per compared
measurement). This file is the narrative that goes with it.

Scope note (packet `03_DELIVERY_PLAN.md`, Step 13 gate): cite primary tables, identify
genuinely comparable measurements, and place KindWindow baseline comparisons alongside
them with units. No separate reproduction track or PR is added: none is in the packet,
and the accepted proposal is merged as-is. The contingency in packet `01` (a replication
experiment) applies only if a current course addendum expressly requires one; no such
addendum is in the packet, so no replication was run.

## 1. The KindWindow numbers this comparison is anchored on

Task: 30-minute-ahead forecast of **any sensor event** in previously observed CASAS smart
homes. Data: CASAS v1 (Cook, DOI `10.5281/zenodo.15708568`, CC BY 4.0). Cohort: 20 homes
(aruba, hh101–hh122). Split: per-home chronological 60/10/10/20 with a 24 h purge on both
sides of each boundary. Windows: n_train = 622,817; n_selection = 102,023;
n_calibration = 102,026; n_test = 206,953 (107,232 positives, prevalence 0.518).
No activity labels are used anywhere: the target is defined from the raw sensor-event
stream alone. All values below are from the frozen test-once evaluation (Step 11) and the
measured systems study (Step 12); they were not re-derived here.

### 1.1 Test-once results (frozen forest-d10-leaf10, 200 trees, depth 10, leaf 10, seed 2718, sigmoid-calibrated)

| Model | Macro Brier (unitless, 0–1; lower is better) | Macro AP (unitless, 0–1; higher is better) |
|---|---:|---:|
| Frozen forest, calibrated | **0.140965** | **0.805909** |
| Frozen forest, raw (uncalibrated) | 0.141939 | 0.805909 |
| Recency-only logistic baseline | 0.184562 | 0.787817 |
| Constant (per-home train rate) baseline | 0.237200 | 0.511600 |
| Hour-of-week baseline | 0.225579 | 0.608696 |

Paired improvement vs the strongest baseline (recency): **+0.043597** in macro Brier
(23.6% relative). The frozen model beats recency in 19/20 homes; the sole exception is
hh119 (frozen 0.093179 vs recency 0.092833). On selection, the winner scored 0.151403 vs
recency 0.193048. Calibration helps slightly (raw 0.141939 → calibrated 0.140965).

### 1.2 Robustness and abstention (same frozen model, same test split)

| Condition | Macro Brier | Denominator / coverage |
|---|---:|---|
| Clean, 100% retention | 0.140965 | 206,953 windows |
| Abstention, 50% retention (frozen gates) | 0.069071 | 100,812 / 206,953 retained; coverage 0.4871 |
| Gap 12 h, flagged windows excluded | 0.162033 | 176,506 kept of 206,953; 30,447 flagged |
| Degradation, 30% of events gone, seed 2207 | 0.142559 | 206,953 windows rescored; +0.001595 vs clean |

Note the gap result's direction: excluding long-gap windows makes the scored set
*harder* (flagged windows are disproportionately easy/silent periods; kept prevalence
0.604 vs 0.518 overall), so 0.162033 is not a "worse model" — it is a different, harder
subset.

### 1.3 Systems study (Step 12, one machine: AMD EPYC 9D25, 4 cores, 8.32 GB RAM, 70% RAM cap, 1800 s cap; medians, n = 3, all parity-passing pairs)

| Workload | pandas (s) | Spark (s) | Outcome |
|---|---:|---:|---|
| Synthetic 1M events | 44.154 | 60.790 | pandas faster (Spark 1.38× slower) |
| Real cohort, 78,275,425 events | 109.847 | 305.412 | pandas faster (Spark 2.78× slower) |
| Synthetic 10M events | 0/3 (memory_limit, ~5.83 GB cap) | 228.088 | Feasibility only; no matched pair, so no ordering claimed at 10M |

## 2. What counts as "genuinely comparable" — and the verdict

A published measurement is genuinely comparable to a KindWindow measurement only if all
of these match: **same task** (binary forecast of any sensor event in a fixed 30-minute
window), **same target and unit of analysis** (30-minute windows anchored at event times,
not activity segments or named activities), **same metric** (Brier score and/or average
precision on probabilities), **same dataset edition and homes**, and **a compatible
split/horizon** (chronological, with leakage control). If any one of these differs, the
numbers live on different scales and must not be subtracted, ranked, or plotted on a
shared axis.

**Verdict: 0 of the 29 compared measurements are genuinely comparable.** Every
`kindwindow_comparable_value` cell in `reports/literature_comparison.csv` is therefore
blank, with the reason recorded per row. This is the honest outcome, not a failure of the
search: the packet's own literature bridge (packet `01`, "Literature bridge") already
warns that the Cook and Minor numbers "are background examples" and that one must "not
subtract an accuracy or a seconds-valued MAE from a Brier loss". A targeted search for a
published Brier score on a CASAS next-event binary forecast found no paper reporting one
on this task. KindWindow's only valid quantitative comparisons are therefore its
within-study baselines in §1.1, which share the task, split, homes, and metric exactly.

That said, the literature is not irrelevant: three of the papers share the data family,
the forecasting intent, or the evaluation vocabulary, and the comparison below says
exactly where each one stands relative to KindWindow — and where it does not.

## 3. Paper by paper

### 3.1 Minor, Doppa & Cook (KDD 2015) — the closest in forecasting intent

*Paper:* "Data-Driven Activity Prediction: Algorithms, Evaluation Methodology, and
Applications", KDD'15, DOI `10.1145/2783258.2783408`. *Verified against the author PDF
(`eecs.wsu.edu/~cook/pubs/kdd15.pdf`), Tables 3, 4, 6 and §5.*

Minor et al. predict the **time until each named activity recurs** — a multi-output
regression in seconds — from sensor data across 24 CASAS smart-home testbeds, using
activity labels produced by an activity-recognition (AR) algorithm that the paper says
yields 95% recognition accuracy via 3-fold cross-validation (§2). Their evaluation is a
sliding-window procedure (§5.2): train on a window of 2,000 events, predict the next
5,000 events, shift by 1,000 events, repeat.

| Method (Table 3) | MAE (s) | RMSE (s) | AUETF (Table 4) |
|---|---:|---:|---:|
| RAP (recurrent activity predictor) | 8,433.38 | 22,337.32 | 0.9091 |
| IP (independent predictor, baseline) | 19,050.85 | 173,501.54 | 0.8757 |
| Exponential baseline | 13,709.05 | 27,772.89 | 0.8673 |
| Oracle (ground-truth labels) | 2,686.47 | 12,758.97 | 0.9972 |

Table 3 values are averages across all datasets; a one-way ANOVA finds the differences
significant (p < .05). RAP's MAE of 8,433.38 s is about 2.3 hours; the paper notes about
55% of RAP errors fall below 15 minutes vs about 40% for IP (§5.3, Figure 4), and that
MAE is the better summary than RMSE because a few near-day-long errors dominate RMSE
(IP RMSE 173,501.54 s). Their live prompting deployment (CAFE, Table 6) achieves MAE
2,925 s (about 48 minutes) over two weeks in two homes (kyoto, navan), with ETF 0.64 at
a 30-minute threshold — but that figure includes roughly 15 minutes of infrastructure
delay and is a prompt-delivery measurement, not an offline forecast.

Where KindWindow sits relative to Minor: the *intent* is adjacent — both forecast the
future from sensor history, and both beat a recency/context-free baseline by a wide
margin (RAP MAE 8,433 s vs IP 19,051 s, a ~56% reduction; KindWindow Brier 0.1410 vs
recency 0.1846, a 23.6% reduction). But the numbers cannot be placed on one scale: a
seconds-valued MAE on named-activity recurrence times is not a Brier score on binary
30-minute windows, the units of analysis differ (activity segments vs windows), Minor's
predictions depend on activity labels (with their own ~5% recognition error) while
KindWindow uses none, and the splits differ (sliding 2,000/5,000-event windows vs a
single chronological 60/10/10/20 split with a 24 h purge). The 30-minute value appears
in both — Minor's CAFE ETF threshold and KindWindow's horizon — but it means different
things in each (an error tolerance vs a forecast window), so no comparison is drawn from
it.

### 3.2 Cook, Crandall, Thomas & Krishnan (Computer, 2013) — recognition, not forecasting

*Paper:* "CASAS: A Smart Home in a Box", *Computer* 46(7), 62–69, DOI
`10.1109/MC.2012.328`. *Verified against the author PDF
(`eecs.wsu.edu/~cook/pubs/computer12.pdf`), Table 2 and the paragraph following it.*

Cook et al. evaluate **activity recognition**: an SVM (their AR software) labels
observed sensor events with the activity in progress. On 18 single-resident apartments,
one month of data per apartment manually annotated, 5-fold cross-validation, the
**weighted average accuracy is 84%** (percentage of sensor events correctly labeled;
Table 2 is the confusion matrix). Accuracy varies strongly by activity — the paper notes
some activities have a distinctive spatial-temporal signature (e.g. cooking) while others
overlap or lack training data.

Where KindWindow sits: this is background, not a comparator. Recognition labels the
present/past; KindWindow forecasts a future window. An 84% accuracy on labelling
observed events says nothing about how well a 30-minute-ahead binary forecast should
score in Brier terms, and 1 − Brier is not an accuracy. The paper's value for KindWindow
is methodological: it documents the CASAS instrumentation and the AR pipeline whose
labels Minor et al. (and Dunne et al.) depend on — and it is a reminder that any
label-dependent method inherits recognition error that KindWindow's label-free target
avoids.

### 3.3 Dunne, Morris & Harper (2022) — closest dataset overlap, different task

*Paper:* "A semantic blocks model for human activity prediction in smart environments
using time-windowed contextual data", *Journal of Reliable Intelligent Environments*,
DOI `10.1007/s40860-022-00194-1`. *Verified against the publisher's open-access full
text (via `d-nb.info/1280118660/34`), Abstract and Tables 1, 5, 6; experiment materials
DOI `10.17632/jm28kgt7tm.1`.*

Dunne et al. predict the **next labeled activity** (multi-class, one activity label)
on CASAS homes HH101, HH102, and HH103 — three homes that are also in KindWindow's
20-home cohort — using a 90/10 train/test split per dataset (§2.2–2.3), with the training
table used on-the-fly as the historic observable data. Their semantic blocks model (SBM)
intersects contextual sets (current activity, preceding event, time window, day of week,
weekend/weekday, location) and returns the highest-scoring next activity.

| Method | HH101 (%) | HH102 (%) | HH103 (%) | Mean (%) |
|---|---:|---:|---:|---:|
| SBM, with contextual data (Table 5/6) | 65.0407 | 82.3991 | 76.2162 | 74.552 |
| CRAFFT benchmark (Table 1/6) | 64.18 | 74.57 | 61.98 | 66.91 |
| SBM, time window only (Table 5) | 3.2557 | 27.2411 | 9.8706 | 13.456 |

Two facts in this paper are useful as design evidence even though the numbers are not
comparable. First, the time window alone is nearly useless (13.456% mean) while the same
window plus contextual data reaches 74.552% — context carries the prediction, which is
consistent with KindWindow's own finding that a recency-only model (0.184562) is far
behind the full-feature forest (0.140965). Second, SBM beats CRAFFT on all three homes,
so the improvement is consistent rather than driven by one home — the same per-home
consistency KindWindow checks (19/20 homes vs recency).

For orientation only — **not a comparison** — KindWindow's test scores on those same
three homes are: hh101 Brier 0.153006 / AP 0.914627 (n = 3,521, prevalence 0.680);
hh102 Brier 0.128833 / AP 0.937750 (n = 9,902, prevalence 0.634); hh103 Brier 0.180075 /
AP 0.857530 (n = 20,651, prevalence 0.580). These are reported here so a reader can see
both sides, but no ranking against 74.552% or 66.91% is valid: the targets (named next
activity vs any event in 30 minutes), the units (activity segments vs windows), the
metrics (accuracy vs Brier/AP), and the splits (90/10 vs chronological 60/10/10/20 with
a 24 h purge) all differ, and Dunne et al. report no probabilities from which a Brier
score could be derived.

### 3.4 Woodford & Ghandour (2025) — not verified, nothing compared

*Paper:* "Investigating activity recognition in smart homes through embedded feature
selection", *Journal of Ambient Intelligence and Humanized Computing* 16(6–7), 699–712,
DOI `10.1007/s12652-025-04971-7` (published online 14 May 2025; closed access).

This is the packet's subscription candidate for TA review (packet `01`, contingency 1).
No number from it is recorded in the comparison table, because none could be verified
from an accessible primary source: the Springer article page returned references only
(no abstract or results text); the University of Otago record returned HTTP 403; and
both OpenAlex and Crossref carry bibliographic metadata only (no abstract, no result
values). The Springer description confirms the topic (variations in activity-sequence
length, per-sensor contribution to specific activities, and activity-class imbalance as
challenges for activity-recognition classification), but the dataset, metric, split, and
values are unverified, so the paper is recorded as **unverified — no values recorded**
rather than summarised from a secondary source. It remains a TA-review candidate only;
if the TA asks for it, access through the UNT library and a full paper card (exact
method, inputs, split, table, metric) must come first.

### 3.5 Selective prediction (El-Yaniv & Wiener 2010; Geifman & El-Yaniv 2017) — vocabulary, not a measurement

*El-Yaniv & Wiener, "On the Foundations of Noise-free Selective Classification", JMLR
11(53):1605–1641, 2010 — verified against the JMLR abstract. Geifman & El-Yaniv,
"Selective Classification for Deep Neural Networks", NeurIPS 2017 — verified against
the NeurIPS proceedings abstract.*

These papers supply the vocabulary KindWindow's abstention analysis uses: a selective
classifier trades **coverage** for lower **risk** (the risk–coverage trade-off), and
Geifman & El-Yaniv's SGR method chooses a threshold that guarantees a desired risk with
high probability — e.g. 2% top-5 error on ImageNet guaranteed with probability 99.9%
at almost 60% test coverage. KindWindow's abstention is structurally the same trade
(coverage 0.4871 at 50% retention, macro Brier 0.069071 vs 0.140965 at full coverage),
but it is an **observed** selective Brier on sensor windows, not a **guaranteed** error
rate, and the loss (Brier vs 0/1 error) and domain (smart-home windows vs image
classification) differ. So the framework is correctly attributed and the numbers are
not compared. The distinction matters for the report: KindWindow can say abstention
halved the observed Brier at ~49% coverage on its test split; it cannot claim a
guarantee of the Geifman type.

## 4. Where KindWindow sits — the summary

| Dimension | KindWindow position |
|---|---|
| Forecast task | 30-min-ahead binary any-event forecast, no activity labels required. The only paper with the same *intent* is Minor et al. (2015), but as a regression on named-activity times, not a binary window forecast. |
| Headline quality | Macro Brier 0.140965, macro AP 0.805909 (test, n = 206,953). No published Brier/AP exists for this task, so there is no external bar to clear; the internal bar is the recency baseline (0.184562), beaten by 0.043597 (23.6%) in 19/20 homes. |
| Calibration | Sigmoid calibration on a held-out segment helps only slightly (0.141939 → 0.140965). No literature comparator reports calibration on this task. |
| Abstention | Observed selective Brier 0.069071 at coverage 0.4871. Same risk–coverage shape as Geifman & El-Yaniv (2017), but observed rather than guaranteed and on a different loss/domain — no numeric comparison. |
| Robustness | Near-insensitive to losing 30% of events (+0.001595 Brier, seed 2207); long-gap windows are disproportionately easy, so excluding them raises Brier to 0.162033. No literature comparator reports either stress. |
| Systems (RQ2) | pandas faster than Spark at 1M (44.154 s vs 60.790 s) and at the real 78.3M-event cohort (109.847 s vs 305.412 s); pandas cannot complete 10M JSONL events under the 70% RAM cap while Spark completes in 228.088 s. No external matched benchmark exists (see §5). |
| Data overlap | Homes hh101–hh103 overlap with Dunne et al. (2022); the CASAS data family overlaps with Minor et al. (2015) and Cook et al. (2013). Overlap of homes/data does not make the measurements comparable — see §5. |

## 5. What is NOT comparable — stated explicitly

- **Minor et al. MAE/RMSE (seconds) vs KindWindow Brier.** Different task (time to next
  named activity vs binary 30-minute window), different metric units, different unit of
  analysis, label-dependent vs label-free, sliding-window vs chronological split. Do not
  subtract 8,433.38 s from 0.140965, and do not convert MAE into an accuracy.
- **Minor et al. AUETF vs KindWindow AP.** Both are areas under a curve, but over
  different things (error thresholds in seconds vs precision–recall). AUETF 0.9091 and
  AP 0.805909 are not on a shared scale.
- **Minor et al. CAFE prompt MAE 2,925 s vs anything in KindWindow.** A live prompting
  evaluation with infrastructure delays in two homes outside the cohort. KindWindow has
  no prompting deployment.
- **Cook et al. 84% recognition accuracy vs KindWindow Brier/AP.** Recognition of the
  present vs forecasting of the future; accuracy vs Brier; 5-fold CV vs chronological
  split. 1 − Brier is not an accuracy.
- **Dunne et al. SBM 74.552% / CRAFFT 66.91% accuracy vs KindWindow Brier/AP.** Next
  labeled activity (multi-class) vs binary any-event window; accuracy vs Brier/AP; 90/10
  split vs chronological 60/10/10/20 with 24 h purge; no probabilities reported. The
  shared homes (hh101–hh103) do not rescue the comparison.
- **Woodford & Ghandour (2025).** Unverified; no number is compared at all.
- **Geifman & El-Yaniv (2017) 2% top-5 error at ~60% coverage vs KindWindow selective
  Brier 0.069071 at coverage 0.4871.** Different domain, loss, and guarantee-vs-observation
  status. Framework only.
- **Systems (RQ2).** The packet's systems sources (pandas scaling guide, Spark SQL
  performance tuning guide, Arrow Dataset API, NumPy `searchsorted`) are documentation,
  not measurements: they prescribe chunking, column projection, and partitioning, but
  report no runtime or memory number on a matched workload and machine. KindWindow's
  pandas-vs-Spark timings therefore have no external measured comparator, and none is
  invented here. In particular, no published pandas/Spark benchmark on a different
  machine or workload is imported as if it were a matched result.
- **Cross-home generalisation.** All literature numbers above are within-home or
  within-testbed. KindWindow likewise trains and tests on the same 20 homes
  (per-home chronological splits); no paper here supports a claim about unseen homes,
  and none is made.

## 6. Limits of this comparison

1. **No external Brier/AP anchor exists for this task.** The comparison is therefore
   contextual by necessity. The strongest evidence in the study is internal: the frozen
   model vs its three baselines on the same sealed test split.
2. **Reduced Step 11 scope propagates here.** Gap sensitivity covers only the 12 h gap
   (not 6 h/24 h); degradation covers only 30% events gone, seed 2207 (not 10%, not 25%
   sensors, not the other four seeds); abstention covers only 50%/100% retention (not
   75%). Any literature comparison involving those conditions inherits those limits.
3. **Woodford & Ghandour (2025) is unverified.** If the TA wants it pursued, the next
   step is library access and a full paper card — not a number carried from a summary.
4. **Dunne et al. is a proof-of-concept** (the authors say so: fixed time windows
   "may not have been tuned perfectly", partial label matches counted as incorrect).
   Its 74.552% should be read as a prototype result, as should any contextual use of it.
5. **Minor et al.'s labels carry recognition error** (AR ~95% per §2). Their MAE
   figures are measured against labels that are themselves predictions; KindWindow's
   labels are read directly off the event stream, so the two error budgets are not the
   same kind of thing.
6. **Verification boundary.** Every number in the CSV was checked against its primary
   source on 22 November 2026 (author PDFs for Minor and Cook; publisher open-access
   full text for Dunne; proceedings/JMLR abstracts for the selective-prediction papers;
   metadata records only for Woodford). No page or table number is asserted that was
   not seen in that source. Two Minor values (share of errors below 15 minutes, ~55%
   RAP / ~40% IP) are approximate text values, marked as such; everything else is an
   exact table value.

## 7. What a valid future comparison would need

A future paper could be genuinely compared to KindWindow if it: (a) forecasts the same
binary target (any sensor event in a fixed forward window, ideally 30 minutes) on
CASAS homes, (b) reports Brier score and/or average precision on probabilities (binary
convention, mean of per-home scores stated), (c) uses a chronological split with
leakage control comparable to the 24 h purge, and (d) states n, prevalence, and
per-home scores. Until such a paper exists, the correct external statement is the one
in §2: no genuinely comparable published measurement was found, and the literature's
role is context — forecasting intent (Minor), recognition background (Cook), same-home
next-activity accuracy (Dunne), and the risk–coverage vocabulary (El-Yaniv & Wiener;
Geifman & El-Yaniv).

## References (primary sources checked)

- D. Cook. CASAS Smart Home dataset — free living, motion, door, activity labels, v1,
  2025. DOI `10.5281/zenodo.15708568` (CC BY 4.0). Dataset used by KindWindow.
- D. Cook, A. Crandall, B. Thomas, N. Krishnan. "CASAS: A Smart Home in a Box",
  *Computer* 46(7), 62–69, 2013. DOI `10.1109/MC.2012.328`. Author PDF:
  `https://eecs.wsu.edu/~cook/pubs/computer12.pdf` (Table 2; weighted average accuracy
  84% in the paragraph following Table 2).
- B. Minor, J. R. Doppa, D. J. Cook. "Data-Driven Activity Prediction: Algorithms,
  Evaluation Methodology, and Applications", KDD'15, 2015. DOI
  `10.1145/2783258.2783408`. Author PDF: `https://eecs.wsu.edu/~cook/pubs/kdd15.pdf`
  (Table 3 MAE/RMSE; Table 4 AUETF; Table 6 CAFE; §5.2 sliding-window procedure).
- R. Dunne, T. Morris, S. Harper. "A semantic blocks model for human activity
  prediction in smart environments using time-windowed contextual data", *Journal of
  Reliable Intelligent Environments*, 2022. DOI `10.1007/s40860-022-00194-1`.
  Open-access full text via `https://d-nb.info/1280118660/34` (Abstract; Tables 1, 5, 6;
  §2.2–2.3 90/10 split). Experiment materials DOI `10.17632/jm28kgt7tm.1`.
- B. J. Woodford, A. Ghandour. "Investigating activity recognition in smart homes
  through embedded feature selection", *Journal of Ambient Intelligence and Humanized
  Computing* 16(6–7), 699–712, 2025. DOI `10.1007/s12652-025-04971-7`. Closed access;
  bibliographic metadata only (Springer article page; OpenAlex; Crossref). No result
  values verified — see §3.4.
- R. El-Yaniv, Y. Wiener. "On the Foundations of Noise-free Selective Classification",
  *JMLR* 11(53):1605–1641, 2010. `https://jmlr.csail.mit.edu/papers/v11/el-yaniv10a.html`
  (Abstract: risk–coverage trade-off).
- Y. Geifman, R. El-Yaniv. "Selective Classification for Deep Neural Networks",
  NeurIPS 2017. `https://proceedings.neurips.cc/paper/2017/hash/4a8423d5e91fda00bb7e46540e2b0cf1-Abstract.html`
  (Abstract: SGR, 2% top-5 ImageNet error guaranteed w.p. 99.9% at ~60% coverage).

KindWindow sources for §1: `reports/tables/test_scores.csv`,
`reports/tables/step11_summary.md`, `reports/benchmarks/README.md`,
`reports/benchmarks/summary.csv`, `docs/freeze-receipt.md`,
`reports/tables/selection_scores.csv`, `configs/study.json`.
