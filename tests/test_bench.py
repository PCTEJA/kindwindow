"""Step 10: benchmark harness + guardrail tests (tiny workloads only).

No timed 1M/10M runs here: the success smoke uses the 9-event golden
fixture, and the failure smokes use absurdly small caps. Spark is NOT
started in this module (the golden parity test covers the Spark engine
and a Spark worker smoke is run once manually; see the Step 10 report).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bench import guardrails
from bench.guardrails import (
    BenchmarkInProgressError,
    BenchmarkLock,
    effective_ram_bytes,
    memory_cap_bytes,
    preflight,
)
from bench.run_benchmark import (
    ATTEMPT_CSV_HEADER,
    attempt_order,
    plan_attempts,
    run_attempt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
KIT_HEADER = ROOT.parent / "kindwindow-kit" / "data" / "benchmark_attempt_header.csv"


def test_attempt_csv_header_matches_kit_schema():
    kit_header = next(csv.reader((KIT_HEADER).open(newline="")))
    assert ATTEMPT_CSV_HEADER == kit_header


def test_plan_alternates_engine_order_and_reverses_per_workload():
    plan = plan_attempts(["w1", "w2"], attempts_per_engine=3)
    assert len(plan) == 12
    engines_w1 = [p["engine"] for p in plan if p["workload"] == "w1"]
    engines_w2 = [p["engine"] for p in plan if p["workload"] == "w2"]
    assert engines_w1 == ["pandas", "spark", "spark", "pandas", "pandas", "spark"]
    assert engines_w2 == ["spark", "pandas", "pandas", "spark", "spark", "pandas"]
    assert [p["attempt"] for p in plan if p["engine"] == "pandas"][:3] == [1, 2, 3]
    assert [p["order_index"] for p in plan] == list(range(12))
    assert attempt_order(0, 0) == "pandas" and attempt_order(0, 1) == "spark"
    assert attempt_order(1, 0) == "spark" and attempt_order(1, 1) == "pandas"


def test_memory_cap_is_70_percent_of_effective_ram():
    assert memory_cap_bytes(0.7) == int(effective_ram_bytes() * 0.7)
    with pytest.raises(ValueError):
        memory_cap_bytes(1.5)


def test_preflight_ok_with_small_disk_requirement(tmp_path):
    result = preflight(ROOT, min_free_bytes=1024, check_path=tmp_path, require_jvm=True)
    assert result.ok, result.failures
    assert result.checks["memory_cap_bytes"] == memory_cap_bytes(0.7)
    assert result.checks["java_version"] is not None


def test_preflight_blocks_on_insufficient_disk(tmp_path):
    result = preflight(ROOT, min_free_bytes=10**18, check_path=tmp_path)
    assert not result.ok
    assert any("disk" in f for f in result.failures)
    with pytest.raises(guardrails.PreflightError):
        result.raise_if_blocked()


def test_lock_blocks_second_holder_and_reclaims_stale(tmp_path):
    lock_path = tmp_path / "bench.lock"
    with BenchmarkLock(lock_path):
        with pytest.raises(BenchmarkInProgressError):
            BenchmarkLock(lock_path).acquire()
    assert not lock_path.exists()  # released
    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999999, "acquired_utc": "2030-01-01T00:00:00+00:00"}))
    lock = BenchmarkLock(stale)
    lock.acquire()
    assert lock.reclaimed_stale
    lock.release()
    assert not stale.exists()


def test_pandas_smoke_attempt_succeeds(tmp_path):
    runs_dir = tmp_path / "runs"
    record = run_attempt(
        repo_root=ROOT,
        engine="pandas",
        workload="smoke",
        input_path=FIXTURES / "golden_events.jsonl",
        attempt=1,
        order_index=0,
        cap_seconds=120.0,
        runs_dir=runs_dir,
        skip_lock=True,
    )
    assert record["status"] == "success", record
    assert record["input_events"] == 9
    assert record["n_windows"] == 3
    assert record["startup_seconds"] is not None and record["startup_seconds"] >= 0
    assert record["elapsed_seconds"] > 0
    assert record["peak_tree_bytes"] > 0
    assert record["output_bytes"] > 0
    assert record["events_per_second"] is not None
    assert record["orphan_java_remaining"] == []
    assert Path(record["output_dir"]).is_dir()
    assert (runs_dir / "attempts.csv").is_file()
    with (runs_dir / "attempts.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1 and rows[0]["run_id"] == record["run_id"]
    assert rows[0]["status"] == "success"
    sidecar = json.loads((runs_dir / f"{record['run_id']}.json").read_text())
    assert sidecar["environment"]["cpu_model"]
    assert sidecar["environment"]["parquet_codec"] == "snappy"
    assert sidecar["environment"]["memory_cap_bytes"] == record["memory_cap_bytes"]
    assert sidecar["environment"]["git"]["commit"]


def test_timeout_smoke_reports_timeout_and_no_partial_output(tmp_path):
    record = guardrails.smoke_timeout_check(
        repo_root=ROOT,
        engine="pandas",
        cap_seconds=1.0,
        sleep_seconds=5.0,
        runs_dir=tmp_path / "runs",
        tmp_path=tmp_path,
    )
    assert record["status"] == "timeout"
    assert record["terminated_by_monitor"] is True
    assert not Path(record["output_dir"]).exists()
    assert record.get("output_removed") in (True, False)  # nothing to remove is fine
    with (tmp_path / "runs" / "attempts.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["status"] == "timeout"


def test_memory_smoke_reports_memory_limit_and_no_partial_output(tmp_path):
    record = guardrails.smoke_memory_check(
        repo_root=ROOT,
        engine="pandas",
        cap_memory_bytes=1_000_000,
        runs_dir=tmp_path / "runs",
        tmp_path=tmp_path,
    )
    assert record["status"] == "memory_limit"
    assert not Path(record["output_dir"]).exists()
    with (tmp_path / "runs" / "attempts.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["status"] == "memory_limit"


def test_run_attempt_rejects_unknown_engine(tmp_path):
    with pytest.raises(ValueError):
        run_attempt(
            repo_root=ROOT,
            engine="dask",
            workload="smoke",
            input_path=FIXTURES / "golden_events.jsonl",
            runs_dir=tmp_path / "runs",
            skip_lock=True,
        )


def test_run_attempt_rejects_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_attempt(
            repo_root=ROOT,
            engine="pandas",
            workload="smoke",
            input_path=tmp_path / "nope.jsonl",
            runs_dir=tmp_path / "runs",
            skip_lock=True,
        )
