"""Step 11 evaluation (reduced scope): test-once scoring of the frozen model."""

from kindwindow.evaluation.evaluation import (
    DEGRADATION_FRACTION,
    DEGRADATION_SEED,
    GAP_HOURS,
    RETENTION_TARGETS,
    drop_event_mask,
    flag_long_gap_windows,
    load_frozen_model,
    load_windows_with_splits,
    per_home_table,
    retention_mask,
    run_evaluation,
)

__all__ = [
    "DEGRADATION_FRACTION",
    "DEGRADATION_SEED",
    "GAP_HOURS",
    "RETENTION_TARGETS",
    "drop_event_mask",
    "flag_long_gap_windows",
    "load_frozen_model",
    "load_windows_with_splits",
    "per_home_table",
    "retention_mask",
    "run_evaluation",
]
