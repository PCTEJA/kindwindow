"""Candidate models for KindWindow (Step 09).

Seven candidates, all fit on the TRAIN split only, per
``configs/study.json`` and ``05_EXPERIMENTS.md``:

* full L2 logistic, C in {0.1, 1, 10} (config order 0-2, complexity 3);
* random forest, 200 trees x max_depth {5, 10} x min_samples_leaf
  {10, 50} (config order 3-6; complexity ranks shallower / larger-leaf
  forests simpler: depth5/leaf50 = 4, depth5/leaf10 = 5,
  depth10/leaf50 = 6, depth10/leaf10 = 7).

Pooled estimators with home indicators. Per-row sample weights are the
predeclared equal-home weights ``N / (H * n_home)`` so the mean training
weight is 1. Preprocessing is fit on train only. The fixed solver is
``lbfgs`` with ``max_iter=1000`` for logistic; the forest seed is the
model seed 2718 and ``n_jobs`` is bounded (2) for the memory-tight VM.
Convergence warnings are captured and returned, never dropped.

Design matrix (31 columns, canonical order): standardized count
features (count_5m/30m/120m, door_5m/30m/120m), recency_scaled,
recency_missing (missing recency -> scaled 0.0 + indicator 1.0, the
predeclared handling from ``features``), standardized hour, weekday
and prior_freq_per_hour, then one home indicator per home (sorted
order). Standardization uses train-only population mean/std (ddof=0);
a zero train std maps to 1.0. Forests use the same design (they are
invariant to monotone scaling); the fit casts to float32 for memory.

No test-split quantity is computed anywhere in this module.
"""

from __future__ import annotations

import hashlib
import time
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from kindwindow.contracts import ScoredOption, choose_best
from kindwindow.features.features import (
    encode_home_indicators,
    fit_recency_scaler,
    home_indicator_columns,
    transform_recency,
)
from kindwindow.models.baselines import equal_home_weights, macro_brier, per_home_brier

MODEL_SEED = 2718
FOREST_N_ESTIMATORS = 200
FOREST_N_JOBS = 2
LOGISTIC_MAX_ITER = 1000

# Numeric columns standardized with train-only mean/std (recency is
# handled separately by transform_recency, which already z-scores it).
_STANDARDIZED_COLUMNS: list[str] = [
    "count_5m",
    "count_30m",
    "count_120m",
    "door_5m",
    "door_30m",
    "door_120m",
    "hour",
    "weekday",
    "prior_freq_per_hour",
]

# (name, config string, kind, params, complexity, order)
CANDIDATES: list[dict] = [
    {"name": "full-logit-C0.1", "config": "C=0.1", "kind": "logistic",
     "params": {"C": 0.1}, "complexity": 3, "order": 0},
    {"name": "full-logit-C1", "config": "C=1", "kind": "logistic",
     "params": {"C": 1.0}, "complexity": 3, "order": 1},
    {"name": "full-logit-C10", "config": "C=10", "kind": "logistic",
     "params": {"C": 10.0}, "complexity": 3, "order": 2},
    {"name": "forest-d5-leaf50", "config": "max_depth=5,min_samples_leaf=50",
     "kind": "forest", "params": {"max_depth": 5, "min_samples_leaf": 50},
     "complexity": 4, "order": 3},
    {"name": "forest-d5-leaf10", "config": "max_depth=5,min_samples_leaf=10",
     "kind": "forest", "params": {"max_depth": 5, "min_samples_leaf": 10},
     "complexity": 5, "order": 4},
    {"name": "forest-d10-leaf50", "config": "max_depth=10,min_samples_leaf=50",
     "kind": "forest", "params": {"max_depth": 10, "min_samples_leaf": 50},
     "complexity": 6, "order": 5},
    {"name": "forest-d10-leaf10", "config": "max_depth=10,min_samples_leaf=10",
     "kind": "forest", "params": {"max_depth": 10, "min_samples_leaf": 10},
     "complexity": 7, "order": 6},
]

EXPECTED_SPLIT_SHA256 = (
    "65f5a9974f944e7c23e9fb7e7a03d749459ba3ae02adc5aadc1419dd74d038cb"
)


