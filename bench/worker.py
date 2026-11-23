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

Real-workload mode (Step 12, ``--input-kind parquet``): both engines
read the SAME normalized Parquet tree (the Step 08 ingest output) and
compute windows from it — there is no JSONL round-trip at cohort scale
because the pandas JSONL path materializes one dataclass per event and
OOMs this VM at 78M events (see ``run_pipeline_from_normalized``).
Timed scope (identical for both engines): read normalized Parquet,
compute windows, write windows as Snappy Parquet partitioned by
home + year-month, commit the output. No normalized copy is written
(the input already is the normalized Parquet); no splits/EDA run.

Two shared normalizations, applied identically inside timing:
* event times are floored to whole seconds. The JSONL interchange
  format and both engines' window contracts are second-resolution;
  the normalized Parquet stores millisecond-precision timestamps as
  ``timestamp[ns]``. Without flooring, pandas-ns recency and
  Spark-second recency differ by 1 s on most rows (a precision
  artifact, not an engine difference), and pandas-ns warmup bounds
  can shift by one half-hour origin. The timed pandas path floors
  ``epoch_ns`` to seconds before ``compute_windows_vectorized``; the
  timed Spark path divides the raw ``time`` nanoseconds by 1e9.
* clock-reversal segmentation follows the frozen
  ``assign_segments_from_epochs`` rule. The pandas path computes it
  in-process (vectorized, ~1 s at cohort scale). The Spark engine has
  no segmentation step, so the Spark path applies the SAME rule's
  boundaries from a precomputed sidecar (``--segment-splits``,
  derived from the identical input bytes outside timing; three homes
  have two segments, the rest one). Both implement identical
  semantics; the Step 12 validation compares the two engines on the
  multi-segment homes to prove it.

Spark reads the Parquet with an explicit schema (PySpark 3.5 cannot
read ``TIMESTAMP(NANOS)`` columns: ``Illegal Parquet type: INT64
(TIMESTAMP(NANOS,false))``). The ``time`` column is declared
``LongType`` so Spark receives the raw epoch nanoseconds, which the
worker then floors to epoch seconds — the same values pandas floors
from ``timestamp[ns]``. No Python UDFs, no driver ``collect()`` on
full data (only ``count()``, a JVM aggregation).
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


def _count_input_events(path: Path, *, input_kind: str) -> int:
    """Event count for an input: JSONL lines, or Parquet metadata rows."""
    if input_kind == "parquet":
        import pyarrow.parquet as pq

        total = 0
        for f in sorted(path.rglob("*.parquet")):
            total += pq.ParquetFile(f).metadata.num_rows
        return total
    return _count_input_lines(path)


def _real_home_dirs(normalized_dir: Path) -> list[tuple[str, Path]]:
    """``(home, dir)`` pairs under a normalized tree.

    Accepts the full tree (``<dir>/home=<h>/...``) or a single home's
    directory (``.../home=<h>``) for validation runs.
    """
    if normalized_dir.name.startswith("home="):
        return [(normalized_dir.name.removeprefix("home="), normalized_dir)]
    homes = sorted(
        p.name.removeprefix("home=")
        for p in normalized_dir.iterdir()
        if p.is_dir() and p.name.startswith("home=")
    )
    if not homes:
        raise ValueError(f"no home=* directories under {normalized_dir}")
    return [(home, normalized_dir / f"home={home}") for home in homes]


