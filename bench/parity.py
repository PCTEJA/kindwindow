"""Chunked pandas-vs-Spark window parity checker (Step 10).

Compares two window outputs by sorted stable keys ``(home, t)`` in
bounded chunks — never by aggregate hash alone (a hash can conceal
tolerance or offset errors). Both inputs are read with PyArrow
(columnar; the comparison walk itself is chunked and never materializes
a merged frame) and walked in sorted-key order with a merge join, so
key-only-in-A / key-only-in-B rows (full outer coverage) are found, not
just rows that happen to align positionally.

Inputs: a Parquet file, a partitioned Parquet directory (``home=`` /
``year_month=`` partition columns are dropped), or a CSV file (the
golden ``golden_windows.csv`` shape). The pandas engine emits the full
16-column window schema; the Spark engine emits the 8-column subset
(``y`` instead of ``label``, no ``door_5m`` / ``door_120m`` /
``recency_missing`` / ``hour`` / ``weekday`` / ``prior_freq_per_hour`` /
``member`` / ``segment``). Only columns present on BOTH sides are
compared; the result lists exactly which columns were compared.

Conventions (frozen, kit 06_PERFORMANCE.md):
* Keys ``(home, t)`` must be unique within each side; a duplicate key
  fails the check.
* Integer features and labels compare exactly.
* Floats (``prior_freq_per_hour``) compare with ``abs(a-b) <= 1e-8``
  and relative tolerance 0.
* Missing / infinite: null matches null only (null masks are compared,
  so null-vs-value is a mismatch); NaN matches NaN only; +inf matches
  +inf only, -inf matches -inf only; any inf-vs-finite or NaN-vs-finite
  is a mismatch. ``recency_seconds`` null must agree with
  ``recency_missing`` where that column exists.
* Mismatch diagnostics are redacted: only the key, the column, and the
  two values (first N mismatches, default 10). No raw event data, no
  member/segment payloads, no full row dumps.

Usable as ``python -m bench.parity <a> <b>`` (exit 0 pass, 1 fail,
2 error) or imported: ``compare_paths(a, b)`` / ``compare_tables``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

FLOAT_ATOL = 1e-8
FLOAT_RTOL = 0.0  # frozen: relative tolerance is exactly zero
DEFAULT_CHUNK_ROWS = 100_000
DEFAULT_MAX_MISMATCHES = 10

# Columns the pandas engine emits; the Spark engine emits the subset
# marked below. ``y`` is renamed to ``label`` on load.
KNOWN_COLUMNS = [
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
REQUIRED_COMPARED = [
    "count_5m",
    "count_30m",
    "count_120m",
    "door_30m",
    "recency_seconds",
    "label",
]
FLOAT_COLUMNS = {"prior_freq_per_hour"}
INT_COLUMNS = {
    "count_5m",
    "count_30m",
    "count_120m",
    "door_5m",
    "door_30m",
    "door_120m",
    "recency_seconds",
    "hour",
    "weekday",
    "label",
    "segment",
}
BOOL_COLUMNS = {"recency_missing"}
# Partition columns added by the Parquet writers; never compared.
PARTITION_COLUMNS = {"year_month"}


@dataclass
class ParityResult:
    """Outcome of one chunked parity comparison."""

    passed: bool
    n_a: int = 0
    n_b: int = 0
    n_matched_keys: int = 0
    n_only_a: int = 0
    n_only_b: int = 0
    n_mismatched_rows: int = 0
    duplicate_keys_a: int = 0
    duplicate_keys_b: int = 0
    compared_columns: list[str] = field(default_factory=list)
    mismatches_by_column: dict[str, int] = field(default_factory=dict)
    mismatches: list[dict] = field(default_factory=list)  # first N, redacted
    only_in_a_keys: list[list[str]] = field(default_factory=list)  # first N
    only_in_b_keys: list[list[str]] = field(default_factory=list)  # first N
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """JSON-serialisable summary (safe to store in attempt records)."""
        return {
            "passed": self.passed,
            "n_a": self.n_a,
            "n_b": self.n_b,
            "n_matched_keys": self.n_matched_keys,
            "n_only_a": self.n_only_a,
            "n_only_b": self.n_only_b,
            "n_mismatched_rows": self.n_mismatched_rows,
            "duplicate_keys_a": self.duplicate_keys_a,
            "duplicate_keys_b": self.duplicate_keys_b,
            "compared_columns": self.compared_columns,
            "mismatches_by_column": self.mismatches_by_column,
            "mismatches": self.mismatches,
            "only_in_a_keys": self.only_in_a_keys,
            "only_in_b_keys": self.only_in_b_keys,
            "warnings": self.warnings,
        }


def _floats_match(a: float | None, b: float | None, *, atol: float = FLOAT_ATOL) -> bool:
    """Float equality under the frozen missing/infinite conventions."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= atol  # rtol is 0 by freeze


