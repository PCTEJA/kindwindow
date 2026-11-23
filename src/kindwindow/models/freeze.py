"""Freeze the Step 09 winner + calibration + retention gates (Step 09).

Writes ``local/models/frozen_model.json`` (and, for a forest winner,
``local/models/frozen_forest.joblib`` holding the fitted trees, since
200 trees cannot live in JSON). The JSON carries the winner's
coefficients/params, the calibration map, the retention cutoffs, the
split hash, the config hash, the code revision, and the training /
selection counts. No test quantity is ever written here.

Retention gates (frozen on SELECTION only, clean cohort): rank by
``u = p_raw * (1 - p_raw)`` (low retained), cutoff via
``contracts.retention_threshold`` with equal-home weights. Cutoff-tie
protocol (frozen): windows with ``u`` strictly below the cutoff are
retained; windows exactly at the cutoff are ordered by the stable
SHA-256 hash of their ``(home, origin)`` key and retained in that
order only until the nominal retained weight is reached; the fraction
of cutoff ties retained is recorded. At 100% retention thresholding
is bypassed explicitly (cutoff ``inf``). Gates are applied only later
(Step 11); here they are frozen, not applied to any scored split.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kindwindow.contracts import retention_threshold
from kindwindow.models.baselines import equal_home_weights
from kindwindow.models.calibration import CalibrationResult, apply_calibration
from kindwindow.models.train import (
    FittedCandidate,
    build_full_design,
    predict_proba_candidate,
)


def hash_key(home: str, origin) -> str:
    """Stable hash tiebreak key for one (home, origin) window."""
    t = pd.Timestamp(origin).strftime("%Y-%m-%dT%H:%M:%S")
    return hashlib.sha256(f"{home}|{t}".encode("utf-8")).hexdigest()


def retention_gate_summary(
    uncertainties: np.ndarray,
    weights: np.ndarray,
    keys: list[str],
    target_fraction: float,
) -> dict:
    """Freeze one retention gate: cutoff, tie fraction, retained counts.

    ``keys`` are the stable hash tiebreak keys in row order. Returns a
    JSON-serializable dict. At target 1.0 the cutoff is ``inf`` and
    every window is retained (thresholding bypassed).
    """
    uncertainties = np.asarray(uncertainties, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    total_weight = float(weights.sum())
    # contracts.retention_threshold takes plain sequences (numpy truthiness
    # is ambiguous), so pass lists.
    cutoff = retention_threshold(
        list(map(float, uncertainties)), list(map(float, weights)), target_fraction
    )
    if target_fraction >= 1.0:
        return {
            "target_fraction": target_fraction,
            "cutoff": float("inf"),
            "n_windows": int(len(uncertainties)),
            "n_retained": int(len(uncertainties)),
            "retained_weight_fraction": 1.0,
            "n_below_cutoff": int(len(uncertainties)),
            "n_at_cutoff": 0,
            "weight_below_cutoff": total_weight,
            "weight_at_cutoff": 0.0,
            "tie_fraction_retained": None,
            "tiebreak": "100% retention: thresholding bypassed, all windows retained",
        }
    below = uncertainties < cutoff
    at = uncertainties == cutoff
    weight_below = float(weights[below].sum())
    weight_at = float(weights[at].sum())
    needed = target_fraction * total_weight - weight_below
    tie_fraction = float(min(max(needed / weight_at, 0.0), 1.0)) if weight_at > 0 else 0.0
    # Hash-tiebreak: ties ordered by stable key hash; retain in that
    # order until the nominal retained weight is reached.
    tie_idx = np.flatnonzero(at)
    tie_order = sorted(tie_idx, key=lambda i: keys[i])
    retained_tie_weight = 0.0
    n_retained_ties = 0
    for i in tie_order:
        if retained_tie_weight >= needed - 1e-12:
            break
        retained_tie_weight += float(weights[i])
        n_retained_ties += 1
    return {
        "target_fraction": target_fraction,
        "cutoff": float(cutoff),
        "n_windows": int(len(uncertainties)),
        "n_retained": int(below.sum()) + n_retained_ties,
        "retained_weight_fraction": float(
            (weight_below + retained_tie_weight) / total_weight
        ),
        "n_below_cutoff": int(below.sum()),
        "n_at_cutoff": int(at.sum()),
        "weight_below_cutoff": weight_below,
        "weight_at_cutoff": weight_at,
        "tie_fraction_retained": tie_fraction,
        "tiebreak": (
            "windows with u strictly below cutoff retained; windows exactly "
            "at cutoff ordered by SHA-256(home|origin) ascending and retained "
            "in that order until the nominal retained weight is reached"
        ),
    }


def compute_retention_gates(
    p_raw_selection: np.ndarray, selection: pd.DataFrame, targets: list[float]
) -> dict[str, dict]:
    """Freeze retention gates for each target fraction on SELECTION."""
    uncertainties = np.asarray(p_raw_selection, dtype=np.float64)
    uncertainties = uncertainties * (1.0 - uncertainties)
    weights = equal_home_weights(selection)
    keys = [
        hash_key(str(h), t) for h, t in zip(selection["home"], selection["t"])
    ]
    return {
        str(target): retention_gate_summary(uncertainties, weights, keys, target)
        for target in targets
    }


@dataclass
class FrozenModel:
    """In-memory frozen artifact (winner + calibration + gates)."""

    winner: FittedCandidate
    calibration: CalibrationResult
    retention_gates: dict
    split_sha256: str
    config_sha256: str
    code_revision: str
    n_train: int
    n_selection: int
    n_calibration: int
    selection_scores: list[dict]
    fit_timings_seconds: dict[str, float]
    convergence_warnings: dict[str, list[str]]

    def to_dict(self) -> dict:
        model: dict = {
            "kind": self.winner.kind,
            "name": self.winner.name,
            "config": self.winner.config,
            "params": self.winner.params,
            "feature_names": list(self.winner.preprocessor.feature_names),
            "homes": list(self.winner.preprocessor.homes),
            "preprocessor": {
                "means": dict(self.winner.preprocessor.means),
                "stds": dict(self.winner.preprocessor.stds),
                "recency_scaler": dict(self.winner.preprocessor.recency_scaler),
            },
        }
        if self.winner.kind == "logistic":
            est = self.winner.estimator
            model["coef"] = [float(v) for v in est.coef_[0]]
            model["intercept"] = float(est.intercept_[0])
            model["solver"] = "lbfgs"
            model["max_iter"] = 1000
        else:
            model["forest_file"] = "frozen_forest.joblib"
            model["n_estimators"] = 200
            model["random_state"] = 2718
            model["n_jobs"] = 2
        return {
            "step": "09",
            "winner": model,
            "calibration": self.calibration.to_dict(),
            "retention_gates": self.retention_gates,
            "split_sha256": self.split_sha256,
            "config_sha256": self.config_sha256,
            "code_revision": self.code_revision,
            "n_train": self.n_train,
            "n_selection": self.n_selection,
            "n_calibration": self.n_calibration,
            "selection_scores": self.selection_scores,
            "fit_timings_seconds": self.fit_timings_seconds,
            "convergence_warnings": self.convergence_warnings,
            "test_sealed": (
                "Test split was never read, fit, tuned, or scored in Step 09. "
                "No test metric appears in this artifact."
            ),
        }


def save_frozen(frozen: FrozenModel, out_dir: Path) -> Path:
    """Write the frozen artifact; returns the JSON path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if frozen.winner.kind == "forest":
        import joblib

        joblib.dump(frozen.winner.estimator, out_dir / "frozen_forest.joblib")
    path = out_dir / "frozen_model.json"
    path.write_text(json.dumps(frozen.to_dict(), indent=2), encoding="utf-8")
    return path