def sha256_file(path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def verify_split_hash(split_path) -> str:
    """Return the split.csv SHA-256, raising if it mismatches Step 08."""
    digest = sha256_file(split_path)
    if digest != EXPECTED_SPLIT_SHA256:
        raise ValueError(
            f"split.csv hash mismatch: got {digest}, expected "
            f"{EXPECTED_SPLIT_SHA256} (refusing to train on an "
            "unverified split)"
        )
    return digest


def load_splits(features_dir, split_path) -> dict[str, pd.DataFrame]:
    """Load windows joined to split.csv; return train/selection/calibration.

    Test rows are dropped before return so no downstream code in this
    step can touch them; purged rows are dropped too. Raises
    ``ValueError`` on a split-hash mismatch and ``FileNotFoundError``
    when inputs are missing.
    """
    from pathlib import Path

    features_dir = Path(features_dir)
    split_path = Path(split_path)
    if not split_path.is_file():
        raise FileNotFoundError(f"split file not found: {split_path}")
    if not features_dir.is_dir() or not list(features_dir.rglob("*.parquet")):
        raise FileNotFoundError(f"no windows Parquet at {features_dir}")
    verify_split_hash(split_path)
    windows = pd.concat(
        [pd.read_parquet(f) for f in sorted(features_dir.rglob("*.parquet"))],
        ignore_index=True,
    )
    splits = pd.read_csv(split_path)
    splits["t"] = pd.to_datetime(splits["t"])
    merged = windows.merge(splits, on=["home", "t"], how="left", validate="one_to_one")
    if merged["split"].isna().any():
        raise ValueError("windows without a split assignment")
    out = {}
    for name in ("train", "selection", "calibration"):
        out[name] = merged[merged["split"] == name].reset_index(drop=True)
    # Test is sealed: never returned, never counted here.
    return out


@dataclass
class Preprocessor:
    """Train-only preprocessing for the full design."""

    means: dict[str, float] = field(default_factory=dict)
    stds: dict[str, float] = field(default_factory=dict)
    recency_scaler: dict[str, float] = field(default_factory=dict)
    homes: list[str] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)


def fit_preprocessor(train: pd.DataFrame) -> Preprocessor:
    """Fit standardization + recency scaler on TRAIN only."""
    means, stds = {}, {}
    for col in _STANDARDIZED_COLUMNS:
        vals = train[col].to_numpy(dtype=np.float64)
        mean = float(np.mean(vals))
        std = float(np.std(vals))
        means[col] = mean
        stds[col] = std if std != 0.0 else 1.0
    recency_scaler = fit_recency_scaler(train)
    homes = sorted(train["home"].unique())
    names = (
        [f"{c}_std" for c in _STANDARDIZED_COLUMNS[:6]]
        + ["recency_scaled", "recency_missing"]
        + ["hour_std", "weekday_std", "prior_freq_per_hour_std"]
        + home_indicator_columns(homes)
    )
    return Preprocessor(
        means=means, stds=stds, recency_scaler=recency_scaler,
        homes=homes, feature_names=names,
    )


def build_full_design(
    df: pd.DataFrame, prep: Preprocessor
) -> tuple[np.ndarray, list[str]]:
    """Full 31-column design matrix for ``df`` under a fitted preprocessor."""
    parts: list[np.ndarray] = []
    for col in _STANDARDIZED_COLUMNS[:6]:
        vals = df[col].to_numpy(dtype=np.float64)
        parts.append(((vals - prep.means[col]) / prep.stds[col])[:, None])
    rec = transform_recency(df, prep.recency_scaler)
    parts.append(rec)
    for col in ("hour", "weekday", "prior_freq_per_hour"):
        vals = df[col].to_numpy(dtype=np.float64)
        parts.append(((vals - prep.means[col]) / prep.stds[col])[:, None])
    parts.append(encode_home_indicators(df, prep.homes))
    return np.hstack(parts), list(prep.feature_names)


@dataclass
class FittedCandidate:
    """One fitted candidate plus fit metadata."""

    name: str
    config: str
    kind: str
    params: dict
    complexity: int
    order: int
    estimator: object = None
    preprocessor: Preprocessor | None = None
    fit_seconds: float = 0.0
    convergence_warnings: list[str] = field(default_factory=list)