def _values_match(column: str, a, b, *, atol: float = FLOAT_ATOL) -> bool:
    """One cell comparison under the column's type conventions."""
    if column in FLOAT_COLUMNS:
        return _floats_match(
            None if a is None else float(a), None if b is None else float(b), atol=atol
        )
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if column in BOOL_COLUMNS:
        return bool(a) == bool(b)
    if column in INT_COLUMNS:
        return int(a) == int(b)
    return a == b


def _normalize_table(table: pa.Table, *, source: str) -> pa.Table:
    """Rename/typed-normalize one side's window table to the common schema.

    * ``y`` -> ``label`` (Spark shape).
    * Partition columns (``year_month``) are dropped.
    * ``t`` becomes a ``YYYY-MM-DDTHH:MM:SS`` string in both cases
      (pandas writes a timestamp, Spark writes a string).
    * Unknown columns are dropped; known-but-absent columns stay absent
      (only the intersection is compared later).
    """
    columns: dict[str, pa.Array] = {}
    for orig_name in table.column_names:
        name = "label" if orig_name == "y" else orig_name
        if name in PARTITION_COLUMNS:
            continue
        if name not in KNOWN_COLUMNS:
            continue
        columns[name] = table.column(orig_name)
    if "home" not in columns or "t" not in columns:
        raise ValueError(f"{source}: window output is missing 'home' or 't'")
    t_col = columns["t"]
    if pa.types.is_timestamp(t_col.type):
        # Truncate to seconds first: strftime otherwise keeps ".000000000".
        t_col = t_col.cast(pa.timestamp("s"))
        columns["t"] = pc.strftime(t_col, format="%Y-%m-%dT%H:%M:%S")
    elif pa.types.is_string(t_col.type) or pa.types.is_large_string(t_col.type):
        # Strip any fractional seconds (e.g. "...T10:00:00.000000000").
        columns["t"] = pc.replace_substring_regex(
            t_col.cast(pa.string()), pattern=r"\.\d+$", replacement=""
        )
    else:
        raise ValueError(f"{source}: 't' has unsupported type {t_col.type}")
    columns["home"] = columns["home"].cast(pa.string())
    if "recency_seconds" in columns:
        columns["recency_seconds"] = columns["recency_seconds"].cast(pa.int64())
    if "prior_freq_per_hour" in columns:
        columns["prior_freq_per_hour"] = columns["prior_freq_per_hour"].cast(pa.float64())
    if "recency_missing" in columns:
        columns["recency_missing"] = columns["recency_missing"].cast(pa.bool_())
    for col in INT_COLUMNS & set(columns):
        if col not in ("recency_seconds",):
            columns[col] = columns[col].cast(pa.int64())
    ordered = [c for c in KNOWN_COLUMNS if c in columns]
    return pa.table({c: columns[c] for c in ordered})


