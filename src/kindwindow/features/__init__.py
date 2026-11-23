"""Feature construction for KindWindow (Step 08).

Re-exports the frozen feature-column contract and the vectorized window
computation (validated against :mod:`kindwindow.engines.pandas_engine`).
"""

from kindwindow.features.features import (
    FEATURE_COLUMNS,
    FEATURE_DTYPES,
    RECENCY_FEATURE_COLUMNS,
    build_recency_design,
    compute_windows_vectorized,
    encode_home_indicators,
    fit_recency_scaler,
    home_indicator_columns,
    training_eda,
    transform_recency,
)

__all__ = [
    "FEATURE_COLUMNS",
    "FEATURE_DTYPES",
    "RECENCY_FEATURE_COLUMNS",
    "build_recency_design",
    "compute_windows_vectorized",
    "encode_home_indicators",
    "fit_recency_scaler",
    "home_indicator_columns",
    "training_eda",
    "transform_recency",
]
