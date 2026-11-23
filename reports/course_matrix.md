# KindWindow — course matrix (Step 15)

Every item the packet (`01_COURSE_AND_REPORT.md`, `03_DELIVERY_PLAN.md`,
`07_DEMO.md`) requires for the final delivery, marked PRESENT, PARTIAL, or
OMITTED with a reason. Sources are repo paths unless noted. "Packet" means
the handoff kit at `~/workspace/kindwindow-kit` (not in the repo).

## 1. Report structure (packet 01, "Report map" — from the report sample)

| # | Required item | Status | Location / note |
|---|---|---|---|
| 1.0 | Cover and table of contents (UNT, CSCE 5300, offering, verified group number, author/team, full title) | PARTIAL | `reports/report.md` header + contents. **Group number is OPEN** — not in the packet, not confirmed on Canvas; no group number is asserted. Offering/author/title present. Page references are section references (Markdown source; no PDF pagination exists yet — see §4). |
| 1.1 | §1 Abstract (question, cohort size/dates, comparison, measured Brier difference, processing outcome, one key limitation) | PRESENT | `reports/report.md` §1 |
| 1.2 | §2 Introduction (motivation, research gap, contribution, CASAS citation, resident control) | PRESENT | `reports/report.md` §2 |
| 1.3 | §3 Problem statement and existing methods (problem in original words, RQ1–3, event unit, observation vs availability, competing methods, scope, prior strengths + comparable quantitative evidence) | PRESENT | `reports/report.md` §3; quantitative literature evidence in §6.6 + `reports/method_comparison.md` |
| 1.4 | §4 Method/algorithm (acquisition, observation filters, target, strict chronology, estimators, calibration, abstention, equivalent processing, text parsing, visualization; readable original code listings on a light background at body font size) | PRESENT | `reports/report.md` §4. Code listing is short and illustrative; full source is in the repo. "Light background / body font size" is a PDF-typesetting property — applies when the PDF is built (see §4). |
| 1.5 | §5 Strengths, weaknesses, applications (interpretability, unequal history, missingness, single-resident selection, known-home generalisation, paper-method limits, resource ceilings, consent; future invitation utility explicitly untested) | PRESENT | `reports/report.md` §5 (utility explicitly untested) |
| 1.6 | §6 Own simulations vs paper results (matched published/reproduced values where authorised; measured RQ1, RQ2, RQ3, parser results, demo tests in distinct subsections; native tables, exported vector plots; source figures credited) | PARTIAL | `reports/report.md` §6. RQ1/RQ2/RQ3, demo tests present with native tables + vector SVG plots. Parser results: **OMITTED** (held-out notes-eval not run — see §2, T7). Matched published/reproduced values: none exist (0 comparable — §6.6); no figure is fabricated for them. No source figures are reproduced. |
| 1.7 | §7 Conclusion (each RQ answered incl. null findings, unresolved discrepancies, evidence limits, future work) | PRESENT | `reports/report.md` §7 |
| 1.8 | §8 References (numbered IEEE-style, in order of first use, square-bracket citations; data version, primary studies, method/software docs, assistance statement) | PRESENT | `reports/report.md` §8 (IEEE-style, order of first use; assistance statement included) |

## 2. Evidence exhibits (packet 01, "Evidence to generate" — T1–T9, F1–F8)

Each exhibit needs a generating command, input hash, run ID, code revision,
units, denominator, and a caption. Commands are given in `reports/report.md`
§6 and in `scripts/reproduce.py` / `scripts/make_figures.py`.

