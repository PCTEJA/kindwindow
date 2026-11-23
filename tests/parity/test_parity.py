"""Step 10: parity checker tests (synthetic Parquet only, plus golden).

Fast tests build tiny synthetic window tables with PyArrow and check the
chunked comparison rules. The golden test at the end runs both real
engines on the golden fixture (Spark session: run it on its own when
memory is tight) and requires exact parity on the shared columns.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.parity import compare_paths, compare_tables, load_windows
from bench.parity import main as parity_main

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

PANDAS_COLUMNS = [
    "home",
    "t",
    "count_5m",
    "count_30m",
    "count_120m",
    "door_5m",
    "door_30m",
    "door_120m",
    "recency_seconds",
    "recency_missing",
    "hour",
    "weekday",
    "prior_freq_per_hour",
    "label",
    "member",
    "segment",
]
SPARK_COLUMNS = [
    "home",
    "t",
    "count_5m",
    "count_30m",
    "count_120m",
    "door_30m",
    "recency_seconds",
    "y",
]


def _pandas_rows():
    return [
        {
            "home": "h-A",
            "t": "2030-02-01T10:00:00",
            "count_5m": 2,
            "count_30m": 3,
            "count_120m": 4,
            "door_5m": 1,
            "door_30m": 2,
            "door_120m": 2,
            "recency_seconds": 1,
            "recency_missing": False,
            "hour": 10,
            "weekday": 4,
            "prior_freq_per_hour": 2.0,
            "label": 0,
            "member": "synthetic.jsonl",
            "segment": 0,
        },
        {
            "home": "h-A",
            "t": "2030-02-01T10:30:00",
            "count_5m": 0,
            "count_30m": 1,
            "count_120m": 4,
            "door_5m": 0,
            "door_30m": 0,
            "door_120m": 2,
            "recency_seconds": 1800,
            "recency_missing": False,
            "hour": 10,
            "weekday": 4,
            "prior_freq_per_hour": 2.0,
            "label": 1,
            "member": "synthetic.jsonl",
            "segment": 0,
        },
        {
            "home": "h-B",
            "t": "2030-02-01T10:00:00",
            "count_5m": 0,
            "count_30m": 0,
            "count_120m": 0,
            "door_5m": 0,
            "door_30m": 0,
            "door_120m": 0,
            "recency_seconds": None,
            "recency_missing": True,
            "hour": 10,
            "weekday": 4,
            "prior_freq_per_hour": 0.0,
            "label": 0,
            "member": "synthetic.jsonl",
            "segment": 0,
        },
    ]


def _spark_rows_from(pandas_rows):
    return [
        {
            "home": r["home"],
            "t": r["t"],
            "count_5m": r["count_5m"],
            "count_30m": r["count_30m"],
            "count_120m": r["count_120m"],
            "door_30m": r["door_30m"],
            "recency_seconds": r["recency_seconds"],
            "y": r["label"],
        }
        for r in pandas_rows
    ]


def _table(rows, columns):
    arrays = {}
    for col in columns:
        values = [r[col] for r in rows]
        if col == "recency_seconds":
            arrays[col] = pa.array(values, type=pa.int64())
        elif col == "prior_freq_per_hour":
            arrays[col] = pa.array(values, type=pa.float64())
        elif col == "recency_missing":
            arrays[col] = pa.array(values, type=pa.bool_())
        elif col in ("t", "home", "member"):
            arrays[col] = pa.array(values, type=pa.string())
        else:
            arrays[col] = pa.array(values, type=pa.int64())
    return pa.table(arrays)


def _write_parquet_dir(path: Path, rows, columns) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(rows, columns), path / "part-0.parquet", compression="snappy")
    return path


def test_identical_files_pass_chunked(tmp_path):
    rows = _pandas_rows()
    a = _write_parquet_dir(tmp_path / "a", rows, PANDAS_COLUMNS)
    b = _write_parquet_dir(tmp_path / "b", rows, PANDAS_COLUMNS)
    result = compare_paths(a, b, chunk_rows=2)
    assert result.passed
    assert result.n_matched_keys == 3
    assert result.n_only_a == result.n_only_b == 0
    assert result.n_mismatched_rows == 0


def test_pandas_vs_spark_shape_compares_shared_columns_only(tmp_path):
    rows = _pandas_rows()
    a = _write_parquet_dir(tmp_path / "a", rows, PANDAS_COLUMNS)
    b = _write_parquet_dir(tmp_path / "b", _spark_rows_from(rows), SPARK_COLUMNS)
    result = compare_paths(a, b, chunk_rows=2)
    assert result.passed
    assert "door_5m" not in result.compared_columns  # Spark does not emit it
    assert "prior_freq_per_hour" not in result.compared_columns
    for col in ("count_5m", "count_30m", "count_120m", "door_30m", "recency_seconds", "label"):
        assert col in result.compared_columns


def test_one_flipped_label_fails_with_redacted_diagnostic(tmp_path):
    rows_a = _pandas_rows()
    rows_b = _pandas_rows()
    rows_b[1] = dict(rows_b[1], label=0)  # was 1
    a = _write_parquet_dir(tmp_path / "a", rows_a, PANDAS_COLUMNS)
    b = _write_parquet_dir(tmp_path / "b", rows_b, PANDAS_COLUMNS)
    result = compare_paths(a, b, chunk_rows=2)
    assert not result.passed
    assert result.n_mismatched_rows == 1
    assert result.mismatches_by_column == {"label": 1}
    assert result.mismatches[0]["home"] == "h-A"
    assert result.mismatches[0]["t"] == "2030-02-01T10:30:00"
    assert result.mismatches[0]["column"] == "label"
    assert result.mismatches[0]["a"] == 1 and result.mismatches[0]["b"] == 0


def test_float_tolerance_1e9_passes_1e7_fails():
    rows = _pandas_rows()
    base = _table(rows, PANDAS_COLUMNS)
    close = _table(
        [dict(r, prior_freq_per_hour=r["prior_freq_per_hour"] + 1e-9) for r in rows],
        PANDAS_COLUMNS,
    )
    far = _table(
        [dict(r, prior_freq_per_hour=r["prior_freq_per_hour"] + 1e-7) for r in rows],
        PANDAS_COLUMNS,
    )
    assert compare_tables(base, close, chunk_rows=2).passed
    result = compare_tables(base, far, chunk_rows=2)
    assert not result.passed
    assert result.mismatches_by_column.get("prior_freq_per_hour") == 3


def test_missing_key_fails_full_outer_coverage():
    rows = _pandas_rows()
    a = _table(rows, PANDAS_COLUMNS)
    b = _table(rows[:2], PANDAS_COLUMNS)
    result = compare_tables(a, b, chunk_rows=2)
    assert not result.passed
    assert result.n_only_a == 1
    assert result.only_in_a_keys == [["h-B", "2030-02-01T10:00:00"]]


def test_duplicate_key_fails():
    rows = _pandas_rows()
    a = _table(rows, PANDAS_COLUMNS)
    b = _table(rows + [rows[0]], PANDAS_COLUMNS)
    result = compare_tables(a, b, chunk_rows=2)
    assert not result.passed
    assert result.duplicate_keys_b == 1


def test_null_recency_matches_null_only():
    rows = _pandas_rows()
    a = _table(rows, PANDAS_COLUMNS)
    assert compare_tables(a, _table(rows, PANDAS_COLUMNS), chunk_rows=2).passed
    changed = [dict(r) for r in rows]
    changed[2] = dict(changed[2], recency_seconds=5, recency_missing=False)
    result = compare_tables(a, _table(changed, PANDAS_COLUMNS), chunk_rows=2)
    assert not result.passed
    assert result.mismatches_by_column.get("recency_seconds") == 1


def test_infinite_float_conventions():
    rows = _pandas_rows()
    a = _table(rows, PANDAS_COLUMNS)
    inf_rows = [dict(r) for r in rows]
    inf_rows[0] = dict(inf_rows[0], prior_freq_per_hour=float("inf"))
    b = _table(inf_rows, PANDAS_COLUMNS)
    assert compare_tables(b, b, chunk_rows=2).passed  # +inf matches +inf
    result = compare_tables(a, b, chunk_rows=2)
    assert not result.passed  # finite vs +inf is a mismatch


def test_recency_missing_inconsistency_is_warned():
    rows = _pandas_rows()
    bad = [dict(r) for r in rows]
    bad[0] = dict(bad[0], recency_missing=True)  # recency 1 is not null
    result = compare_tables(_table(rows, PANDAS_COLUMNS), _table(bad, PANDAS_COLUMNS))
    assert result.warnings, "expected a recency-consistency warning"
    assert not result.passed  # the flag itself also differs


def test_cli_exit_codes(tmp_path, capsys):
    rows = _pandas_rows()
    a = _write_parquet_dir(tmp_path / "a", rows, PANDAS_COLUMNS)
    b = _write_parquet_dir(tmp_path / "b", rows, PANDAS_COLUMNS)
    assert parity_main([str(a), str(b)]) == 0
    bad = _write_parquet_dir(tmp_path / "bad", [dict(rows[0], label=1)], PANDAS_COLUMNS)
    assert parity_main([str(a), str(bad)]) == 1
    assert parity_main([str(tmp_path / "missing"), str(b)]) == 2


def test_golden_csv_loads_as_spark_shape():
    table = load_windows(FIXTURES / "golden_windows.csv", source="golden")
    assert table.num_rows == 3
    assert table.column("label").to_pylist() == [0, 1, 0]


# ---------------------------------------------------------------------------
# Golden fixture: real pandas engine vs real Spark engine (slow; run alone
# when memory is tight — one Spark session for the module).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark():
    from kindwindow.engines.spark_engine import get_spark

    session = get_spark()
    yield session
    session.stop()


def test_golden_fixture_pandas_vs_spark(spark, tmp_path):
    """Parity on the golden fixture: pandas windows == Spark windows."""
    from kindwindow.engines.pandas_engine import run_pipeline
    from kindwindow.engines.spark_engine import build_windows, load_events, write_windows

    pandas_out = tmp_path / "pandas-out"
    run_pipeline(FIXTURES / "golden_events.jsonl", pandas_out)
    spark_out = tmp_path / "spark-out"
    write_windows(build_windows(load_events(spark, FIXTURES / "golden_events.jsonl")), spark_out)
    result = compare_paths(pandas_out / "windows", spark_out, chunk_rows=2)
    assert result.passed, result.summary()
    assert result.n_matched_keys == 3
    assert result.compared_columns == [
        "count_5m",
        "count_30m",
        "count_120m",
        "door_30m",
        "recency_seconds",
        "label",
    ]
    # Cross-check against the golden CSV oracle too.
    expected = [
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
        for row in csv.DictReader((FIXTURES / "golden_windows.csv").open(newline=""))
    ]
    table = load_windows(spark_out, source="spark-golden")
    got = [
        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
        for r in zip(
            table.column("home").to_pylist(),
            table.column("t").to_pylist(),
            table.column("count_5m").to_pylist(),
            table.column("count_30m").to_pylist(),
            table.column("count_120m").to_pylist(),
            table.column("door_30m").to_pylist(),
            table.column("recency_seconds").to_pylist(),
            table.column("label").to_pylist(),
        )
    ]
    assert got == expected
