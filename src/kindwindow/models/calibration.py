"""Calibration for KindWindow (Step 09).

A single sigmoid (Platt) map fit on the frozen candidate's scores using
ONLY the calibration segment, per ``05_EXPERIMENTS.md``:

* Gate: fit only if calibration has >= 50 positives AND >= 50
  negatives across homes (``calibration_min_each_class`` = 50 from
  ``configs/study.json``); otherwise identity calibration with the
  counts and reason disclosed.
* Pinned input: the raw positive-class probability
  ``p_raw = P(y=1)`` from the frozen candidate. The identical input is
  used at inference (see :func:`apply_calibration`).
* The map is ``p_cal = sigmoid(a * p_raw + b)``, fit as a 1-D logistic
  regression on ``p_raw`` with equal-home weights
  ``N / (H * n_home)`` on the calibration segment (home influence
  equalized; weights recorded on the result). The base estimator stays
  frozen.
* Saved/reloaded identity is verified by
  :func:`kindwindow.models.freeze.verify_frozen_predictions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from kindwindow.models.baselines import equal_home_weights

CALIBRATION_MIN_EACH_CLASS = 50
# Pinned calibration input: raw positive-class probability.
CALIBRATION_INPUT = "raw_positive_class_probability"


@dataclass
class CalibrationResult:
    """Outcome of the calibration gate + (optional) sigmoid fit."""

    fitted: bool
    reason: str
    n_calibration: int = 0
    n_positive: int = 0
    n_negative: int = 0
    n_homes: int = 0
    coef: float | None = None  # a in sigmoid(a * p_raw + b)
    intercept: float | None = None  # b in sigmoid(a * p_raw + b)
    input: str = CALIBRATION_INPUT
    weights: str = "equal-home N/(H*n_home) on calibration"
    convergence_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fitted": self.fitted,
            "reason": self.reason,
            "n_calibration": self.n_calibration,
            "n_positive": self.n_positive,
            "n_negative": self.n_negative,
            "n_homes": self.n_homes,
            "coef": self.coef,
            "intercept": self.intercept,
            "input": self.input,
            "weights": self.weights,
            "convergence_warnings": list(self.convergence_warnings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationResult":
        return cls(
            fitted=bool(data["fitted"]),
            reason=str(data["reason"]),
            n_calibration=int(data["n_calibration"]),
            n_positive=int(data["n_positive"]),
            n_negative=int(data["n_negative"]),
            n_homes=int(data["n_homes"]),
            coef=data.get("coef"),
            intercept=data.get("intercept"),
            input=str(data.get("input", CALIBRATION_INPUT)),
            weights=str(data.get("weights", "")),
            convergence_warnings=list(data.get("convergence_warnings", [])),
        )


def apply_calibration(p_raw: np.ndarray, calibration: CalibrationResult) -> np.ndarray:
    """Apply the frozen calibration map to raw probabilities.

    Identity when the calibration gate failed (``fitted`` is False).
    The input is always the raw positive-class probability.
    """
    p = np.asarray(p_raw, dtype=np.float64)
    if not calibration.fitted:
        return p.copy()
    z = calibration.coef * p + calibration.intercept
    return 1.0 / (1.0 + np.exp(-z))


def fit_calibration(
    p_raw_calibration: np.ndarray,
    calibration_df: pd.DataFrame,
    min_each_class: int = CALIBRATION_MIN_EACH_CLASS,
) -> CalibrationResult:
    """Fit (or refuse) the sigmoid on the calibration segment only.

    ``p_raw_calibration`` must be the frozen candidate's raw
    positive-class probabilities on ``calibration_df``, in row order.
    """
    y = calibration_df["label"].to_numpy(dtype=np.int64)
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    base = CalibrationResult(
        fitted=False, reason="",
        n_calibration=len(calibration_df), n_positive=n_pos,
        n_negative=n_neg,
        n_homes=len(sorted(calibration_df["home"].unique())),
    )
    if n_pos < min_each_class or n_neg < min_each_class:
        base.reason = (
            f"calibration gate failed: calibration has {n_pos} positives "
            f"and {n_neg} negatives, need >= {min_each_class} of each; "
            "using identity calibration"
        )
        return base
    weights = equal_home_weights(calibration_df)
    est = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000)
    est.fit(
        np.asarray(p_raw_calibration, dtype=np.float64).reshape(-1, 1),
        y, sample_weight=weights,
    )
    base.fitted = True
    base.coef = float(est.coef_[0, 0])
    base.intercept = float(est.intercept_[0])
    base.reason = (
        f"calibration gate passed: {n_pos} positives and {n_neg} negatives "
        f"(>= {min_each_class} each); sigmoid fit on raw positive-class "
        "probability with equal-home weights"
    )
    return base