def _decode_dictionaries(table: pa.Table) -> pa.Table:
    """Cast dictionary-encoded columns to their value type.

    PyArrow returns dictionary-encoded string columns (as written by the
    pandas ``to_parquet`` path) as ``dictionary<...>`` arrays, which
    cannot be concatenated with plain string arrays from another file.
    """
    fields = []
    for schema_field in table.schema:
        if pa.types.is_dictionary(schema_field.type):
            fields.append(
                pa.field(
                    schema_field.name,
                    schema_field.type.value_type,
                    nullable=schema_field.nullable,
                )
            )
        else:
            fields.append(schema_field)
    return table.cast(pa.schema(fields))


def _read_parquet_dir(path: Path) -> pa.Table:
    """Read a partitioned Parquet directory, resolving partition keys.

    The pandas engine writes the ``home`` column BOTH as a data column
    and as a Hive partition key (``home=...``), which PyArrow refuses to
    merge into one schema. Each file is therefore read on its own: any
    data column shadowed by a partition key is dropped and the partition
    value (from the path) is used instead. ``year_month`` partitions are
    dropped — they are a write-time partition only, never compared.
    """
    files = sorted(path.rglob("*.parquet"))
    if not files:
        raise ValueError(f"{path}: no .parquet files found")
    tables: list[pa.Table] = []
    for file in files:
        # ParquetFile.read (not read_table): read_table would still do Hive
        # partition discovery on a file under home=.../ and merge the
        # partition 'home' with the data-column 'home'.
        table = _decode_dictionaries(pq.ParquetFile(file).read())
        partitions: dict[str, str] = {}
        for part in file.relative_to(path).parts[:-1]:
            if "=" in part:
                key, _, value = part.partition("=")
                partitions[key] = value
        for key in ("home", "year_month"):
            if key in partitions and key in table.column_names:
                table = table.drop_columns(key)
        if "home" in partitions:
            table = table.append_column(
                "home", pa.array([partitions["home"]] * table.num_rows, type=pa.string())
            )
        tables.append(table)
    # Unify schemas (a column missing from one file becomes null there).
    all_columns: list[str] = []
    types: dict[str, pa.DataType] = {}
    for table in tables:
        for name in table.column_names:
            if name not in types:
                types[name] = table.schema.field(name).type
                all_columns.append(name)
    unified = []
    for table in tables:
        cols = {}
        for name in all_columns:
            if name in table.column_names:
                cols[name] = table.column(name)
            else:
                cols[name] = pa.nulls(table.num_rows, type=types[name])
        unified.append(pa.table(cols))
    return pa.concat_tables(unified)


def load_windows(path: str | Path, *, source: str = "") -> pa.Table:
    """Load one window output (Parquet file/dir or CSV) as a normalized table."""
    path = Path(path)
    label = source or str(path)
    if not path.exists():
        raise FileNotFoundError(f"{label}: no such file or directory: {path}")
    if path.is_dir():
        table = _read_parquet_dir(path)
    elif path.suffix.lower() == ".parquet":
        table = _decode_dictionaries(pq.ParquetFile(path).read())
    elif path.suffix.lower() == ".csv":
        import pandas as pd

        frame = pd.read_csv(path, dtype={"home": str, "t": str})
        table = pa.Table.from_pandas(frame, preserve_index=False)
    else:
        raise ValueError(f"{label}: unsupported window output (need .parquet dir/file or .csv)")
    return _normalize_table(table, source=label)


def _recency_consistency(table: pa.Table, *, side: str, chunk_rows: int) -> list[str]:
    """Check recency_missing agrees with the recency_seconds null mask.

    Chunked scan; returns warning strings (empty when consistent or when
    the side does not carry both columns, e.g. Spark output).
    """
    if "recency_seconds" not in table.column_names:
        return []
    if "recency_missing" not in table.column_names:
        return []
    bad = 0
    total = table.num_rows
    for offset in range(0, total, chunk_rows):
        chunk = table.slice(offset, min(chunk_rows, total - offset))
        nulls = pc.is_null(chunk.column("recency_seconds")).to_pylist()
        flags = chunk.column("recency_missing").to_pylist()
        bad += sum(1 for is_null, flag in zip(nulls, flags) if bool(is_null) != bool(flag))
    if bad:
        return [f"{side}: recency_missing disagrees with recency_seconds null mask on {bad} rows"]
    return []


