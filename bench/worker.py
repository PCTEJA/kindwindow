"""Timed benchmark worker (Step 10): ONE engine, ONE input, ONE attempt.

This is the child process the parent monitor (:mod:`bench.run_benchmark`)
launches fresh per attempt. It is standalone: it never imports the
parent, never talks to the lockfile, and never decides pass/fail — it
just runs one engine end to end and writes a result JSON next to the
output directory. The parent owns timing, caps, termination and the
attempt record.

Timing split recorded here (the parent records its own wall/monotonic
elapsed as well, which is authoritative):
* ``startup_seconds`` — Spark: time to build the SparkSession
  (``SparkSession``-ready timestamp, written separately from the
  end-to-end time); pandas: time to import the engine modules.
* ``elapsed_seconds`` — worker-observed, from process start until the
  output is committed (rename/publish complete).

Output discipline: the Spark path writes to a sibling temp directory
and renames it onto the output path only after ``write_windows``
returns, so a killed worker leaves no partial output at the output
path. The pandas path uses :func:`run_pipeline`, which already
publishes atomically (temp dir + rename) and writes ``_COMPLETE.json``.

Spark session: built here (not via ``get_spark``) so the benchmark can
record and vary the settings that matter — ``local[N]`` worker count,
shuffle partitions, AQE — while keeping ``get_spark``'s safe defaults
(UTC session timezone so local-tick semantics are preserved, loopback
driver host, ``PYSPARK_PYTHON`` = this interpreter). No
``.cache()`` / ``.persist()``, no ``.collect()`` on full data (only
``count()``, a JVM aggregation), no Python UDFs — the JVM-only engine
contract is preserved.

``--sleep-seconds`` is a TEST-ONLY hook for the forced-timeout smoke
check; it sleeps before any engine work and is never used in a real
timed attempt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _count_input_lines(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as fh:
        for _ in fh:
            n += 1
    return n


def _run_pandas(input_path: Path, output_dir: Path) -> dict:
    """Pandas engine: import-ready time is the startup figure."""
    t_import_start = time.monotonic()
    from kindwindow.engines.pandas_engine import run_pipeline

    startup_seconds = time.monotonic() - t_import_start
    result = run_pipeline(input_path, output_dir)
    return {
        "startup_seconds": startup_seconds,
        "n_windows": result.n_windows,
        "n_events_accepted": result.n_events,
        "n_quarantined": result.n_quarantined,
        "ledger_reconciles": result.ledger.reconciles,
    }


def _run_spark(
    input_path: Path, output_dir: Path, *, threads: int, shuffle_partitions: int
) -> dict:
    """Spark engine: session-ready time is the startup figure."""
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    from pyspark.sql import SparkSession

    from kindwindow.engines.spark_engine import build_windows, load_events, write_windows

    t_session_start = time.monotonic()
    spark = (
        SparkSession.builder.appName("KindWindowBench")
        .master(f"local[{threads}]")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.sql.adaptive.enabled", "false")
        .getOrCreate()
    )
    startup_seconds = time.monotonic() - t_session_start
    try:
        windows = build_windows(load_events(spark, input_path))
        n_windows = windows.count()
        tmp_dir = output_dir.parent / f".{output_dir.name}.tmp-bench-{os.getpid()}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            write_windows(windows, tmp_dir)
            os.rename(tmp_dir, output_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        return {
            "startup_seconds": startup_seconds,
            "n_windows": int(n_windows),
            "spark_version": spark.version,
            "spark_master": f"local[{threads}]",
            "shuffle_partitions": shuffle_partitions,
            "adaptive_query_execution": False,
            "session_timezone": "UTC",
        }
    finally:
        spark.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.worker")
    parser.add_argument("--engine", choices=["pandas", "spark"], required=True)
    parser.add_argument("--input", required=True, help="Input normalized-events JSONL")
    parser.add_argument("--output", required=True, help="Output directory (unique per attempt)")
    parser.add_argument("--result", required=True, help="Result JSON path (sibling file)")
    parser.add_argument("--threads", type=int, default=2, help="Worker threads / local[N]")
    parser.add_argument("--shuffle-partitions", type=int, default=4)
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="TEST-ONLY hook: sleep before engine work (forced-timeout smoke check)",
    )
    args = parser.parse_args(argv)

    t_start = time.monotonic()
    started_utc = _utc_now()
    input_path = Path(args.input)
    output_dir = Path(args.output)
    result_path = Path(args.result)
    result: dict = {
        "engine": args.engine,
        "input": str(input_path),
        "output": str(output_dir),
        "started_utc": started_utc,
        "threads": args.threads,
        "shuffle_partitions": args.shuffle_partitions,
        "sleep_seconds": args.sleep_seconds,
        "status": "error",
    }
    try:
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
        result["input_events"] = _count_input_lines(input_path)
        if args.engine == "pandas":
            detail = _run_pandas(input_path, output_dir)
        else:
            detail = _run_spark(
                input_path,
                output_dir,
                threads=args.threads,
                shuffle_partitions=args.shuffle_partitions,
            )
        result.update(detail)
        result["output_bytes"] = _dir_bytes(output_dir) if output_dir.exists() else 0
        result["status"] = "success"
    except Exception as exc:  # worker reports; the parent decides the status
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc(limit=5)
    finally:
        result["elapsed_seconds"] = time.monotonic() - t_start
        result["finished_utc"] = _utc_now()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
