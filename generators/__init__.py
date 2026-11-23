"""Synthetic event generator package (Step 10, train-only).

Lazy re-exports (PEP 562): importing the package must not import the
``synthetic`` submodule eagerly, so ``python -m generators.synthetic``
does not trigger a duplicate-import warning.
"""

__all__ = [
    "PARAMS_SCHEMA",
    "fit_params_from_summary_csv",
    "generate_bytes",
    "generate_file",
    "load_params",
    "sha256_hex",
    "summarize_bytes",
    "validate_params",
]


def __getattr__(name: str):
    if name in __all__:
        from generators import synthetic as _synthetic

        return getattr(_synthetic, name)
    raise AttributeError(f"module 'generators' has no attribute {name!r}")