def _count_duplicate_keys(table: pa.Table, *, chunk_rows: int) -> int:
    """Count duplicate (home, t) keys in a table already sorted by key.

    Chunked scan over the two key columns only; a duplicate is a key
    equal to the immediately preceding key in sorted order.
    """
    total = table.num_rows
    duplicates = 0
    prev: tuple | None = None
    for offset in range(0, total, chunk_rows):
        chunk = table.slice(offset, min(chunk_rows, total - offset))
        homes = chunk.column("home").to_pylist()
        times = chunk.column("t").to_pylist()
        for home, t in zip(homes, times):
            key = (home, t)
            if key == prev:
                duplicates += 1
            prev = key
    return duplicates


class _ChunkedRows:
    """Row-at-a-time view over a sorted Arrow table, loaded in chunks.

    Only the current chunk (``chunk_rows`` rows) is materialised as
    Python values at a time; the caller advances with :meth:`peek` /
    :meth:`advance`.
    """

    def __init__(self, table: pa.Table, columns: list[str], *, chunk_rows: int):
        self._table = table
        self._columns = columns
        self._chunk_rows = chunk_rows
        self._offset = 0
        self._rows: list[tuple] = []
        self._pos = 0
        self._load_chunk()

    def _load_chunk(self) -> None:
        total = self._table.num_rows
        if self._offset >= total:
            self._rows = []
            return
        size = min(self._chunk_rows, total - self._offset)
        chunk = self._table.slice(self._offset, size)
        self._offset += size
        cols = [chunk.column(c).to_pylist() for c in self._columns]
        self._rows = list(zip(*cols)) if cols else []
        self._pos = 0

    def peek(self) -> tuple | None:
        """Current row without advancing, or None at end of table."""
        while self._pos >= len(self._rows):
            if self._offset >= self._table.num_rows:
                return None
            self._load_chunk()
            if not self._rows:
                return None
        return self._rows[self._pos]

    def advance(self) -> None:
        self._pos += 1


