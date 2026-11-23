"""Step 12: benchmark-study tests (train-fit params, summary math, parity tagging).

No timed 1M/10M runs here: the generator test uses n=2000, the summary
and parity tests use tiny synthetic tables, and the real-path test uses
a hand-built 4-event normalized Parquet tree in tmp_path. Spark is NOT
started in this module.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.run_benchmark import (
    _count_input_events,
    _sha256_tree,
    summarize_attempts,
    update_parity_status,
)
from bench.worker import _load_segment_splits, _real_home_dirs, _run_pandas_real
from generators.synthetic import generate_bytes, load_params

ROOT = Path(__file__).resolve().parents[1]
PARAMS_TRAIN = ROOT / "generators" / "params_train.json"


def test_params_train_loads_and_is_deterministic():
    assert PARAMS_TRAIN.is_file(), "Step 12 must write generators/params_train.json"
    params = load_params(PARAMS_TRAIN)
    assert "train" in params.get("provenance", "").lower()
    assert abs(sum(h["probability"] for h in params["homes"]) - 1.0) < 1e-6
    assert len(params["homes"]) == 20
    assert params["fit_defaults_used"] == []
    first = generate_bytes(params, 2000, 941)
    second = generate_bytes(params, 2000, 941)
    assert first == second, "same seed + params must give identical bytes"
    assert hashlib.sha256(first).hexdigest()
    third = generate_bytes(params, 2000, 942)
    assert third != first, "a different seed must change the bytes"


def test_summary_math_on_tiny_attempts_table():
    rows = [
        {"workload": "w", "engine": "pandas", "status": "success",
         "elapsed_seconds": 10.0, "startup_seconds": 1.0, "peak_tree_bytes": 100,
         "output_bytes": 1000, "input_events": 1000, "parity_status": "pass"},
        {"workload": "w", "engine": "pandas", "status": "success",
         "elapsed_seconds": 20.0, "startup_seconds": 2.0, "peak_tree_bytes": 200,
         "output_bytes": 2000, "input_events": 1000, "parity_status": "pass"},
        {"workload": "w", "engine": "pandas", "status": "success",
         "elapsed_seconds": 30.0, "startup_seconds": 3.0, "peak_tree_bytes": 300,
         "output_bytes": 3000, "input_events": 1000, "parity_status": "fail"},
        {"workload": "w", "engine": "pandas", "status": "timeout",
         "elapsed_seconds": 1800.0, "startup_seconds": None, "peak_tree_bytes": 400,
         "output_bytes": 0, "input_events": 1000, "parity_status": "not_run"},
        {"workload": "w", "engine": "spark", "status": "error",
         "elapsed_seconds": 5.0, "startup_seconds": None, "peak_tree_bytes": 50,
         "output_bytes": 0, "input_events": 1000, "parity_status": "not_run"},
    ]
    (summary,) = [s for s in summarize_attempts(rows) if s["engine"] == "pandas"]
    assert summary["attempts"] == 4
    assert summary["successes"] == 3
    assert summary["failures"] == 1
    assert summary["median_elapsed_seconds"] == 20.0
    assert summary["min_elapsed_seconds"] == 10.0
    assert summary["max_elapsed_seconds"] == 30.0
    assert summary["median_startup_seconds"] == 2.0
    assert summary["throughput_events_per_second"] == pytest.approx(1000 / 20.0)
    assert summary["parity_pass"] == 2
    assert summary["parity_fail"] == 1
    assert summary["n_valid"] == 2
    assert summary["valid_median_elapsed_seconds"] == 15.0
    assert summary["valid_throughput_events_per_second"] == pytest.approx(1000 / 15.0)
    (spark_summary,) = [s for s in summarize_attempts(rows) if s["engine"] == "spark"]
    assert spark_summary["successes"] == 0
    assert spark_summary["median_elapsed_seconds"] is None
    assert spark_summary["throughput_events_per_second"] is None


def test_summary_marks_single_success_n1():
    rows = [
        {"workload": "w", "engine": "pandas", "status": "success",
         "elapsed_seconds": 42.0, "startup_seconds": 1.5, "peak_tree_bytes": 10,
         "output_bytes": 10, "input_events": 420, "parity_status": "pass"},
    ]
    (summary,) = summarize_attempts(rows)
    assert summary["n_success"] == 1
    assert summary["median_elapsed_seconds"] == 42.0
    assert summary["min_elapsed_seconds"] == 42.0
    assert summary["max_elapsed_seconds"] == 42.0
    assert summary["throughput_events_per_second"] == pytest.approx(420 / 42.0)


def test_parity_status_propagation_tags_attempts_invalid(tmp_path):
    csv_path = tmp_path / "attempts.csv"
    header = ["run_id", "workload", "engine", "attempt", "status", "parity_status"]
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerow({"run_id": "r1", "workload": "w", "engine": "pandas",
                         "attempt": "1", "status": "success", "parity_status": "not_run"})
        writer.writerow({"run_id": "r2", "workload": "w", "engine": "spark",
                         "attempt": "1", "status": "success", "parity_status": "not_run"})
        writer.writerow({"run_id": "r3", "workload": "w", "engine": "pandas",
                         "attempt": "2", "status": "timeout", "parity_status": "not_run"})
    updated = update_parity_status(csv_path, {"r1": "fail", "r2": "fail"})
    by_id = {r["run_id"]: r for r in updated}
    assert by_id["r1"]["parity_status"] == "fail"
    assert by_id["r2"]["parity_status"] == "fail"
    assert by_id["r3"]["parity_status"] == "not_run"  # untouched: no pair
    summary = summarize_attempts(updated)
    (row,) = [s for s in summary if s["engine"] == "pandas"]
    assert row["n_valid"] == 0
    assert row["valid_median_elapsed_seconds"] is None


def test_segment_splits_loader(tmp_path):
    sidecar = tmp_path / "splits.json"
    sidecar.write_text(json.dumps({
        "homes": {
            "aruba": {"split_rows": [1459332]},
            "hh101": {"split_rows": []},
        }
    }))
    assert _load_segment_splits(sidecar) == {"aruba": [1459332], "hh101": []}
    assert _load_segment_splits(None) == {}
    # A missing sidecar path raises (fail loudly, never silently unsegmented).
    with pytest.raises(FileNotFoundError):
        _load_segment_splits(tmp_path / "missing.json")


def test_real_home_dirs(tmp_path):
    root = tmp_path / "normalized"
    (root / "home=hh101" / "year_month=2011-01").mkdir(parents=True)
    (root / "home=aruba" / "year_month=2010-11").mkdir(parents=True)
    assert [h for h, _ in _real_home_dirs(root)] == ["aruba", "hh101"]
    assert _real_home_dirs(root / "home=aruba") == [("aruba", root / "home=aruba")]


def test_parquet_input_count_and_tree_hash(tmp_path):
    root = tmp_path / "normalized"
    (root / "home=a").mkdir(parents=True)
    table = pa.table({"x": [1, 2, 3]})
    pq.write_table(table, root / "home=a" / "part0.parquet")
    pq.write_table(table, root / "home=a" / "part1.parquet")
    assert _count_input_events(root) == 6
    first = _sha256_tree(root)
    assert _sha256_tree(root) == first
    pq.write_table(pa.table({"x": [1, 2]}), root / "home=a" / "part0.parquet")
    assert _sha256_tree(root) != first


def test_pandas_real_path_on_tiny_normalized_tree(tmp_path):
    """Vectorized real path on 4 events: windows match a direct call."""
    import numpy as np
    import pandas as pd

    from kindwindow.features.features import compute_windows_vectorized

    root = tmp_path / "normalized"
    home_dir = root / "home=tiny" / "year_month=2030-02"
    home_dir.mkdir(parents=True)
    # 5 motion events, 1 h apart starting 08:00; warmup 120m + 30m target
    # give origins at 10:00, 10:30, 11:00, 11:30 (4 windows).
    times = pd.to_datetime(
        ["2030-02-01T08:00:00", "2030-02-01T09:00:00",
         "2030-02-01T10:00:00", "2030-02-01T11:00:00",
         "2030-02-01T12:00:00"]
    )
    frame = pd.DataFrame({
        "home": "tiny",
        "member": "data/tiny.csv",
        "row": [1, 2, 3, 4, 5],
        "time": times,
        "sensor": ["M1"] * 5,
        "type": ["motion"] * 5,
        "message": ["ON", "OFF", "ON", "OFF", "ON"],
        "is_positive": [True, False, True, False, True],
        "release": [""] * 5,
        "checksum": [""] * 5,
    })
    frame.to_parquet(home_dir / "events.parquet", engine="pyarrow", compression="snappy")
    out = tmp_path / "out"
    detail = _run_pandas_real(root, out)
    assert detail["n_events_accepted"] == 5
    assert detail["n_windows"] == 4
    assert (out / "_COMPLETE.json").is_file()
    # Read back via bench.parity.load_windows: the windows tree carries
    # `home` both as a data column and as a Hive partition key, which a
    # plain pd.read_parquet dataset read cannot merge.
    from bench.parity import load_windows

    written = load_windows(out / "windows").to_pandas()
    epoch_ns = times.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    epoch_ns = (epoch_ns // 1_000_000_000) * 1_000_000_000
    expected, _ = compute_windows_vectorized(
        "tiny", "data/tiny.csv", epoch_ns,
        np.array([False] * 5), np.array([True, False, True, False, True]),
    )
    assert len(written) == len(expected)
    assert written["count_120m"].tolist() == expected["count_120m"].tolist()
    assert written["label"].tolist() == expected["label"].tolist()
