"""Benchmark harness package (Step 10): parity, guardrails, timed runs.

Lazy re-exports (PEP 562): importing the package must not import the
submodules eagerly, so ``python -m bench.run_benchmark`` /
``python -m bench.parity`` do not trigger duplicate-import warnings.
"""

__all__ = ["guardrails", "parity", "run_benchmark"]


def __getattr__(name: str):
    if name in __all__:
        import importlib

        return importlib.import_module(f"bench.{name}")
    raise AttributeError(f"module 'bench' has no attribute {name!r}")