def compare_tables(
    table_a: pa.Table,
    table_b: pa.Table,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    max_mismatches: int = DEFAULT_MAX_MISMATCHES,
    float_atol: float = FLOAT_ATOL,
) -> ParityResult:
    """Chunked sorted-merge comparison of two normalized window tables."""
    compared = [
        c
        for c in KNOWN_COLUMNS
        if c in ("home", "t")
        or (c in table_a.column_names and c in table_b.column_names and c not in ("home", "t"))
    ]
    compared_values = [c for c in compared if c not in ("home", "t")]
    missing_required = [c for c in REQUIRED_COMPARED if c not in compared_values]
    if missing_required:
        raise ValueError(
            "cannot compare: required column(s) missing from one side: "
            + ", ".join(missing_required)
        )
    result = ParityResult(passed=False, compared_columns=compared_values)
    result.warnings.extend(_recency_consistency(table_a, side="a", chunk_rows=chunk_rows))
    result.warnings.extend(_recency_consistency(table_b, side="b", chunk_rows=chunk_rows))

    sorted_a = table_a.sort_by([("home", "ascending"), ("t", "ascending")])
    sorted_b = table_b.sort_by([("home", "ascending"), ("t", "ascending")])
    result.n_a = sorted_a.num_rows
    result.n_b = sorted_b.num_rows
    result.duplicate_keys_a = _count_duplicate_keys(sorted_a, chunk_rows=chunk_rows)
    result.duplicate_keys_b = _count_duplicate_keys(sorted_b, chunk_rows=chunk_rows)
    cols = ["home", "t", *compared_values]
    rows_a = _ChunkedRows(sorted_a, cols, chunk_rows=chunk_rows)
    rows_b = _ChunkedRows(sorted_b, cols, chunk_rows=chunk_rows)

    def record_mismatch(key: tuple[str, str], column: str, a, b) -> None:
        result.mismatches_by_column[column] = result.mismatches_by_column.get(column, 0) + 1
        if len(result.mismatches) < max_mismatches:
            result.mismatches.append(
                {"home": key[0], "t": key[1], "column": column, "a": a, "b": b}
            )

    while True:
        ra = rows_a.peek()
        rb = rows_b.peek()
        if ra is None and rb is None:
            break
        if ra is None:
            key_b = (rb[0], rb[1])
            result.n_only_b += 1
            if len(result.only_in_b_keys) < max_mismatches:
                result.only_in_b_keys.append([key_b[0], key_b[1]])
            rows_b.advance()
            continue
        if rb is None:
            key_a = (ra[0], ra[1])
            result.n_only_a += 1
            if len(result.only_in_a_keys) < max_mismatches:
                result.only_in_a_keys.append([key_a[0], key_a[1]])
            rows_a.advance()
            continue
        key_a = (ra[0], ra[1])
        key_b = (rb[0], rb[1])
        if key_a < key_b:
            result.n_only_a += 1
            if len(result.only_in_a_keys) < max_mismatches:
                result.only_in_a_keys.append([key_a[0], key_a[1]])
            rows_a.advance()
        elif key_b < key_a:
            result.n_only_b += 1
            if len(result.only_in_b_keys) < max_mismatches:
                result.only_in_b_keys.append([key_b[0], key_b[1]])
            rows_b.advance()
        else:
            result.n_matched_keys += 1
            row_mismatched = False
            for i, column in enumerate(compared_values, start=2):
                if not _values_match(column, ra[i], rb[i], atol=float_atol):
                    row_mismatched = True
                    record_mismatch(key_a, column, ra[i], rb[i])
            if row_mismatched:
                result.n_mismatched_rows += 1
            rows_a.advance()
            rows_b.advance()
    result.passed = (
        result.n_only_a == 0
        and result.n_only_b == 0
        and result.n_mismatched_rows == 0
        and result.duplicate_keys_a == 0
        and result.duplicate_keys_b == 0
    )
    return result


def compare_paths(
    path_a: str | Path,
    path_b: str | Path,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    max_mismatches: int = DEFAULT_MAX_MISMATCHES,
    float_atol: float = FLOAT_ATOL,
) -> ParityResult:
    """Load two window outputs and compare them (see :func:`compare_tables`)."""
    table_a = load_windows(path_a, source="a")
    table_b = load_windows(path_b, source="b")
    return compare_tables(
        table_a,
        table_b,
        chunk_rows=chunk_rows,
        max_mismatches=max_mismatches,
        float_atol=float_atol,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m bench.parity <a> <b> [--json-out PATH]``."""
    parser = argparse.ArgumentParser(prog="bench.parity")
    parser.add_argument("a", help="First window output (Parquet dir/file or CSV)")
    parser.add_argument("b", help="Second window output (Parquet dir/file or CSV)")
    parser.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK_ROWS)
    parser.add_argument("--max-mismatches", type=int, default=DEFAULT_MAX_MISMATCHES)
    parser.add_argument("--float-atol", type=float, default=FLOAT_ATOL)
    parser.add_argument("--json-out", default=None, help="Write the result summary as JSON")
    args = parser.parse_args(argv)
    try:
        result = compare_paths(
            args.a,
            args.b,
            chunk_rows=args.chunk_rows,
            max_mismatches=args.max_mismatches,
            float_atol=args.float_atol,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"bench.parity: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.summary(), indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result.summary(), indent=2), encoding="utf-8")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
