# KindWindow Step 11 — test-once evaluation (REDUCED scope)

Frozen model: **forest-d10-leaf10** (200 trees, depth 10, leaf 10, seed 2718), sigmoid calibration coef 6.911216 / intercept -3.445529, loaded from `local/models/frozen_model.json` (SHA-256 `b2989ae57cd8c7c5d64f8913b60299910dbacd24f490775a90c7e84354527a81`) + sidecar. Never refit. Code revision `f145412`, config SHA-256 `628db5f27bb48ea991333261e9a1533628cbbf26d817fa16ab72207f2b5d35b2`, split SHA-256 `65f5a9974f944e7c23e9fb7e7a03d749459ba3ae02adc5aadc1419dd74d038cb`. Freeze manifest written to `local/runs/step11/manifest.json` BEFORE any test row was read; test evaluated exactly once (`kindwindow evaluate`, ~290 s). Row-level predictions are not published; only aggregates are in `reports/tables/test_scores.csv`.

## RQ1 / RQ3 — test scores (n_test = 206,953 windows, 20 homes, 107,232 positives, prevalence 0.518)

| model | macro Brier | macro AP |
|---|---:|---:|
| forest-d10-leaf10-calibrated | 0.140965 | 0.805909 |
| forest-d10-leaf10-raw | 0.141939 | 0.805909 |
| recency-baseline | 0.184562 | 0.787817 |
| constant-baseline | 0.237200 | 0.511600 |
| hour-of-week-baseline | 0.225579 | 0.608696 |

Paired improvement (recency baseline − frozen calibrated, macro Brier): **0.043597** (23.6% relative). Frozen calibrated beats recency in 19/20 homes; the exception is hh119 (frozen 0.093179 vs recency 0.092833). Calibration helps slightly: raw macro Brier 0.141939 → calibrated 0.140965.

### Per-home Brier (frozen calibrated vs baselines)

| home | n | n_pos | prevalence | frozen calib | recency | constant | hour-of-week |
|---|---:|---:|---:|---:|---:|---:|---:|
| aruba | 2062 | 1434 | 0.695 | 0.130081 | 0.195987 | 0.212112 | 0.202148 |
| hh101 | 3521 | 2395 | 0.680 | 0.153006 | 0.186469 | 0.219936 | 0.204087 |
| hh102 | 9902 | 6276 | 0.634 | 0.128833 | 0.180676 | 0.235062 | 0.204991 |
| hh103 | 20651 | 11985 | 0.580 | 0.180075 | 0.211756 | 0.244091 | 0.241620 |
| hh104 | 7751 | 6501 | 0.839 | 0.099701 | 0.119201 | 0.135352 | 0.140803 |
| hh105 | 20180 | 11097 | 0.550 | 0.187832 | 0.233715 | 0.267513 | 0.222434 |
| hh106 | 12433 | 6545 | 0.526 | 0.163169 | 0.220602 | 0.249312 | 0.220635 |
| hh108 | 20659 | 11233 | 0.544 | 0.159602 | 0.201303 | 0.261761 | 0.249518 |
| hh109 | 15938 | 10323 | 0.648 | 0.191538 | 0.213310 | 0.229093 | 0.211544 |
| hh111 | 20660 | 12691 | 0.614 | 0.180379 | 0.216068 | 0.237201 | 0.224123 |
| hh112 | 1279 | 878 | 0.686 | 0.145353 | 0.197082 | 0.215358 | 0.203373 |
| hh113 | 4840 | 2988 | 0.617 | 0.156315 | 0.218904 | 0.238657 | 0.181370 |
| hh114 | 20612 | 10411 | 0.505 | 0.205718 | 0.238709 | 0.256864 | 0.231569 |
| hh115 | 3268 | 1439 | 0.440 | 0.097348 | 0.235056 | 0.363513 | 0.365721 |
| hh116 | 1451 | 81 | 0.056 | 0.057295 | 0.099198 | 0.161061 | 0.169177 |
| hh117 | 3268 | 236 | 0.072 | 0.077488 | 0.151895 | 0.206015 | 0.214141 |
| hh118 | 11198 | 4001 | 0.357 | 0.089604 | 0.123038 | 0.256732 | 0.277623 |
| hh119 | 18472 | 2790 | 0.151 | 0.093179 | 0.092833 | 0.207930 | 0.216690 |
| hh120 | 6819 | 2637 | 0.387 | 0.134585 | 0.145047 | 0.315485 | 0.313734 |
| hh122 | 1989 | 1291 | 0.649 | 0.188193 | 0.210393 | 0.230943 | 0.216278 |
| **MACRO** | **206953** | **107232** | **0.518** | **0.140965** | **0.184562** | **0.237200** | **0.225579** |