def _run_pandas_real(normalized_dir: Path, output_dir: Path) -> dict:
    """Pandas real workload: vectorized features path, per home.

    Mirrors ``kindwindow features`` (Step 08) but without splits/EDA
    and without malformed sidecars (the cohort audit found zero
    malformed rows; all malformed sidecars are empty). Times are floored
    to whole seconds (see module docstring). Writes ``windows/`` +
    ``_COMPLETE.json`` and publishes atomically, like ``run_pipeline``.
    """
    import tempfile

    t_import_start = time.monotonic()
    import numpy as np
    import pandas as pd
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from kindwindow.engines.pandas_engine import _publish, _write_partitioned
    from kindwindow.features.features import compute_windows_vectorized

    startup_seconds = time.monotonic() - t_import_start
    ns_per_second = 1_000_000_000
    frames: list[pd.DataFrame] = []
    total_events = 0
    total_excluded = 0
    total_discarded = 0
    for home, home_dir in _real_home_dirs(normalized_dir):
        files = sorted(home_dir.rglob("*.parquet"))
        if not files:
            raise ValueError(f"no parquet files for home {home} under {home_dir}")
        row_parts: list[np.ndarray] = []
        time_parts: list[np.ndarray] = []
        door_parts: list[np.ndarray] = []
        pos_parts: list[np.ndarray] = []
        members: set[str] = set()
        for f in files:
            table = pq.ParquetFile(f).read(
                columns=["row", "time", "type", "is_positive", "member"]
            )
            members.update(table.column("member").to_pylist())
            row_parts.append(table.column("row").to_numpy(zero_copy_only=False))
            time_parts.append(
                table.column("time").to_numpy(zero_copy_only=False).astype("datetime64[ns]")
            )
            door_parts.append(
                pc.equal(table.column("type"), "door").to_numpy(zero_copy_only=False)
            )
            pos_parts.append(
                table.column("is_positive").to_numpy(zero_copy_only=False).astype(bool)
            )
        if len(members) != 1:
            raise ValueError(f"home {home}: expected one member, got {sorted(members)}")
        rows = np.concatenate(row_parts)
        epoch_ns = np.concatenate(time_parts).astype(np.int64)
        is_door = np.concatenate(door_parts).astype(bool)
        is_positive = np.concatenate(pos_parts).astype(bool)
        order = np.argsort(rows, kind="stable")
        epoch_ns = epoch_ns[order]
        # Floor to whole seconds: the shared second-resolution contract.
        epoch_ns = (epoch_ns // ns_per_second) * ns_per_second
        is_door = is_door[order]
        is_positive = is_positive[order]
        windows, report = compute_windows_vectorized(
            home, members.pop(), epoch_ns, is_door, is_positive
        )
        frames.append(windows)
        total_events += int(len(rows))
        total_excluded += int(report["n_excluded_windows"])
        total_discarded += int(report["n_discarded_duplicate_windows"])
        del rows, epoch_ns, is_door, is_positive, windows
    if frames:
        all_windows = pd.concat(frames, ignore_index=True)
        all_windows = all_windows.sort_values(["home", "t"], kind="stable").reset_index(drop=True)
    else:
        all_windows = pd.DataFrame()
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=str(output_dir.parent))
    )
    try:
        window_files = _write_partitioned(
            all_windows, tmp_dir / "windows", time_col="t", stem="windows"
        )
        manifest = {
            "workload": "real",
            "input": str(normalized_dir),
            "time_resolution": "seconds (floored from timestamp[ns])",
            "n_events": total_events,
            "n_windows": len(all_windows),
            "n_excluded_windows": total_excluded,
            "n_discarded_duplicate_windows": total_discarded,
            "window_files": [str(p) for p in window_files],
        }
        (tmp_dir / "_COMPLETE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _publish(tmp_dir, output_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return {
        "startup_seconds": startup_seconds,
        "n_windows": int(len(all_windows)),
        "n_events_accepted": total_events,
        "n_quarantined": 0,
        "ledger_reconciles": True,
        "n_excluded_windows": total_excluded,
        "n_discarded_duplicate_windows": total_discarded,
    }