def predict_proba_candidate(model: FittedCandidate, df: pd.DataFrame) -> np.ndarray:
    """Raw positive-class probabilities of a fitted candidate on ``df``."""
    if model.estimator is None or model.preprocessor is None:
        raise ValueError(f"candidate {model.name} is not fitted")
    X, _ = build_full_design(df, model.preprocessor)
    if model.kind == "forest":
        X = X.astype(np.float32)
    return np.asarray(model.estimator.predict_proba(X)[:, 1], dtype=np.float64)


def fit_candidate(
    spec: dict, train: pd.DataFrame, prep: Preprocessor, weights: np.ndarray
) -> FittedCandidate:
    """Fit one candidate spec on TRAIN; capture timing + warnings."""
    y = train["label"].to_numpy(dtype=np.int64)
    X, _ = build_full_design(train, prep)
    t0 = time.time()
    caught: list[str] = []
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        if spec["kind"] == "logistic":
            est = LogisticRegression(
                C=spec["params"]["C"], penalty="l2", solver="lbfgs",
                max_iter=LOGISTIC_MAX_ITER,
            )
            est.fit(X, y, sample_weight=weights)
        else:
            est = RandomForestClassifier(
                n_estimators=FOREST_N_ESTIMATORS,
                max_depth=spec["params"]["max_depth"],
                min_samples_leaf=spec["params"]["min_samples_leaf"],
                random_state=MODEL_SEED, n_jobs=FOREST_N_JOBS,
            )
            est.fit(X.astype(np.float32), y, sample_weight=weights)
        for w in caught_warnings:
            caught.append(f"{w.category.__name__}: {w.message}")
    return FittedCandidate(
        name=spec["name"], config=spec["config"], kind=spec["kind"],
        params=dict(spec["params"]), complexity=spec["complexity"],
        order=spec["order"], estimator=est, preprocessor=prep,
        fit_seconds=time.time() - t0, convergence_warnings=caught,
    )


def fit_all_candidates(train: pd.DataFrame) -> tuple[list[FittedCandidate], Preprocessor]:
    """Fit all 7 candidates on TRAIN; returns (models, preprocessor)."""
    prep = fit_preprocessor(train)
    weights = equal_home_weights(train)
    models = [fit_candidate(spec, train, prep, weights) for spec in CANDIDATES]
    return models, prep


def evaluate_on_selection(
    models: list[FittedCandidate], selection: pd.DataFrame
) -> pd.DataFrame:
    """Per-home + macro Brier on SELECTION for each candidate.

    One row per (candidate, home) plus a MACRO row per candidate.
    Columns: candidate, config, split, home, n, brier, macro_brier,
    complexity, order.
    """
    rows: list[dict] = []
    for model in models:
        p = predict_proba_candidate(model, selection)
        per_home = per_home_brier(selection, p)
        macro = macro_brier(per_home)
        for home in sorted(per_home):
            rows.append({
                "candidate": model.name, "config": model.config,
                "split": "selection", "home": home,
                "n": int((selection["home"] == home).sum()),
                "brier": per_home[home], "macro_brier": macro,
                "complexity": model.complexity, "order": model.order,
            })
        rows.append({
            "candidate": model.name, "config": model.config,
            "split": "selection", "home": "MACRO", "n": len(selection),
            "brier": macro, "macro_brier": macro,
            "complexity": model.complexity, "order": model.order,
        })
    return pd.DataFrame(
        rows,
        columns=["candidate", "config", "split", "home", "n", "brier",
                 "macro_brier", "complexity", "order"],
    )


def select_winner(scores: pd.DataFrame) -> tuple[ScoredOption, pd.DataFrame]:
    """Winner via ``contracts.choose_best`` over the 7 candidates.

    Returns (winner, ranking) where ranking is one row per candidate
    with macro Brier, complexity, order, sorted by the tie rule.
    """
    macro = scores[scores["home"] == "MACRO"]
    options = [
        ScoredOption(
            name=str(r["candidate"]), loss=float(r["macro_brier"]),
            complexity=int(r["complexity"]), order=int(r["order"]),
        )
        for r in macro.to_dict("records")
    ]
    winner = choose_best(options)
    ranking = macro[["candidate", "config", "macro_brier", "complexity", "order"]].copy()
    ranking = ranking.sort_values(
        ["macro_brier", "complexity", "order"], kind="stable"
    ).reset_index(drop=True)
    return winner, ranking

