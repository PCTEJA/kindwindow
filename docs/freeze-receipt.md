# KindWindow Step 09 freeze receipt

Winner: **forest-d10-leaf10** (max_depth=10,min_samples_leaf=10, forest) — selection macro Brier 0.151403 (n_selection=102023).

## Selection ranking (macro Brier, mean of per-home Brier, selection only)

| rank | candidate | config | macro Brier | complexity | order |
|---:|---|---|---:|---:|---:|
| 1 | forest-d10-leaf10 | max_depth=10,min_samples_leaf=10 | 0.151403 | 7 | 6 |
| 2 | forest-d10-leaf50 | max_depth=10,min_samples_leaf=50 | 0.151520 | 6 | 5 |
| 3 | forest-d5-leaf50 | max_depth=5,min_samples_leaf=50 | 0.161105 | 4 | 3 |
| 4 | forest-d5-leaf10 | max_depth=5,min_samples_leaf=10 | 0.161203 | 5 | 4 |
| 5 | full-logit-C10 | C=10 | 0.168686 | 3 | 2 |
| 6 | full-logit-C1 | C=1 | 0.170000 | 3 | 1 |
| 7 | full-logit-C0.1 | C=0.1 | 0.173833 | 3 | 0 |

Fixed recency baseline (C=1, recency-only) selection macro Brier: 0.193048.

## Calibration gate

- Outcome: fitted
- Calibration n=102026, positives=52068, negatives=49958, homes=20
- Reason: calibration gate passed: 52068 positives and 49958 negatives (>= 50 each); sigmoid fit on raw positive-class probability with equal-home weights
- Input pinned: raw_positive_class_probability
- Coef: 6.911215850339948, intercept: -3.4455288717200574
- Weights: equal-home N/(H*n_home) on calibration

## Retention gates (frozen on SELECTION only, u = p_raw*(1-p_raw))

| target | cutoff | n retained / n | tie fraction retained |
|---:|---:|---:|---:|
| 0.5 | 0.203018 | 44651 / 102023 | 0.7289164472140097 |
| 0.75 | 0.241647 | 73531 / 102023 | 0.5691900643183628 |
| 1.0 | inf | 102023 / 102023 | None |

Tie protocol: windows with u strictly below the cutoff are retained; windows exactly at the cutoff are ordered by SHA-256(home|origin) ascending and retained in that order until the nominal retained weight is reached. At 100% retention thresholding is bypassed explicitly. Gates are applied only later (Step 11); here they are frozen, not applied to any scored split.

## What is frozen

- Frozen artifact: `local/models/frozen_model.json` (SHA-256 b2989ae57cd8c7c5d64f8913b60299910dbacd24f490775a90c7e84354527a81) + `local/models/frozen_forest.joblib` (fitted forest sidecar)
- Winner model: forest-d10-leaf10 (forest), params {'max_depth': 10, 'min_samples_leaf': 10}
- Feature names (31): count_5m_std, count_30m_std, count_120m_std, door_5m_std, door_30m_std, door_120m_std, recency_scaled, recency_missing, hour_std, weekday_std, prior_freq_per_hour_std, home_aruba, home_hh101, home_hh102, home_hh103, home_hh104, home_hh105, home_hh106, home_hh108, home_hh109, home_hh111, home_hh112, home_hh113, home_hh114, home_hh115, home_hh116, home_hh117, home_hh118, home_hh119, home_hh120, home_hh122
- Split SHA-256: 65f5a9974f944e7c23e9fb7e7a03d749459ba3ae02adc5aadc1419dd74d038cb
- Config SHA-256: 628db5f27bb48ea991333261e9a1533628cbbf26d817fa16ab72207f2b5d35b2
- Code revision: c4dfa98a5050841e44eac05699b6f1e48d2c6e18
- n_train=622817, n_selection=102023, n_calibration=102026
- Round-trip verification: predictions after save/reload agree within tolerance 1e-12 (max abs diff raw 4.440892098500626e-16, calibrated 5.551115123125783e-16, n_rows=102023) — forest tree-averaging summation order differs at the 1e-16 level after reload, far below the protocol float_atol of 1e-8
- Reproducibility note: the frozen artifacts are **not byte-reproducible** across identical re-runs of `freeze`. The parallel forest (n_jobs=2, float32) refits differ at last-bit float level (tree-averaging summation order varies with job scheduling), so the frozen JSON SHA changed between two identical freeze runs (5526ae… → b2989a…) and the forest sidecar SHA likewise differs between runs. The artifact of record is the one whose SHA-256 is in this receipt and in `local/runs/step09/manifest.json`; its predictions agree with the recorded selection scores to 1e-12. The determinism test in `tests/test_models.py` asserts 1e-12 agreement rather than bitwise equality for this reason.

## Test-sealed statement

The test split was never read, fit, tuned, or scored in Step 09. No test metric appears in this receipt or in the frozen artifact.
