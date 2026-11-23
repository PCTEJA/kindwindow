# Course decision — KindWindow (redacted)

## Decision

The TA accepted the KindWindow proposal. Confirmed by the student, Teja Piepur Chakravarthi, on **22 November 2026**.

**No TA correspondence is in the handoff packet, and none is reproduced here.** This record is based only on the student-confirmed acceptance and the proposal / brief files in the handoff kit. No private correspondence, Canvas credentials, or Canvas session material is included.

## Proposal scope (accepted)

- Course: UNT CSCE 5300 Introduction to Big Data and Data Science, Fall 2026
- Project: KindWindow — Household Event Prediction and Scalable Sensor Data Processing
- Data: CASAS Smart Home dataset v1, DOI `10.5281/zenodo.15708568`; only `data.zip`, up to 20 documented single-resident older-adult homes, ≥90 days of interpretable logs, target ≥10 homes
- RQ1: recent sensor features vs constant-rate / hour-of-week / recency baselines for 30-minute event forecasts
- RQ2: PySpark vs pandas processing time at tested event volumes on the same machine
- RQ3: abstention under degraded inputs — predictive loss vs coverage lost
- Effort: 75 hours total (8 audit/scope, 17 processing, 16 modeling, 12 experiments, 10 interface/parser, 12 reporting/reproducibility)

## Dates — conflict to resolve against Canvas

The standalone course brief (undated, no year in its instructions) lists:

- Sept 30 — topic approval, 23:59
- Oct 31 — draft PDF, 23:59
- Nov 8 — final upload, 23:59

The accepted proposal assigns:

- Oct 24–30 — TA topic approval, Canvas requirements, data audit, cohort/label freeze
- Oct 31–Nov 6 — parsing, quality summaries, Parquet storage, exploratory plots, baselines
- Nov 7–13 — model selection, calibration, fixed abstention configurations
- Nov 14–20 — core benchmarks, robustness tests, preference parser, local demonstration
- Nov 21–29 — freeze results, rerun documented workflow, finalize report, slides, and recorded demonstration
- Week 16 — presentation, on the date confirmed in Canvas

These do not agree. Record the current Canvas destination, date, time zone, and required formats at submission time. Never backdate work or infer an extension. These date checks do not block implementation.

## What remains

- Confirm group number, approved paper/exception (if any), deadlines, presentation format, and assistance rules from current Fall 2026 Canvas / a written TA ruling.
- If a current addendum explicitly requires an additional paper replication, that is a separate track (see `01_COURSE_AND_REPORT.md` in the kit); it is not activated by the brief alone.
- Student owns submission unless separately authorized; a submission receipt / Turnitin value can only be recorded from the actual authenticated course action.