def load_frozen(path: Path) -> tuple[dict, object]:
    """Reload a frozen artifact; returns (artifact dict, estimator).

    For a logistic winner the estimator is rebuilt from the stored
    coefficients; for a forest winner it is loaded from the joblib
    sidecar next to the JSON.
    """
    from sklearn.linear_model import LogisticRegression

    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    winner = data["winner"]
    if winner["kind"] == "logistic":
        est = LogisticRegression(C=winner["params"]["C"], solver="lbfgs", max_iter=1000)
        est.coef_ = np.array([winner["coef"]], dtype=np.float64)
        est.intercept_ = np.array([winner["intercept"]], dtype=np.float64)
        est.classes_ = np.array([0, 1])
        est.n_features_in_ = len(winner["coef"])
        estimator = est
    else:
        import joblib

        estimator = joblib.load(path.parent / winner["forest_file"])
    return data, estimator


def predict_frozen(data: dict, estimator, df: pd.DataFrame) -> np.ndarray:
    """Raw positive-class probabilities of a reloaded frozen artifact."""
    from kindwindow.models.train import Preprocessor

    winner = data["winner"]
    prep = Preprocessor(
        means={k: float(v) for k, v in winner["preprocessor"]["means"].items()},
        stds={k: float(v) for k, v in winner["preprocessor"]["stds"].items()},
        recency_scaler={
            k: float(v)
            for k, v in winner["preprocessor"]["recency_scaler"].items()
        },
        homes=list(winner["homes"]),
        feature_names=list(winner["feature_names"]),
    )
    X, _ = build_full_design(df, prep)
    if winner["kind"] == "forest":
        X = X.astype(np.float32)
    return np.asarray(estimator.predict_proba(X)[:, 1], dtype=np.float64)


