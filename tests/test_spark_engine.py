"""Spark engine tests (Step 05).

All input is read through Spark's JVM JSON reader (``spark.read.json``);
no test uses ``createDataFrame`` from Python objects and no test uses a
Python UDF. The only driver-side collections are the scored outputs
(3 golden rows / 1 synthetic row), never the event stream.

VM limitation (verified by the coordinator, re-checked standalone in
this step): Python workers fail here with
``java.net.SocketException: Broken pipe`` on ``createDataFrame``. The
skipped test below records that; the engine never takes that path.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest

from kindwindow.engines.spark_engine import (
    PYTHON_WORKER_LIMITATION,
    build_windows,
    explain_plan,
    get_spark,
    load_events,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PLAN_ARTIFACT = ROOT / "tests" / "spark_plan.txt"

# Synthetic cross-month fixture: an event in the prior month must change
# the first next-month output (history + recency carry across months).
# Events (home synth-B):
#   2030-01-31T22:00:00 motion ON   (positive; prior month)
#   2030-01-31T23:55:00 motion OFF  (accepted; prior month, recency anchor)
#   2030-02-01T00:10:00 door OPEN   (positive; target of the 00:00 origin)
#   2030-02-01T00:30:00 motion ON   (positive; span end, makes [00:00,00:30) complete)
# Only origin: 2030-02-01T00:00:00 (first >= first_event+120m with a
# complete target). Expected:
#   count_5m  = [23:55,00:00) -> 23:55 OFF                       = 1
#   count_30m = [23:30,00:00) -> 23:55 OFF                       = 1
#   count_120m= [22:00,00:00) -> 22:00 ON, 23:55 OFF             = 2
#   door_30m  = [23:30,00:00) -> no door events                  = 0
#   recency   = 00:00 - 23:55 (prior month)                      = 300
#   y         = positive in [00:00,00:30) -> 00:10 OPEN          = 1
SYNTH_EVENTS = [
    {
        "home": "synth-B",
        "member": "synthetic.jsonl",
        "row": 1,
        "time": "2030-01-31T22:00:00",
        "sensor": "M1",
        "type": "motion",
        "message": "ON",
    },
    {
        "home": "synth-B",
        "member": "synthetic.jsonl",
        "row": 2,
        "time": "2030-01-31T23:55:00",
        "sensor": "M1",
        "type": "motion",
        "message": "OFF",
    },
    {
        "home": "synth-B",
        "member": "synthetic.jsonl",
        "row": 3,
        "time": "2030-02-01T00:10:00",
        "sensor": "D1",
        "type": "door",
        "message": "OPEN",
    },
    {
        "home": "synth-B",
        "member": "synthetic.jsonl",
        "row": 4,
        "time": "2030-02-01T00:30:00",
        "sensor": "M1",
        "type": "motion",
        "message": "ON",
    },
]
SYNTH_EXPECTED = ("synth-B", "2030-02-01T00:00:00", 1, 1, 2, 0, 300, 1)


@pytest.fixture(scope="module")
def spark():
    """One session per module; records startup time on the session object."""
    start = time.monotonic()
    session = get_spark()
    session._kindwindow_startup_seconds = time.monotonic() - start  # type: ignore[attr-defined]
    yield session
    session.stop()


def _rows(df):
    """Collect scored windows only (tiny outputs); rows as plain tuples."""
    return [
        (
            r["home"],
            r["t"],
            int(r["count_5m"]),
            int(r["count_30m"]),
            int(r["count_120m"]),
            int(r["door_30m"]),
            int(r["recency_seconds"]),
            int(r["y"]),
        )
        for r in df.collect()
    ]


def _golden_expected():
    with (FIXTURES / "golden_windows.csv").open(newline="") as fh:
        return [
            (
                row["home"],
                row["t"],
                int(row["count_5m"]),
                int(row["count_30m"]),
                int(row["count_120m"]),
                int(row["door_30m"]),
                int(row["recency_seconds"]),
                int(row["y"]),
            )
            for row in csv.DictReader(fh)
        ]


def test_spark_golden_parity(spark):
    """Golden fixture parity: Spark windows == golden_windows.csv (3 windows)."""
    windows = build_windows(load_events(spark, FIXTURES / "golden_events.jsonl"))
    assert windows.columns == [
        "home",
        "t",
        "count_5m",
        "count_30m",
        "count_120m",
        "door_30m",
        "recency_seconds",
        "y",
    ]
    assert _rows(windows) == _golden_expected()


def test_spark_prior_month_carry(spark, tmp_path):
    """Prior-month event changes the first next-month output.

    Input is a synthetic JSONL file read via spark.read.json (JVM path).
    If history/recency were computed per-month, count_120m would be 0
    and recency missing; the correct cross-month values are 2 and 300.
    """
    path = tmp_path / "synth.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in SYNTH_EVENTS) + "\n", encoding="utf-8")
    windows = build_windows(load_events(spark, path))
    assert _rows(windows) == [SYNTH_EXPECTED]


def test_spark_plan_artifact(spark):
    """Record SparkSession startup time + query plan in tests/spark_plan.txt."""
    windows = build_windows(load_events(spark, FIXTURES / "golden_events.jsonl"))
    startup = getattr(spark, "_kindwindow_startup_seconds", None)
    startup_line = (
        f"SparkSession startup time (s): {startup:.3f}\n"
        if startup is not None
        else "SparkSession startup time (s): unknown\n"
    )
    plan = explain_plan(windows)
    assert plan and len(plan.strip()) > 0
    PLAN_ARTIFACT.write_text(
        "KindWindow Spark engine — test artifact (Step 05)\n"
        f"Spark version: {spark.version}\n"
        + startup_line
        + f"Input: tests/fixtures/golden_events.jsonl\n"
        f"Output columns: {', '.join(windows.columns)}\n"
        f"Python-worker limitation: {PYTHON_WORKER_LIMITATION}\n"
        "\n--- query plan (df.explain equivalent) ---\n"
        f"{plan}\n",
        encoding="utf-8",
    )
    assert PLAN_ARTIFACT.is_file()
    assert "Project" in plan or "Sort" in plan or "Filter" in plan


@pytest.mark.skip(reason=f"Python-worker path not used by the engine. {PYTHON_WORKER_LIMITATION}")
def test_spark_python_worker_limitation(spark):
    """Documented skip: createDataFrame from Python objects is not used.

    Re-checking it standalone fails with java.net.SocketException:
    Broken pipe in this VM, so it is deliberately not exercised inside
    the suite (a failing Python worker must not poison the session).
    """
    spark.createDataFrame([(1,)], ["x"]).count()


def test_spark_cli_pipeline_fixture(spark, tmp_path):
    """CLI: pipeline --engine spark --workload fixture writes Snappy Parquet."""
    from kindwindow.cli import main

    out = tmp_path / "windows"
    rc = main(["pipeline", "--engine", "spark", "--workload", "fixture", "--output", str(out)])
    assert rc == 0
    parquet_files = list(out.rglob("*.parquet"))
    assert parquet_files, f"no parquet files under {out}"
    assert (out / "home=fixture-A" / "year_month=2030-02").is_dir()


def test_spark_cli_pipeline_real_errors_cleanly():
    """CLI: --workload real errors cleanly (exit 2, no Spark started, no data)."""
    from kindwindow.cli import main

    rc = main(["pipeline", "--engine", "spark", "--workload", "real"])
    assert rc == 2