def _load_segment_splits(path: str | Path | None) -> dict[str, list[int]]:
    """``{home: [split_row, ...]}`` from a segment-splits sidecar JSON."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        home: [int(r) for r in info.get("split_rows", [])]
        for home, info in data.get("homes", {}).items()
    }


def _run_spark_real(
    normalized_dir: Path,
    output_dir: Path,
    *,
    threads: int,
    shuffle_partitions: int,
    segment_splits: str | Path | None,
) -> dict:
    """Spark real workload: read normalized Parquet, compute windows.

    Reads with an explicit schema (see module docstring: PySpark 3.5
    rejects ``TIMESTAMP(NANOS)``); ``time`` arrives as epoch
    nanoseconds and is floored to epoch seconds, the same values the
    pandas path floors from ``timestamp[ns]``. The segment id is the
    frozen segmentation rule's precomputed boundary applied as a
    ``CASE`` on ``row`` (no join, no UDF, no driver collect).
    ``is_positive`` / ``is_door`` are recomputed from ``type`` /
    ``message`` exactly as :func:`load_events` does for JSONL.
    """
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        BooleanType,
        LongType,
        StringType,
        StructField,
        StructType,
    )

    from kindwindow.engines.spark_engine import build_windows, write_windows

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
        splits = _load_segment_splits(segment_splits)
        schema = StructType(
            [
                StructField("home", StringType(), True),
                StructField("member", StringType(), True),
                StructField("row", LongType(), True),
                StructField("time", LongType(), True),  # epoch ns (TIMESTAMP(NANOS))
                StructField("sensor", StringType(), True),
                StructField("type", StringType(), True),
                StructField("message", StringType(), True),
                StructField("is_positive", BooleanType(), True),
                StructField("release", StringType(), True),
                StructField("checksum", StringType(), True),
            ]
        )
        raw = spark.read.schema(schema).parquet(str(normalized_dir))
        # Segment index: number of split thresholds <= row, per home.
        seg_expr = F.lit(0)
        for home in sorted(splits):
            for k, threshold in enumerate(sorted(splits[home]), start=1):
                seg_expr = F.when(
                    (F.col("home") == home) & (F.col("row") >= threshold),
                    F.lit(k),
                ).otherwise(seg_expr)
        events = (
            raw.withColumn("epoch", (F.col("time") / 1_000_000_000).cast("long"))
            .withColumn(
                "segment",
                F.concat_ws(
                    "|", F.col("home"), F.col("member"), seg_expr.cast("string")
                ),
            )
            .withColumn("seg_idx", seg_expr)
            .withColumn(
                "is_positive",
                F.when(
                    (F.col("type") == "motion") & (F.col("message") == "ON"),
                    F.lit(1),
                )
                .when(
                    (F.col("type") == "door")
                    & F.col("message").isin("OPEN", "CLOSE"),
                    F.lit(1),
                )
                .otherwise(F.lit(0))
                .cast("int"),
            )
            .withColumn(
                "is_door",
                F.when(F.col("type") == "door", F.lit(1)).otherwise(F.lit(0)).cast("int"),
            )
            .filter(F.col("epoch").isNotNull())
            .select("home", "segment", "seg_idx", "epoch", "is_positive", "is_door")
        )
        # Build windows per segment index and drop cross-segment
        # duplicate keys, keeping the FIRST segment's row — the frozen
        # "first segment wins" rule (compute_windows_vectorized). Without
        # this, a multi-segment home emits the same (home, t) key twice
        # (aruba: 37 duplicates) and parity fails on duplicate keys.
        max_seg = max((len(v) for v in splits.values()), default=0)
        windows = None
        keys_so_far = None
        for s in range(max_seg + 1):
            w_s = build_windows(events.filter(F.col("seg_idx") == s))
            if keys_so_far is not None:
                w_s = w_s.join(keys_so_far, on=["home", "t"], how="left_anti")
            w_keys = w_s.select("home", "t")
            keys_so_far = w_keys if keys_so_far is None else keys_so_far.union(w_keys)
            windows = w_s if windows is None else windows.unionByName(w_s)
        n_windows = windows.count()
        output_dir = Path(output_dir)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = output_dir.parent / f".{output_dir.name}.tmp-bench-{os.getpid()}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            write_windows(windows, tmp_dir / "windows")
            manifest = {
                "workload": "real",
                "input": str(normalized_dir),
                "time_resolution": "seconds (epoch ns floored by 1e9)",
                "n_windows": int(n_windows),
                "segment_splits_applied": {h: len(v) for h, v in splits.items() if v},
            }
            (tmp_dir / "_COMPLETE.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
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
            "time_resolution": "seconds (epoch ns floored by 1e9)",
            "segment_splits_applied": {h: len(v) for h, v in splits.items() if v},
        }
    finally:
        spark.stop()


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
    parser.add_argument(
        "--input",
        required=True,
        help="Input normalized-events JSONL, or normalized Parquet dir "
        "with --input-kind parquet",
    )
    parser.add_argument(
        "--input-kind",
        choices=["jsonl", "parquet"],
        default="jsonl",
        help="jsonl: full JSONL pipeline; parquet: real workload (normalized Parquet -> windows)",
    )
    parser.add_argument(
        "--segment-splits",
        default=None,
        help="Segment-splits sidecar JSON (Spark real mode only)",
    )
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
        "input_kind": args.input_kind,
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
        result["input_events"] = _count_input_events(input_path, input_kind=args.input_kind)
        if args.input_kind == "parquet":
            if args.engine == "pandas":
                detail = _run_pandas_real(input_path, output_dir)
            else:
                detail = _run_spark_real(
                    input_path,
                    output_dir,
                    threads=args.threads,
                    shuffle_partitions=args.shuffle_partitions,
                    segment_splits=args.segment_splits,
                )
        elif args.engine == "pandas":
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
