# KindWindow — slides (Step 14)

> Editable slide-deck source (Markdown, one slide per `---`).
> 15-minute class talk + up to 5 minutes Q&A (packet 07 timing).
> Presenter: **Teja Piepur Chakravarthi** presents every section;
> Q&A owner: **Teja Piepur Chakravarthi**. No other speakers are named:
> if a teammate is confirmed for the course presentation, assign them a
> section here by name at that point — do not present anyone else's part
> without that confirmation. Backup slides follow the closing slide.
> PDF/PowerPoint export naming follows the Canvas/group-number convention
> (Step 15); the group number was not in the packet, so no filename is
> asserted here.
>
> All numbers are the verified frozen results (Steps 11–13); nothing here
> was re-derived. Real-study results are shown only as aggregates.
> The live demo (slides 10–13 segment) uses synthetic fixtures only.

---

# KindWindow

## Household event prediction and scalable sensor-data processing

**Teja Piepur Chakravarthi — UNT CSCE 5300, Fall 2026**

30-minute-ahead forecast of any sensor event in previously observed
CASAS smart homes — and what it costs to compute at scale.

*Presenter: Teja Piepur Chakravarthi (all sections) · Q&A owner: Teja Piepur Chakravarthi*

---

# 0–2 min · Problem

- A resident wants to invite a contact at a good time — but only the
  resident decides, and only in hours they chose.
- The research question underneath: can recent sensor history forecast
  whether *any* sensor event happens in the next 30 minutes?
- The forecast is a probability, never a decision: approval is always an
  explicit resident action, and it works without the probability.
- Nothing is inferred about health, consent, or willingness from sensors.

*Presenter: Teja Piepur Chakravarthi*

---

# 2–5 min · Cohort and methods

- **Data:** CASAS Smart Home v1 (Cook), DOI 10.5281/zenodo.15708568.
  Only `data.zip` is used; no activity labels anywhere — the target comes
  from the raw sensor-event stream alone.
- **Cohort:** 20 single-resident homes (aruba, hh101–hh122), ≥ 90 days each.
- **Split:** per-home chronological 60 / 10 / 10 / 20 with a 24 h purge
  on both sides of each boundary.
  n_train = 622,817 · n_selection = 102,023 · n_calibration = 102,026 ·
  n_test = 206,953 (107,232 positives, prevalence 0.518).
- **Target:** half-open `[t, t+30m)` — an event exactly at `t+30m` belongs
  to the *next* window (the golden-fixture demo shows this live).
- **Model:** frozen random forest, 200 trees, depth 10, leaf 10,
  seed 2718, sigmoid-calibrated on the calibration split only.
  Test was scored exactly once, after the freeze.
- **Baselines:** per-home constant, hour-of-week, recency-only logistic.

*Presenter: Teja Piepur Chakravarthi*

---

# 5–8 min · RQ1 — Does recent history help?

Frozen forest (calibrated) vs baselines, test-once, macro Brier (lower is better):

| Model | Macro Brier | Macro AP |
|---|---:|---:|
| **Frozen forest, calibrated** | **0.1410** | **0.8059** |
| Frozen forest, raw | 0.1419 | 0.8059 |
| Recency baseline | 0.1846 | 0.7878 |
| Hour-of-week baseline | 0.2256 | 0.6087 |
| Constant baseline | 0.2372 | 0.5116 |

- Paired improvement vs the strongest baseline (recency): **+0.0436
  (23.6%)** in macro Brier.
- Frozen beats recency in **19/20 homes** — sole exception hh119
  (0.0932 vs 0.0928).
- Best home hh116 (0.0573), worst hh114 (0.2057). Calibration helps
  slightly (raw 0.1419 → calibrated 0.1410).

**Answer to RQ1: yes — recent history improves the forecast well beyond
every baseline, on the same homes and keys.**

*Presenter: Teja Piepur Chakravarthi*

---

# 5–8 min · RQ3 — Abstention: withhold the uncertain ones

Frozen gates (`u = p_raw·(1−p_raw)`, low values retained), test:

| Retention | Retained / eligible | Coverage | Macro Brier on retained |
|---:|---:|---:|---:|
| 50% | 100,812 / 206,953 | 0.4871 | **0.0691** |
| 100% | 206,953 / 206,953 | 1.0000 | 0.1410 |

- Withholding the most uncertain half cuts the loss on what is kept
  by more than half (0.1410 → 0.0691) — at the price of answering
  only ~49% of windows.
- The trade-off is the point: abstention buys accuracy with coverage,
  and the coverage is measured, not assumed (nominal 50% → actual 0.4871).

*Presenter: Teja Piepur Chakravarthi*

---

# 5–8 min · RQ3 — Robustness under degraded inputs

Same frozen model, same test split:

- **30% of events gone (seed 2207):** macro Brier **0.1426** vs 0.1410
  clean (**+0.0016**) — nearly insensitive to losing almost a third
  of the event stream. 23,475,783 of 78,275,425 events dropped; all
  206,953 windows rescored, 0 missing.
- **12 h gap:** 30,447 windows touch an event-free gap over 12 h;
  excluding them gives Brier **0.1620** on the 176,506 kept — the kept
  set is *harder* (prevalence 0.604 vs 0.518), so this is a different,
  harder subset, not a worse model.

**Answer to RQ3: yes — abstention reduces loss on retained predictions,
and the model is robust to substantial event loss.**

*Presenter: Teja Piepur Chakravarthi*

---

# 8–10 min · RQ2 — Systems: pandas vs Spark

One machine (AMD EPYC 9D25, 4 cores, 8.32 GB RAM, 70% RAM cap),
medians of n = 3, parity-passing pairs only:

| Workload | pandas | Spark | Outcome |
|---|---:|---:|---|
| Synthetic 1M events | 44.2 s | 60.8 s | pandas faster (Spark 1.38× slower) |
| Real cohort, 78,275,425 events | 109.8 s | 305.4 s | pandas faster (Spark 2.78× slower) |
| Synthetic 10M events | 0/3 (memory limit, ~5.83 GB cap) | 228.1 s | Feasibility only — no matched pair, no ordering claimed |

- Spark's JVM startup (~9.0 s vs pandas ~0.6 s) accounts for about half
  of the 16.6 s gap at 1M — and even excluding startup entirely, Spark
  is still ~8 s slower there (51.8 s vs 43.5 s net of startup).
- At 10M, pandas hits the memory cap and Spark completes: Spark's value
  here is feasibility, not speed.

**Answer to RQ2: on this machine, Spark is never faster at the tested
volumes where a comparison exists; it is the only engine that finishes
at 10M.**

*Presenter: Teja Piepur Chakravarthi*

---

# 10–13 min · Live demo (1): the pipeline

Run live from a fresh checkout (`demo/run_demo.py`):

- `kindwindow doctor` — release commit, golden fixture present.
- Golden fixture through the clean pandas pipeline: 9 events → 3 windows.
- **Why the 10:30 event belongs to the next forecast:** the target is
  half-open `[t, t+30m)`. The 10:30 motion ON sits at the end of the
  10:00 window's target, so the 10:00 window has label 0 and the 10:30
  window has label 1.

*Presenter: Teja Piepur Chakravarthi — live terminal*

---

# 10–13 min · Live demo (2): Rosa and Contact A

Synthetic resident **Rosa** and synthetic **Contact A / Contact B**
(no real people, no real sensor data):

1. Propose with no preferences → 409, nothing invented.
2. Set weekdays 09:00–17:00, quiet 12:00–13:00.
3. Parse a note → pending; **cancel once** (nothing changes), parse
   again → **confirm** (Monday 09:00–12:00, quiet 10:00–10:30 goes live).
4. Quiet / outside-hours / wrong-day slots refused (409); ungranted
   contact refused (403).
5. Propose Mon 11:00 → pending (Contact A sees nothing); duplicate
   replay returns the same invitation; approve → Contact A sees one
   whitelisted invitation, Contact B sees nothing. The resident-only
   experimental probability (0.42) never reaches contacts.
6. Postpone needs fresh approval; reject cannot be revived; pause
   invalidates approved invitations; revoke is immediate and idempotent;
   delete removes everything (0 remaining).

*Presenter: Teja Piepur Chakravarthi — live app on loopback*

---

# 10–13 min · Live demo (3): latency

Measured live in the demo (10 warm-ups + 100 measured calls per
endpoint, concurrency 1, request start → response fully read):

| Endpoint | p50 | p95 |
|---|---:|---:|
| GET / | 1.64 ms | 2.24 ms |
| GET /preferences | 2.81 ms | 3.38 ms |
| GET /invitations | 3.01 ms | 3.77 ms |
| GET /contact/invitations | 3.29 ms | 4.26 ms |
| POST /preferences | 3.33 ms | 3.84 ms |

Packet budget: p95 ≤ 200 ms. **Worst measured p95: 4.26 ms — far inside
budget.** Cold launch to ready: 0.550 s. Raw samples:
`demo/latency_samples.csv`. Full protocol: `docs/demo.md`.

*Presenter: Teja Piepur Chakravarthi*

---

# 13–15 min · Limitations, replication, conclusion

**Limitations (stated, not buried):**

- Known homes only — no claim about unseen homes; single-resident
  cohort; per-home results vary widely (hh116 0.0573 → hh114 0.2057).
- Reduced Step 11 scope: gap 6h/24h, degradation 10% / 25%-sensors /
  other seeds, abstention 75%, and notes-eval were **omitted**, not measured.