| ID | Required exhibit | Status | Location / note |
|---|---|---|---|
| T1 | Cohort flow and audit | PRESENT | `reports/report.md` §6.1 Table T1; `docs/cohort-audit.md`; `data/manifests/cohort.csv` |
| T2 | Splits and prevalence by home | PRESENT | `reports/report.md` §6.1 Table T2; `reports/tables/split_summary.csv`; `reports/tables/test_scores.csv` |
| T3 | Model selection | PRESENT | `reports/report.md` §6.2 Table T3; `reports/tables/selection_scores.csv`; `docs/freeze-receipt.md` |
| T4 | Paired test loss and AP | PRESENT | `reports/report.md` §6.2 Table T4; `reports/tables/test_scores.csv`. **Bootstrap CIs OMITTED** — the packet's exploratory paired household-bootstrap intervals (5,000 draws) were not computed; no interval is claimed. |
| T5 | Degradation and retained denominators | PRESENT | `reports/report.md` §6.3 Table T5; `local/runs/step11/results.json` (local, hashed in `reports/evidence.csv`) |
| T6 | Pipeline parity and all timed attempts | PRESENT | `reports/report.md` §6.4 Table T6; `reports/benchmarks/attempts.csv`, `summary.csv`, `parity.json` |
| T7 | Held-out note extraction | OMITTED | Notes-eval on the 20 held-out notes was **not run** (reduced Step 11 scope). Parser dev result (40/40 exact) is stated in `reports/report.md` §6.5; no held-out precision/recall/exact-match exists. |
| T8 | Demo access and latency | PRESENT | `reports/report.md` §6.7 Table T8; `demo/demo_results.json`, `demo/demo_log.txt`, `demo/latency_samples.csv`; `docs/demo.md` |
| T9 | Paper replication if applicable | OMITTED (not applicable) | No replication track: the packet's contingency applies only if a current course addendum expressly requires one; none is in the packet. Recorded in `docs/course-decision.md` and `reports/method_comparison.md` §1. Woodford & Ghandour (2025) remains a TA-review candidate only (unverified, closed access). |
| F1 | Dataflow | PRESENT | `reports/report.md` §4.1 (text dataflow diagram). No separate diagram file. |
| F2 | Aggregate training hourly counts | PARTIAL | Table present: `reports/tables/training_eda.csv` (500 rows). No F2 plot was drawn; the table is the exhibit. |
| F3 | Per-home Brier differences | PRESENT | `reports/figures/per_home_brier.svg`; verified by `python3 scripts/make_figures.py --check` (PASS) |
| F4 | Reliability with bin sizes | OMITTED | Reliability bins were not produced (no `reliability_bins.csv` exists); calibration is evidenced only by raw-vs-calibrated Brier (0.141939 → 0.140965). Not measured, not plotted. |
| F5 | Risk against achieved coverage | PRESENT | `reports/figures/risk_coverage.svg`; `python3 scripts/make_figures.py` from `reports/tables/test_scores.csv`. Only measured retentions (50%, 100%) plotted; 75% not interpolated. |
| F6 | Runtime vs event count | PRESENT | `reports/figures/runtime_vs_events.svg`; `python3 scripts/make_figures.py` from `reports/benchmarks/summary.csv` |
| F7 | Memory vs event count | PRESENT | `reports/figures/memory_vs_events.svg`; `python3 scripts/make_figures.py` from `reports/benchmarks/summary.csv` |
| F8 | Comparable paper results | OMITTED (nothing to plot) | 0 of 29 measurements genuinely comparable, so no shared-axis figure is valid. The verdict and per-row reasons are in `reports/method_comparison.md` + `reports/literature_comparison.csv`. Fabricating a comparison plot would misrepresent the literature. |

## 3. Delivery plan gates (packet 03, Step 15 row)

| Required item | Status | Location / note |
|---|---|---|
| Redacted artifacts (no real CASAS rows, heldout labels, PII, secrets, tokens) | PRESENT | Verified by inspection in Step 15: report, release notes, evidence index, scripts, and figures contain only aggregates, hashes, and synthetic fixture values. `data/heldout/` is never read by any Step 15 script. |
| Course matrix complete | PRESENT | This file. |
| Fresh-clone fixture + verified-data rerun | PRESENT | `docs/release.md` §"Fresh-clone verification" and §"Verified-data rerun". Fixture + demo pass; one pytest failure is environmental (kit-sibling path) — documented there. |
| Checksums | PRESENT | `reports/evidence.csv` (SHA-256 for every committed file + local artifacts + archive); `python3 scripts/build_evidence.py --check`. |
| Evidence index | PRESENT | `reports/evidence.csv`. |
| Committed sources | PRESENT | All Step 15 deliverables are local, uncommitted working-tree files for the coordinator to review and commit (Step 15 worker commits nothing). |
| Figure/table commands | PRESENT | Every figure/table in `reports/report.md` names its regenerating command; `scripts/reproduce.py`, `scripts/make_figures.py`, `scripts/build_evidence.py`. |
| Final verified PR ledger | PRESENT | `docs/release.md` — all 17 commits base→HEAD with identity-guard status (all PASS, PCTEJA id 39472754). Note: this release itself is uncommitted local work; its own PR/merge record must be added by the coordinator after merge. |