# ---------------------------------------------------------------------------
# Orchestration (CLI entry points)
# ---------------------------------------------------------------------------


def _versions() -> dict:
    import platform
    import sys

    import sklearn

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }


def _code_revision(root) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=30,
        )
        rev = out.stdout.strip()
        return rev if rev else "unknown"
    except Exception:
        return "unknown"


def run_train(root, features_dir=None, split_path=None) -> dict:
    """Fit all candidates on TRAIN, score on SELECTION, save artifacts.

    Writes ``reports/tables/selection_scores.csv`` (7 candidates + the
    fixed recency baseline), ``local/models/candidates_step09.joblib``
    (fitted candidates for ``freeze``), and
    ``local/runs/step09/train_result.json``. Never touches test.
    """
    import json
    from pathlib import Path

    import joblib

    from kindwindow.models.baselines import (
        fit_recency_logistic,
        predict_recency_logistic,
    )

    root = Path(root)
    features_dir = Path(features_dir) if features_dir else root / "local" / "features"
    split_path = Path(split_path) if split_path else root / "data" / "manifests" / "split.csv"
    splits = load_splits(features_dir, split_path)
    train, selection, calibration = (
        splits["train"], splits["selection"], splits["calibration"],
    )
    split_hash = verify_split_hash(split_path)
    config_hash = sha256_file(root / "configs" / "study.json")
    revision = _code_revision(root)

    models, _prep = fit_all_candidates(train)
    scores = evaluate_on_selection(models, selection)

    # Fixed recency baseline (C=1, recency-only) for comparison, same
    # selection split; it is NOT part of the 7-candidate winner pool.
    rec = fit_recency_logistic(train)
    p_rec = predict_recency_logistic(rec, selection)
    per_home = per_home_brier(selection, p_rec)
    macro = macro_brier(per_home)
    rec_rows = []
    for home in sorted(per_home):
        rec_rows.append({
            "candidate": "recency_logistic", "config": "C=1 recency-only",
            "split": "selection", "home": home,
            "n": int((selection["home"] == home).sum()),
            "brier": per_home[home], "macro_brier": macro,
            "complexity": 2, "order": 99,
        })
    rec_rows.append({
        "candidate": "recency_logistic", "config": "C=1 recency-only",
        "split": "selection", "home": "MACRO", "n": len(selection),
        "brier": macro, "macro_brier": macro, "complexity": 2, "order": 99,
    })
    scores = pd.concat(
        [scores, pd.DataFrame(rec_rows, columns=list(scores.columns))],
        ignore_index=True,
    )

    winner, ranking = select_winner(
        scores[scores["candidate"] != "recency_logistic"]
    )
    out_path = root / "reports" / "tables" / "selection_scores.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scores.to_csv(out_path, index=False)

    models_dir = root / "local" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {m.name: m for m in models}, models_dir / "candidates_step09.joblib"
    )
    timings = {m.name: m.fit_seconds for m in models}
    warnings_out = {m.name: m.convergence_warnings for m in models}
    result = {
        "step": "09",
        "split_sha256": split_hash,
        "config_sha256": config_hash,
        "code_revision": revision,
        "n_train": len(train),
        "n_selection": len(selection),
        "n_calibration": len(calibration),
        "winner": {
            "name": winner.name, "loss": winner.loss,
            "complexity": winner.complexity, "order": winner.order,
        },
        "ranking": ranking.to_dict("records"),
        "recency_baseline_macro_brier": macro,
        "fit_timings_seconds": timings,
        "convergence_warnings": warnings_out,
        "versions": _versions(),
        "test_sealed": True,
    }
    run_dir = root / "local" / "runs" / "step09"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "train_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    result["selection_scores_csv"] = str(out_path)
    return result
