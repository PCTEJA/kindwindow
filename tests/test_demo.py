"""Step 14 smoke test: the integrated demo runner completes end to end.

Runs ``demo/run_demo.py`` as a subprocess with a small latency sample
(warmup 2, n=5 per endpoint) against a fresh temporary demo database and
output directory, and checks the exit code and the machine-readable
summary. This is a smoke test, not a re-measurement: the authoritative
latency numbers live in ``demo/demo_results.json`` and ``docs/demo.md``.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_run_demo_smoke(tmp_path):
    out_dir = tmp_path / "demo"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "demo" / "run_demo.py"),
            "--port",
            str(_free_port()),
            "--out-dir",
            str(out_dir),
            "--warmup",
            "2",
            "--latency-n",
            "5",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DEMO FINISHED: all checks passed." in proc.stdout
    results = json.loads((out_dir / "demo_results.json").read_text(encoding="utf-8"))
    assert results["commit"]
    assert results["checks"] and all(c["ok"] for c in results["checks"])
    assert set(results["latency"]) == {
        "GET /",
        "GET /preferences",
        "GET /invitations",
        "GET /contact/invitations",
        "POST /preferences",
    }
    for summary in results["latency"].values():
        assert summary["n"] == 5 and summary["errors"] == 0
        assert summary["p50_ms"] > 0 and summary["p95_ms"] >= summary["p50_ms"]
    assert (out_dir / "demo_log.txt").is_file()
    assert (out_dir / "latency_samples.csv").is_file()
    assert (out_dir / "server.log").is_file()
    samples = (out_dir / "latency_samples.csv").read_text().strip().splitlines()
    assert len(samples) == 1 + 5 * 5  # header + 5 endpoints x 5 measured calls