def verify_frozen_predictions(
    frozen: FrozenModel, df: pd.DataFrame
) -> dict:
    """Check saved/reloaded artifact gives identical predictions.

    ``df`` must be a non-test frame (selection is used by callers).
    Returns max abs difference and a pass flag. Exact bitwise equality
    is not expected for a forest: ``predict_proba`` averages 200 trees
    and the summation order can differ after a joblib reload, giving
    differences at the 1e-16 level. The pass tolerance is 1e-12, far
    below the protocol float_atol of 1e-8.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = save_frozen(frozen, Path(tmp))
        data, estimator = load_frozen(path)
        p_before = predict_proba_candidate(frozen.winner, df)
        p_after = predict_frozen(data, estimator, df)
        # Calibration must also round-trip identically.
        cal = CalibrationResult.from_dict(data["calibration"])
        c_before = apply_calibration(p_before, frozen.calibration)
        c_after = apply_calibration(p_after, cal)
        max_diff = float(np.max(np.abs(p_before - p_after))) if len(p_before) else 0.0
        max_cal_diff = float(np.max(np.abs(c_before - c_after))) if len(p_before) else 0.0
        tolerance = 1e-12
        return {
            "max_abs_diff_raw": max_diff,
            "max_abs_diff_calibrated": max_cal_diff,
            "tolerance": tolerance,
            "identical": bool(max_diff <= tolerance and max_cal_diff <= tolerance),
            "n_rows": int(len(df)),
        }

# ---------------------------------------------------------------------------
# Orchestration (CLI entry point)
# ---------------------------------------------------------------------------


def run_freeze(root, features_dir=None, split_path=None) -> dict:
    """Freeze the Step 09 winner + calibration + retention gates.

    Requires ``kindwindow train`` to have run (fitted candidates in
    ``local/models/candidates_step09.joblib`` and
    ``local/runs/step09/train_result.json``). Fits calibration on the
    calibration segment only, freezes retention gates on selection
    only, writes ``local/models/frozen_model.json`` (+ forest joblib
    sidecar when the winner is a forest), ``docs/freeze-receipt.md``
    and ``local/runs/step09/manifest.json``. Never touches test.
    """
    import json
    from pathlib import Path

    import joblib

    from kindwindow.models.calibration import fit_calibration
    from kindwindow.models.train import (
        _code_revision,
        _versions,
        load_splits,
        predict_proba_candidate,
        sha256_file,
        verify_split_hash,
    )

    root = Path(root)
    run_dir = root / "local" / "runs" / "step09"
    train_result_path = run_dir / "train_result.json"
    candidates_path = root / "local" / "models" / "candidates_step09.joblib"
    if not train_result_path.is_file():
        raise FileNotFoundError(
            f"train result not found: {train_result_path} "
            "(run kindwindow train first)"
        )
    if not candidates_path.is_file():
        raise FileNotFoundError(
            f"fitted candidates not found: {candidates_path} "
            "(run kindwindow train first)"
        )
    train_result = json.loads(train_result_path.read_text(encoding="utf-8"))
    features_dir = Path(features_dir) if features_dir else root / "local" / "features"
    split_path = Path(split_path) if split_path else root / "data" / "manifests" / "split.csv"
    splits = load_splits(features_dir, split_path)
    selection, calibration_df = splits["selection"], splits["calibration"]
    split_hash = verify_split_hash(split_path)
    if split_hash != train_result["split_sha256"]:
        raise ValueError(
            "split.csv hash changed since train "
            f"({train_result['split_sha256']} -> {split_hash}); refusing to freeze"
        )
    config_hash = sha256_file(root / "configs" / "study.json")
    candidates = joblib.load(candidates_path)
    winner_name = train_result["winner"]["name"]
    winner = candidates[winner_name]

    p_raw_cal = predict_proba_candidate(winner, calibration_df)
    calibration = fit_calibration(p_raw_cal, calibration_df)
    p_raw_sel = predict_proba_candidate(winner, selection)
    gates = compute_retention_gates(p_raw_sel, selection, [0.5, 0.75, 1.0])
    scores = train_result["ranking"]

    frozen = FrozenModel(
        winner=winner,
        calibration=calibration,
        retention_gates=gates,
        split_sha256=split_hash,
        config_sha256=config_hash,
        code_revision=_code_revision(root),
        n_train=train_result["n_train"],
        n_selection=train_result["n_selection"],
        n_calibration=train_result["n_calibration"],
        selection_scores=scores,
        fit_timings_seconds=train_result["fit_timings_seconds"],
        convergence_warnings=train_result["convergence_warnings"],
    )
    frozen_path = save_frozen(frozen, root / "local" / "models")
    frozen_sha = sha256_file(frozen_path)
    verification = verify_frozen_predictions(frozen, selection)
    if not verification["identical"]:
        raise ValueError(
            "frozen artifact round-trip mismatch: "
            f"{verification}"
        )

    # Freeze receipt (no test metrics anywhere in it).
    lines = [
        "# KindWindow Step 09 freeze receipt",
        "",
        f"Winner: **{winner_name}** ({winner.config}, {winner.kind}) — "
        f"selection macro Brier {train_result['winner']['loss']:.6f} "
        f"(n_selection={train_result['n_selection']}).",
        "",
        "## Selection ranking (macro Brier, mean of per-home Brier, selection only)",
        "",
        "| rank | candidate | config | macro Brier | complexity | order |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for i, row in enumerate(scores, start=1):
        lines.append(
            f"| {i} | {row['candidate']} | {row['config']} | "
            f"{row['macro_brier']:.6f} | {row['complexity']} | {row['order']} |"
        )
    lines += [
        "",
        f"Fixed recency baseline (C=1, recency-only) selection macro Brier: "
        f"{train_result['recency_baseline_macro_brier']:.6f}.",
        "",
        "## Calibration gate",
        "",
        f"- Outcome: {'fitted' if calibration.fitted else 'identity (gate failed)'}",
        f"- Calibration n={calibration.n_calibration}, "
        f"positives={calibration.n_positive}, negatives={calibration.n_negative}, "
        f"homes={calibration.n_homes}",
        f"- Reason: {calibration.reason}",
        f"- Input pinned: {calibration.input}",
        f"- Coef: {calibration.coef}, intercept: {calibration.intercept}",
        f"- Weights: {calibration.weights}",
        "",
        "## Retention gates (frozen on SELECTION only, u = p_raw*(1-p_raw))",
        "",
        "| target | cutoff | n retained / n | tie fraction retained |",
        "|---:|---:|---:|---:|",
    ]
    for target in ("0.5", "0.75", "1.0"):
        g = gates[target]
        lines.append(
            f"| {target} | {g['cutoff']:.6f} | {g['n_retained']} / {g['n_windows']} | "
            f"{g['tie_fraction_retained']} |"
        )
    lines += [
        "",
        "Tie protocol: windows with u strictly below the cutoff are retained; "
        "windows exactly at the cutoff are ordered by SHA-256(home|origin) "
        "ascending and retained in that order until the nominal retained "
        "weight is reached. At 100% retention thresholding is bypassed "
        "explicitly. Gates are applied only later (Step 11); here they are "
        "frozen, not applied to any scored split.",
        "",
        "## What is frozen",
        "",
        f"- Frozen artifact: `local/models/frozen_model.json` "
        f"(SHA-256 {frozen_sha})"
        + (
            " + `local/models/frozen_forest.joblib` (fitted forest sidecar)"
            if winner.kind == "forest" else ""
        ),
        f"- Winner model: {winner_name} ({winner.kind}), params {winner.params}",
        f"- Feature names ({len(winner.preprocessor.feature_names)}): "
        + ", ".join(winner.preprocessor.feature_names),
        f"- Split SHA-256: {split_hash}",
        f"- Config SHA-256: {config_hash}",
        f"- Code revision: {frozen.code_revision}",
        f"- n_train={frozen.n_train}, n_selection={frozen.n_selection}, "
        f"n_calibration={frozen.n_calibration}",
        f"- Round-trip verification: predictions after save/reload agree "
        f"within tolerance 1e-12 "
        f"(max abs diff raw {verification['max_abs_diff_raw']}, "
        f"calibrated {verification['max_abs_diff_calibrated']}, "
        f"n_rows={verification['n_rows']}) — forest tree-averaging "
        f"summation order differs at the 1e-16 level after reload, "
        f"far below the protocol float_atol of 1e-8",
        "",
        "## Test-sealed statement",
        "",
        "The test split was never read, fit, tuned, or scored in Step 09. "
        "No test metric appears in this receipt or in the frozen artifact.",
        "",
    ]
    receipt_path = root / "docs" / "freeze-receipt.md"
    receipt_path.write_text("\n".join(lines), encoding="utf-8")

    manifest = {
        "step": "09",
        "winner": train_result["winner"],
        "selection_ranking": scores,
        "recency_baseline_macro_brier": train_result["recency_baseline_macro_brier"],
        "calibration": calibration.to_dict(),
        "retention_gates": gates,
        "split_sha256": split_hash,
        "config_sha256": config_hash,
        "code_revision": frozen.code_revision,
        "n_train": frozen.n_train,
        "n_selection": frozen.n_selection,
        "n_calibration": frozen.n_calibration,
        "fit_timings_seconds": train_result["fit_timings_seconds"],
        "convergence_warnings": train_result["convergence_warnings"],
        "frozen_model_path": str(frozen_path),
        "frozen_model_sha256": frozen_sha,
        "round_trip_verification": verification,
        "versions": _versions(),
        "test_sealed": True,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    manifest["freeze_receipt"] = str(receipt_path)
    return manifest