All 20 homes have both classes in test, so every per-home AP is defined (macro AP 0.805909). Full per-home AP values are in `reports/tables/test_scores.csv`.

## Abstention (frozen gates, u = p_raw·(1−p_raw), low retained; frozen 50% cutoff 0.203018)

| retention | cutoff | retained / eligible | coverage | macro Brier on retained |
|---:|---:|---:|---:|---:|
| 0.5 | 0.20301844971321653 | 100812 / 206953 | 0.4871 | 0.069071 |
| 1.0 | inf | 206953 / 206953 | 1.0000 | 0.140965 |

At 50% retention the measured coverage is 0.4871 (nominal 0.50 — coverage on test differs from nominal, as the protocol expects); retained macro Brier 0.069071 vs 0.140965 at 100%. The frozen hash tiebreak for windows exactly at the cutoff was implemented per the freeze protocol; tie counts on test were not separately logged. Per-home retained counts are in `local/runs/step11/results.json`.

## Gap sensitivity — 12 h only

Windows whose span [t−120m, t+30m) intersects an event-free gap strictly longer than 12 h: **30447 flagged** (aruba and hh113: 0; most in hh119 (11,331), hh118 (4,819), hh120 (2,668), hh108 (2,660) — see `results.json`), 176506 kept. Macro Brier excluding flagged windows: **0.162033** vs 0.140965 on all test windows — excluding long-gap windows makes the scored set *harder* (flagged windows are disproportionately easy/silent periods; kept prevalence 0.604 vs 0.518 overall).

## Degradation — 30% of events gone, seed 2207 only

Deterministic hash drop (SHA-256 of `seed|home|member|row`): 23,475,783 events dropped of 78,275,425 (actual fraction 0.29991). Windows recomputed on the degraded stream with the vectorized features path; clean test labels preserved; all 206,953 test keys rescored, 0 missing keys. Macro Brier **0.142559** vs clean 0.140965 (+0.001595) — the frozen model is nearly insensitive to losing 30% of events.

## Failed cases

None. `failed_cases` in `results.json` is empty: every test window was scored for every reported model/condition; the degradation condition scored all 206,953 keys with 0 missing.

## Post-test bug fix (labeled rerun, per kit §05)

The first run omitted the degradation condition with `KeyError: 'label'` — the degraded windows' own label column collided with the clean test label on merge. Fixed in `src/kindwindow/evaluation/evaluation.py` (drop the degraded-stream label before merging). Only the degradation condition was rerun (`/tmp/step11_degradation_rerun.py`, 253 s); clean test scores were NOT recomputed and previous outputs were preserved — only the degradation rows were appended to `test_scores.csv` and the degradation section of `results.json` updated.

## Omitted from reduced scope (not run)

- gap 6h/24h (reduced scope)
- degradation 10% events, 25% sensors, seeds 1103/3301/4409/5519 (reduced scope)
- abstention 75% (reduced scope)
- notes-eval on heldout (not run in this reduced step)
- Figure: `reports/figures/per_home_brier.svg` (grouped per-home Brier, frozen calibrated vs recency) was generated by hand in SVG — no matplotlib in the venv.

## Files

- `src/kindwindow/evaluation/__init__.py`, `src/kindwindow/evaluation/evaluation.py` — evaluation module (load frozen, score, retention, gap, degradation)
- `tests/test_evaluation.py` — 7 synthetic unit tests
- `src/kindwindow/cli.py` — `evaluate` subcommand wired (was a stub)
- `local/runs/step11/manifest.json` — freeze manifest (written before test access)
- `local/runs/step11/results.json` — machine-readable summary
- `reports/tables/test_scores.csv` — 189 rows (9 models × 21 home/MACRO rows)
- `reports/figures/per_home_brier.svg` — per-home Brier figure