#!/bin/sh
# KindWindow reproduction — POSIX wrapper around scripts/reproduce.py.
# Usage: sh scripts/reproduce.sh [--profile fixture|verify|figures|demo|systems|real] [--full]
#   --profile fixture is the default. See scripts/reproduce.py for the
#   profile contract and the test-once seal (evaluate is never re-run).
# Requires: uv 0.13.9 on PATH, Python 3.12. Java 17 on PATH for Spark work.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH (expected ~/.local/bin/uv, version 0.13.9)" >&2
  exit 2
fi
exec python3 scripts/reproduce.py "$@"