- No external anchor exists: 29 literature measurements across 6 papers,
  **0 genuinely comparable** (different task/metric) — within-study
  baselines are the valid comparison (Step 13).
- Invitation *utility* (whether forecasts help real scheduling) is
  untested; the demo shows consent mechanics, not outcomes.
- Demo latency is one run on one VM; systems results are one machine.

**Replication:** frozen model + config + split hashes in
`docs/freeze-receipt.md`; evidence index and reproduction scripts are
Step 15. The demo re-runs from a fresh checkout with one command.

**Conclusion:** recent history forecasts the next 30 minutes well
(RQ1: Brier 0.1410 vs 0.1846 recency); abstention trades coverage for
accuracy and the model survives 30% event loss (RQ3); on this machine
pandas beats Spark where both finish, and Spark alone finishes at 10M
(RQ2) — with the resident, not the model, in control of every invitation.

*Presenter: Teja Piepur Chakravarthi*

---

# Backup — Per-home Brier (frozen calibrated vs recency)

Best hh116 (0.0573), worst hh114 (0.2057); sole recency win hh119
(0.0932 vs 0.0928). Full table: `reports/tables/step11_summary.md`,
`reports/tables/test_scores.csv`. Figure: `reports/figures/per_home_brier.svg`.

| Home | n | Frozen | Recency |
|---|---:|---:|---:|
| aruba | 2,062 | 0.1301 | 0.1960 |
| hh101 | 3,521 | 0.1530 | 0.1865 |
| hh102 | 9,902 | 0.1288 | 0.1807 |
| hh103 | 20,651 | 0.1801 | 0.2118 |
| hh104 | 7,751 | 0.0997 | 0.1192 |
| hh105 | 20,180 | 0.1878 | 0.2337 |
| hh106 | 12,433 | 0.1632 | 0.2206 |
| hh108 | 20,659 | 0.1596 | 0.2013 |
| hh109 | 15,938 | 0.1915 | 0.2133 |
| hh111 | 20,660 | 0.1804 | 0.2161 |
| hh112 | 1,279 | 0.1454 | 0.1971 |
| hh113 | 4,840 | 0.1563 | 0.2189 |
| hh114 | 20,612 | 0.2057 | 0.2387 |
| hh115 | 3,268 | 0.0973 | 0.2351 |
| hh116 | 1,451 | 0.0573 | 0.0992 |
| hh117 | 3,268 | 0.0775 | 0.1519 |
| hh118 | 11,198 | 0.0896 | 0.1230 |
| hh119 | 18,472 | 0.0932 | 0.0928 |
| hh120 | 6,819 | 0.1346 | 0.1450 |
| hh122 | 1,989 | 0.1882 | 0.2104 |

---

# Backup — Literature verdict (Step 13)

- 29 measurements compared across 6 papers; **0 genuinely comparable**
  to a 30-minute, any-event, Brier/AP forecast on CASAS.
- Cook et al. (2013): 84% activity-recognition accuracy — a different
  task and metric; Minor et al. (2015): RAP MAE 8,433 s vs 19,051 s —
  seconds-valued error on a named-activity timing task. Neither number
  can be subtracted from a Brier loss.
- No published Brier score on this task was found; KindWindow's valid
  comparisons are its within-study baselines.
- Source: `reports/literature_comparison.csv`,
  `reports/method_comparison.md`.

---

# Backup — What was not run (reduced scope)

- Gap sensitivity 6 h / 24 h; degradation 10% of events, 25% of sensors,
  seeds other than 2207; abstention 75%; notes-eval on held-out notes.
- A post-test bug fix in Step 11 (degraded-label merge `KeyError`) was a
  labeled rerun of the degradation condition only; clean test scores were
  never recomputed.
- Demo: invite expiry is not shown live (real clock; covered by
  `tests/test_app.py::test_expiry_at_exact_expires_at` with a fake clock);
  no screen recording — working demo + captured log is the evidence
  (`demo/demo_log.txt`, `demo/demo_results.json`).

---

# Backup — Speaker ownership and timing

| Segment | Time | Presenter | Backup / Q&A owner |
|---|---:|---|---|
| Problem | 0–2 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| Cohort and methods | 2–5 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| RQ1 + RQ3 evidence | 5–8 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| RQ2 systems | 8–10 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| Live demo | 10–13 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| Limitations, replication, conclusion | 13–15 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |
| Q&A (up to 5 min) | 15–20 | Teja Piepur Chakravarthi | Teja Piepur Chakravarthi |

Single presenter; no other speaker names are invented. If the course
confirms additional team members, their sections are assigned here by
name before the talk. If the live demo fails on the day, fall back to
the captured log (`demo/demo_log.txt`) and the recorded run summary in
`docs/demo.md` — do not improvise numbers.