## 4. Submission artefacts (packet 01, "Delivery")

| Required item | Status | Location / note |
|---|---|---|
| Report source | PRESENT | `reports/report.md` (Markdown source). |
| Report PDF | OMITTED (not built) | No PDF build toolchain is pinned in the repo and no PDF was produced in any step. The Markdown source is the deliverable; PDF export (with cover, pagination, light-background code listings) is a presenter step at submission time. No PDF is asserted to exist. |
| Editable slide deck | PRESENT | `slides/slides.md` (Markdown, one slide per `---`). |
| Slide PDF / PowerPoint export | OMITTED (naming OPEN) | Export naming follows the Canvas/group-number convention; **the group number was not in the packet** (checked again in Step 15 — packet 01 says only "Obtain the exact naming convention ... from Canvas"). Recorded as OPEN in `docs/release.md`. No group number is invented and no `.ppt`/`.pptx` filename is asserted. |
| Actual local demo or recording | PARTIAL | Working local demo + captured log/results/latency samples: `demo/run_demo.py`, `demo/demo_log.txt`, `demo/demo_results.json`, `demo/latency_samples.csv`. **No screen recording** — the VM is headless, so a video would have been staged rather than real. A recorded fallback is left for the presenter to produce on the presentation laptop (`docs/demo.md`). |
| Turnitin value ≤ 30% | OPEN | Only the actual Canvas result can be recorded; none exists yet. To be recorded privately at submission. |
| Submission confirmation / timestamp / score | OPEN | To be retained privately by the student at submission. Student owns submission unless separately authorised. |
| Presentation ≤ 15 min + ≤ 5 min Q&A; each team member presents their contribution | PRESENT (as planned) | `slides/slides.md`: 15-min talk + backup, single presenter Teja Piepur Chakravarthi; Q&A owner the same. No other speakers are named — if teammates are confirmed, assign sections by name before the talk. |
| Original charts instead of screenshots | PRESENT | All report figures are SVG generated from committed tables (`scripts/make_figures.py`) or the verified hand-drawn SVG (F3). No screenshots in the report. |

## 5. Course-administration checks (packet 01)

| Required item | Status | Note |
|---|---|---|
| TA acceptance of the KindWindow proposal | PRESENT (student-confirmed) | `docs/course-decision.md`: accepted, confirmed by Teja 22 Nov 2026. No TA correspondence in the packet; none reproduced. |
| Group number | OPEN | Not in the packet; confirm from current Fall 2026 Canvas / written TA ruling. Blocks only the export filenames (§4), not the work. |
| Approved paper / exception (if any) | OPEN | None in the packet. The replication contingency (packet 01) is not activated; if a current addendum requires one, Woodford & Ghandour (2025) is the candidate for TA review (unverified). |
| Deadlines / Canvas destination / timezone / required formats | OPEN | Brief dates (Sept 30 / Oct 31 / Nov 8) conflict with the proposal schedule (Oct 24–30 / Nov 21–29 / Week 16). Record current Canvas facts at submission; never backdate; no extension inferred. |
| Presentation and assistance rules | OPEN | Confirm from Canvas / TA ruling. Assistance statement is in `reports/report.md` §8. |
| Current Canvas check for administrative details | OPEN | Student-side check at submission time (requires institutional session; not performed by this worker). |

## 6. Omitted items — consolidated

1. T7 held-out note extraction — not run (reduced Step 11 scope).
2. F4 reliability diagram — not produced (no reliability bins saved).
3. F8 comparable-paper figure — nothing valid to plot (0 comparable).
4. T9 paper replication — not applicable (no addendum requires it).
5. T4 bootstrap intervals — not computed (exploratory suggestion only).
6. F2 plot — table only, no plot drawn.
7. Report PDF — not built (no pinned toolchain; presenter step).
8. Slide PDF/PowerPoint export — naming OPEN (group number unknown).
9. Screen recording — not produced (headless VM; working demo + log is the evidence).
10. Turnitin value, submission receipt, Canvas facts — OPEN until submission.
11. Gap 6 h/24 h, degradation 10%/25%-sensors/other seeds, abstention 75% — not run (reduced Step 11 scope); listed in `reports/report.md` §6.8.
