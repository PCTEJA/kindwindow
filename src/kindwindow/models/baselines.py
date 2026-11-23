"""Baseline models for KindWindow (Step 08).

Pure fit/predict functions; all fitting is on the TRAIN split only.
Per ``configs/study.json`` and ``05_EXPERIMENTS.md``:

* (a) per-home constant: predict the home's train prevalence.
* (b) per-home hour-of-week with pseudocount 20: bucket 0-167 from local
  weekday/hour; ``p = (positives_in_bucket + 20 * home_rate) /
  (n_in_bucket + 20)``; an unseen bucket returns the training home rate.
* (c) recency-only pooled L2 logistic, C=1: features are the scaled
  recency, the recency-missing indicator, and home indicators
  (``features.build_recency_design``); the scaler is fit on train only;
  sample weights are the predeclared equal-home weights
  ``N / (H * n_home)`` so each home contributes equally to the pooled fit.

Brier score is reported per home; the macro is the equal-home mean of
per-home Brier, per the frozen selection metric. No test-split quantity
is computed anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from kindwindow.features.features import build_recency_design, fit_recency_scaler

PSEUDOCOUNT = 20
LOGISTIC_C = 1.0
HOUR_OF_WEEK_BUCKETS = 168


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    """Mean squared error between binary outcomes and probabilities."""
    y = np.asarray(y, dtype=np.float64)
    p = np.asarray(p, dtype=np.float64)
    if len(y) == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def per_home_brier(df: pd.DataFrame, p: np.ndarray) -> dict[str, float]:
    """Brier score per home, in sorted-home order."""
    out: dict[str, float] = {}
    p = np.asarray(p, dtype=np.float64)
    homes = df["home"].to_numpy()
    labels = df["label"].to_numpy()
    for home in sorted(df["home"].unique()):
        pos = np.where(homes == home)[0]
        out[str(home)] = brier_score(labels[pos], p[pos])
    return out


def macro_brier(per_home: dict[str, float]) -> float:
    """Equal-home mean of per-home Brier scores."""
    return float(np.mean(list(per_home.values())))


# ---------------------------------------------------------------------------
# (a) Per-home constant
# ---------------------------------------------------------------------------


@dataclass
class ConstantModel:
    home_rates: dict[str, float] = field(default_factory=dict)
    global_rate: float = 0.0


def fit_constant(train: pd.DataFrame) -> ConstantModel:
    """Per-home train prevalence; global train prevalence as fallback."""
    rates = {
        str(home): float(group["label"].mean())
        for home, group in train.groupby("home", sort=True)
    }
    return ConstantModel(
        home_rates=rates, global_rate=float(train["label"].mean())
    )


def predict_constant(model: ConstantModel, df: pd.DataFrame) -> np.ndarray:
    return np.array(
        [model.home_rates.get(str(h), model.global_rate) for h in df["home"]],
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# (b) Per-home hour-of-week with pseudocount 20
# ---------------------------------------------------------------------------


@dataclass
class HourOfWeekModel:
    home_rates: dict[str, float] = field(default_factory=dict)
    global_rate: float = 0.0
    # (home, bucket) -> smoothed probability
    table: dict[tuple[str, int], float] = field(default_factory=dict)


def hour_of_week(df: pd.DataFrame) -> np.ndarray:
    """Bucket 0-167 from local weekday (Monday=0) and hour."""
    return (df["weekday"].to_numpy(dtype=np.int64) * 24
            + df["hour"].to_numpy(dtype=np.int64))


def fit_hour_of_week(train: pd.DataFrame) -> HourOfWeekModel:
    """Smoothed per-bucket rates: (pos + 20*home_rate) / (n + 20)."""
    base = fit_constant(train)
    table: dict[tuple[str, int], float] = {}
    buckets = hour_of_week(train)
    frame = train.assign(_bucket=buckets)
    for (home, bucket), group in frame.groupby(["home", "_bucket"], sort=True):
        n = len(group)
        pos = int(group["label"].sum())
        home_rate = base.home_rates[str(home)]
        table[(str(home), int(bucket))] = (pos + PSEUDOCOUNT * home_rate) / (
            n + PSEUDOCOUNT
        )
    return HourOfWeekModel(
        home_rates=base.home_rates, global_rate=base.global_rate, table=table
    )


def predict_hour_of_week(model: HourOfWeekModel, df: pd.DataFrame) -> np.ndarray:
    buckets = hour_of_week(df)
    out = np.empty(len(df), dtype=np.float64)
    for i, (home, bucket) in enumerate(zip(df["home"], buckets)):
        home = str(home)
        out[i] = model.table.get(
            (home, int(bucket)), model.home_rates.get(home, model.global_rate)
        )
    return out


# ---------------------------------------------------------------------------
# (c) Recency-only pooled L2 logistic, C=1
# ---------------------------------------------------------------------------


@dataclass
class RecencyLogisticModel:
    estimator: LogisticRegression | None = None
    scaler: dict[str, float] = field(default_factory=dict)
    homes: list[str] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)


def equal_home_weights(train: pd.DataFrame) -> np.ndarray:
    """Predeclared normalized weights ``N / (H * n_home)``."""
    n = len(train)
    homes = sorted(train["home"].unique())
    counts = train["home"].value_counts()
    return np.array(
        [n / (len(homes) * counts[h]) for h in train["home"]], dtype=np.float64
    )


def fit_recency_logistic(train: pd.DataFrame) -> RecencyLogisticModel:
    """Pooled L2 logistic (C=1) on recency + home indicators, train only."""
    homes = sorted(train["home"].unique())
    scaler = fit_recency_scaler(train)
    X, names = build_recency_design(train, homes, scaler)
    weights = equal_home_weights(train)
    est = LogisticRegression(C=LOGISTIC_C, penalty="l2", solver="lbfgs",
                             max_iter=1000)
    est.fit(X, train["label"].to_numpy(dtype=np.int64), sample_weight=weights)
    return RecencyLogisticModel(
        estimator=est, scaler=scaler, homes=homes, feature_names=names
    )


def predict_recency_logistic(
    model: RecencyLogisticModel, df: pd.DataFrame
) -> np.ndarray:
    if model.estimator is None:
        raise ValueError("recency logistic model is not fitted")
    X, _ = build_recency_design(df, model.homes, model.scaler)
    return np.asarray(
        model.estimator.predict_proba(X)[:, 1], dtype=np.float64
    )


def evaluate_baselines(
    train: pd.DataFrame, selection: pd.DataFrame
) -> pd.DataFrame:
    """Fit all three baselines on train; report train + selection Brier.

    One row per (baseline, split, home) with n and per-home Brier, plus
    macro (equal-home) rows with home == "MACRO". Selection reporting
    only; test is never touched.
    """
    const = fit_constant(train)
    how = fit_hour_of_week(train)
    rec = fit_recency_logistic(train)
    models = {
        "constant": (const, predict_constant),
        "hour_of_week": (how, predict_hour_of_week),
        "recency_logistic": (rec, predict_recency_logistic),
    }
    rows: list[dict] = []
    for name, (model, predict) in models.items():
        for split_name, frame in (("train", train), ("selection", selection)):
            frame = frame.reset_index(drop=True)
            p = predict(model, frame)
            per_home = per_home_brier(frame, p)
            for home in sorted(per_home):
                n_home = int((frame["home"] == home).sum())
                rows.append(
                    {
                        "baseline": name,
                        "split": split_name,
                        "home": home,
                        "n": n_home,
                        "brier": per_home[home],
                    }
                )
            rows.append(
                {
                    "baseline": name,
                    "split": split_name,
                    "home": "MACRO",
                    "n": len(frame),
                    "brier": macro_brier(per_home),
                }
            )
    return pd.DataFrame(
        rows, columns=["baseline", "split", "home", "n", "brier"]
    )
