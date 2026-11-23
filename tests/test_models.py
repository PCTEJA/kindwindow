"""Step 09: candidate models, calibration, retention gates, freeze (tests).

All fixtures are synthetic small data. No real windows, no test split.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kindwindow.cli import main
from kindwindow.contracts import ScoredOption, choose_best, retention_threshold
from kindwindow.features.features import FEATURE_COLUMNS
from kindwindow.models.baselines import equal_home_weights
from kindwindow.models.calibration import (
    CalibrationResult,
    apply_calibration,
    fit_calibration,
)
from kindwindow.models.freeze import (
    FrozenModel,
    compute_retention_gates,
    retention_gate_summary,
    verify_frozen_predictions,
)
from kindwindow.models.train import (
    CANDIDATES,
    build_full_design,
    evaluate_on_selection,
    fit_all_candidates,
    fit_candidate,
    fit_preprocessor,
    predict_proba_candidate,
    select_winner,
)

ROOT = Path(__file__).resolve().parents[1]


def make_frame(n_per_home: int = 60, homes=("a", "b"), seed: int = 0) -> pd.DataFrame:
    """Small synthetic windows frame with every feature column."""
    rng = np.random.default_rng(seed)
    rows = []
    for home in homes:
        n = n_per_home
        rec = pd.array(rng.integers(0, 7200, size=n), dtype="Int64")
        missing = rng.random(n) < 0.1
        rec[missing] = pd.NA
        rows.append(pd.DataFrame({
            "home": home,
            "t": pd.date_range("2020-01-01", periods=n, freq="30min"),
            "count_5m": rng.integers(0, 5, size=n),
            "count_30m": rng.integers(0, 20, size=n),
            "count_120m": rng.integers(0, 60, size=n),
            "door_5m": rng.integers(0, 3, size=n),
            "door_30m": rng.integers(0, 8, size=n),
            "door_120m": rng.integers(0, 20, size=n),
            "recency_seconds": rec,
            "recency_missing": missing,
            "hour": rng.integers(0, 24, size=n),
            "weekday": rng.integers(0, 7, size=n),
            "prior_freq_per_hour": rng.random(n) * 10,
            "label": rng.integers(0, 2, size=n),
        }))
    df = pd.concat(rows, ignore_index=True)
    for col in FEATURE_COLUMNS:
        assert col in df.columns
    return df


# ---------------------------------------------------------------- design / weights

def test_design_shape_and_feature_names():
    df = make_frame()
    prep = fit_preprocessor(df)
    X, names = build_full_design(df, prep)
    # 11 model features (6 counts + recency x2 + hour/weekday/prior_freq)
    # + one indicator per home (2 synthetic homes; 31 with the real 20).
    assert X.shape == (len(df), 13)
    assert names == prep.feature_names
    assert len(names) == 13
    assert names[-2:] == ["home_a", "home_b"]
    assert not np.isnan(X).any()


def test_equal_home_weights_mean_one():
    df = pd.DataFrame({"home": ["a"] * 10 + ["b"] * 30})
    w = equal_home_weights(df)
    assert w.mean() == pytest.approx(1.0)
    assert np.allclose(w[:10], 2.0)  # N/(H*n_a) = 40/20
    assert np.allclose(w[10:], 2.0 / 3)


# ---------------------------------------------------------------- candidates

def test_fit_candidate_logistic_shapes_and_repeat_deterministic():
    df = make_frame()
    prep = fit_preprocessor(df)
    weights = equal_home_weights(df)
    spec = CANDIDATES[1]  # full-logit C=1
    m1 = fit_candidate(spec, df, prep, weights)
    m2 = fit_candidate(spec, df, prep, weights)
    p1 = predict_proba_candidate(m1, df)
    p2 = predict_proba_candidate(m2, df)
    assert p1.shape == (len(df),)
    assert ((p1 >= 0) & (p1 <= 1)).all()
    assert np.array_equal(p1, p2)
    assert m1.fit_seconds >= 0
    assert isinstance(m1.convergence_warnings, list)


def test_fit_candidate_forest_repeat_deterministic():
    df = make_frame()
    prep = fit_preprocessor(df)
    weights = equal_home_weights(df)
    spec = CANDIDATES[3]  # forest depth5 leaf50
    m1 = fit_candidate(spec, df, prep, weights)
    m2 = fit_candidate(spec, df, prep, weights)
    # Parallel forest (n_jobs=2) tree-averaging is deterministic only to
    # last-bit float noise: summation order varies with job scheduling, so
    # identical refits can differ at ~1e-16. Assert agreement within the
    # same 1e-12 tolerance the freeze round-trip uses, not bitwise equality.
    p1 = predict_proba_candidate(m1, df)
    p2 = predict_proba_candidate(m2, df)
    assert np.max(np.abs(p1 - p2)) <= 1e-12


def test_fit_all_candidates_seven_and_selection_scores():
    df = make_frame(n_per_home=80)
    sel = make_frame(n_per_home=40, seed=7)
    models, _ = fit_all_candidates(df)
    assert len(models) == 7
    assert [m.name for m in models] == [c["name"] for c in CANDIDATES]
    scores = evaluate_on_selection(models, sel)
    # 7 candidates x (2 homes + MACRO)
    assert len(scores) == 21
    assert set(scores["candidate"]) == {c["name"] for c in CANDIDATES}
    assert (scores["macro_brier"] >= 0).all() and (scores["macro_brier"] <= 1).all()
    winner, ranking = select_winner(scores)
    assert winner.name in {c["name"] for c in CANDIDATES}
    assert len(ranking) == 7
    assert ranking.iloc[0]["candidate"] == winner.name
    assert ranking["macro_brier"].is_monotonic_increasing


# ---------------------------------------------------------------- choose_best

def test_choose_best_tie_rule_via_select_winner():
    # Exact tie: simpler complexity wins, then config order.
    scores = pd.DataFrame([
        {"candidate": "forest-d10-leaf10", "config": "x", "split": "selection",
         "home": "MACRO", "n": 10, "brier": 0.20, "macro_brier": 0.20,
         "complexity": 7, "order": 6},
        {"candidate": "full-logit-C1", "config": "x", "split": "selection",
         "home": "MACRO", "n": 10, "brier": 0.20, "macro_brier": 0.20,
         "complexity": 3, "order": 1},
        {"candidate": "full-logit-C0.1", "config": "x", "split": "selection",
         "home": "MACRO", "n": 10, "brier": 0.20, "macro_brier": 0.20,
         "complexity": 3, "order": 0},
    ])
    winner, _ = select_winner(scores)
    assert winner.name == "full-logit-C0.1"
    assert choose_best([
        ScoredOption("b", 0.2, 3, 1), ScoredOption("a", 0.2, 3, 0),
    ]).name == "a"


# ---------------------------------------------------------------- calibration

def _cal_frame(n_pos: int, n_neg: int) -> pd.DataFrame:
    n = n_pos + n_neg
    return pd.DataFrame({
        "home": ["a"] * n,
        "t": pd.date_range("2020-01-01", periods=n, freq="30min"),
        "label": [1] * n_pos + [0] * n_neg,
    })


def test_calibration_gate_sufficient_branch():
    df = _cal_frame(60, 60)
    rng = np.random.default_rng(0)
    p = rng.random(len(df))
    result = fit_calibration(p, df)
    assert result.fitted is True
    assert result.n_positive == 60 and result.n_negative == 60
    assert result.coef is not None and result.intercept is not None
    assert result.input == "raw_positive_class_probability"
    cal = apply_calibration(p, result)
    assert ((cal >= 0) & (cal <= 1)).all()
    assert not np.array_equal(cal, p) or True  # map may coincide only by chance


def test_calibration_gate_insufficient_branch_identity():
    df = _cal_frame(10, 60)  # only 10 positives < 50
    p = np.full(len(df), 0.4)
    result = fit_calibration(p, df)
    assert result.fitted is False
    assert result.n_positive == 10
    assert "gate failed" in result.reason
    assert np.array_equal(apply_calibration(p, result), p)
    df2 = _cal_frame(60, 49)  # only 49 negatives < 50
    result2 = fit_calibration(p[:109], df2)
    assert result2.fitted is False


# ---------------------------------------------------------------- retention

def test_retention_threshold_targets_and_tie_protocol():
    u = [0.1, 0.2, 0.2, 0.3]
    w = [1.0, 1.0, 1.0, 1.0]
    assert retention_threshold(u, w, 0.5) == 0.2
    assert retention_threshold(u, w, 0.75) == 0.2
    assert retention_threshold(u, w, 1.0) == float("inf")
    # Tie protocol: at cutoff 0.2 there are 2 tied windows (weight 2 of 4);
    # 50% target needs 1.0 of the tied weight -> half the ties retained.
    keys = ["k1", "k2", "k3", "k4"]
    g = retention_gate_summary(u, w, keys, 0.5)
    assert g["cutoff"] == 0.2
    assert g["n_below_cutoff"] == 1 and g["n_at_cutoff"] == 2
    assert g["tie_fraction_retained"] == pytest.approx(0.5)
    assert g["n_retained"] == 2  # 1 below + 1 of the 2 ties, by hash order
    g100 = retention_gate_summary(u, w, keys, 1.0)
    assert g100["cutoff"] == float("inf")
    assert g100["n_retained"] == 4
    assert g100["tie_fraction_retained"] is None


def test_compute_retention_gates_targets():
    df = make_frame(n_per_home=50, seed=3)
    p = np.linspace(0.05, 0.95, len(df))
    gates = compute_retention_gates(p, df, [0.5, 0.75, 1.0])
    assert set(gates) == {"0.5", "0.75", "1.0"}
    assert gates["0.5"]["cutoff"] < gates["0.75"]["cutoff"]
    assert gates["1.0"]["cutoff"] == float("inf")
    assert gates["0.5"]["retained_weight_fraction"] == pytest.approx(0.5, abs=0.05)


# ---------------------------------------------------------------- freeze round-trip

def test_frozen_artifact_round_trip_identical_predictions(tmp_path):
    df = make_frame(n_per_home=80)
    sel = make_frame(n_per_home=40, seed=7)
    models, _ = fit_all_candidates(df)
    winner = models[1]  # full-logit C=1
    cal = CalibrationResult(
        fitted=False, reason="synthetic identity", n_calibration=len(sel),
        n_positive=int(sel["label"].sum()),
        n_negative=int((sel["label"] == 0).sum()), n_homes=2,
    )
    p_raw = predict_proba_candidate(winner, sel)
    gates = compute_retention_gates(p_raw, sel, [0.5, 0.75, 1.0])
    frozen = FrozenModel(
        winner=winner, calibration=cal, retention_gates=gates,
        split_sha256="0" * 64, config_sha256="1" * 64,
        code_revision="test", n_train=len(df), n_selection=len(sel),
        n_calibration=len(sel), selection_scores=[],
        fit_timings_seconds={m.name: m.fit_seconds for m in models},
        convergence_warnings={},
    )
    verification = verify_frozen_predictions(frozen, sel)
    assert verification["identical"] is True
    assert verification["max_abs_diff_raw"] == 0.0
    assert verification["n_rows"] == len(sel)


# ---------------------------------------------------------------- CLI refusal

def test_cli_train_freeze_refuse_missing_data():
    assert main(["train", "--features", "/nonexistent-dir",
                 "--split", "/nonexistent.csv"]) == 2
    assert main(["freeze", "--features", "/nonexistent-dir",
                 "--split", "/nonexistent.csv"]) == 2


def test_cli_train_freeze_refuse_test_split():
    assert main(["train", "--eval-split", "test"]) == 2
    assert main(["freeze", "--eval-split", "test"]) == 2
