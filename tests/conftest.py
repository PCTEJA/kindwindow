"""Test path setup: repo root (bench/, generators/) and src/ on sys.path.

The package under test is installed editable from src/, but the
top-level ``bench`` and ``generators`` packages live at the repo root,
which pytest does not add to sys.path on its own.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
