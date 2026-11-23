"""Pandas pipeline: raw/JSON -> validate + reconcile -> normalized Parquet
-> forecast keys, history and labels.

Pipeline
--------
``run_pipeline`` ingests one JSONL file (or, for the real workload, an
already-normalized Parquet tree), validates every row through
:mod:`kindwindow.engines.common`, reconciles the ledger, splits events into
continuous segments at file boundaries / clock reversals, computes forecast
windows with the fast path below, and publishes partitioned Snappy Parquet
under ``<out>/normalized/`` and ``<out>/windows/``.

Fast path
---------
Per segment the events are stable-sorted once by time; counts over the
half-open history windows ``[t-w, t)`` and the target ``[t, t+30m)`` are
then obtained with ``numpy.searchsorted`` (left insertion positions) and
prefix sums over the door / positive indicator arrays. No per-window scan
of the event list happens. The test suite checks this fast path against an
independent naive per-window oracle in ``tests/test_pandas_engine.py``.

Feature contract (per window origin ``t``, home, segment)
-----------------------------------------------------------
* ``count_5m`` / ``count_30m`` / ``count_120m``: all accepted physical
  messages in ``[t-w, t)`` — motion ON *and* OFF, door OPEN *and* CLOSE.
* ``door_5m`` / ``door_30m`` / ``door_120m``: door messages (both states)
  in the same half-open windows.
* ``recency_seconds``: seconds since the most recent earlier accepted
  event; ``None`` with ``recency_missing=True`` when there is none (never
  an invented 0). Eligible windows normally always have one, because the
  120-minute warmup guarantees an earlier event in the segment.
* ``hour`` / ``weekday``: local hour (0-23) and weekday (Monday=0) of ``t``.
* ``prior_freq_per_hour``: ``count_120m`` per elapsed observed hour in the
  prior 120 minutes; the warmup guarantees the full 2 h are observed, so
  the denominator is 2.0. It is redundant with ``count_120m`` by design
  (predeclared in the study config); it is a rate, not a new signal.
* ``label``: 1 iff any positive message (motion ON, door OPEN, door CLOSE)
  falls in ``[t, t+30m)``; motion OFF is valid but not positive.

Exclusions
----------
* A window is dropped when its target intersects a quarantined row with a
  reliable (parseable) time, when its date matches a quarantined row whose
  date is parseable but whose time is not, or when its segment's file has a
  quarantined row whose day cannot be bounded at all (the whole segment is
  excluded rather than guessed at). Quarantined rows that cannot be
  attributed to a member exclude nothing, but are counted in the ledger.
* Overlapping file spans for the same home can produce the same window key
  twice; the first segment in deterministic ``(member, segment index)``
  order wins and later duplicates are counted as discarded, never emitted
  twice. History/recency never bridge files or clock reversals.
* Long event-free gaps do *not* exclude windows nominally (silence yields
  clean zeros); :func:`mark_long_gap_windows` flags windows whose target
  intersects a gap strictly longer than a threshold, for the 6/12/24 h
  sensitivity analyses.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kindwindow.engines.common import (
    HISTORY_MINUTES,
    SCHEMA_EVENTS,
    SCHEMA_WINDOWS,
    WARMUP_MINUTES,
    WINDOW_MINUTES,
    Event,
    Ledger,
    QuarantinedRow,
    Segment,
    assign_segments,
    canonical_hash,
    generate_window_keys,
    parse_record,
)

EVENT_COLUMNS: dict[str, str] = {
    "home": "string",
    "member": "string",
    "row": "int64",
    "time": "datetime64[ns]",
    "sensor": "string",
    "type": "string",
    "message": "string",
    "is_positive": "bool",
    "release": "string",
    "checksum": "string",
}

WINDOW_COLUMNS: list[str] = [
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


class RealDataUnavailableError(FileNotFoundError):
    """Raised when ``--workload real`` finds no real normalized data."""


@dataclass
class IngestResult:
    events: list[Event] = field(default_factory=list)
    quarantined: list[QuarantinedRow] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)


@dataclass
class PipelineResult:
    output_dir: Path
    ledger: Ledger
    n_events: int = 0
    n_windows: int = 0
    n_quarantined: int = 0
    n_excluded_windows: int = 0
    n_discarded_duplicate_windows: int = 0
    normalized_files: list[Path] = field(default_factory=list)
    window_files: list[Path] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "output_dir": str(self.output_dir),
            "total_lines": self.ledger.total_lines,
            "accepted_first": self.ledger.accepted_first,
            "quarantined_first": self.ledger.quarantined_first,
            "replays": self.ledger.replays,
            "ignored": self.ledger.ignored,
            "ledger_reconciles": self.ledger.reconciles,
            "n_events": self.n_events,
            "n_windows": self.n_windows,
            "n_excluded_windows": self.n_excluded_windows,
            "n_discarded_duplicate_windows": self.n_discarded_duplicate_windows,
        }


def ingest_jsonl(
    path: Path,
    *,
    release: str = "",
    timezone_name: str | None = None,
) -> IngestResult:
    """Read a JSONL file, validating and reconciling every line.

    Blank lines are explicitly ignored (counted separately). A repeated
    ``(release, member, checksum, row)`` tuple with identical content is a
    replay, counted separately and not re-accepted; the same row number
    with *different* content is a conflicting duplicate and is quarantined.
    """
    result = IngestResult()
    seen: dict[tuple[str, str, int], str] = {}
    member_default = path.name
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            result.ledger.total_lines += 1
            if not line.strip():
                result.ledger.ignored += 1
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                result.ledger.quarantined_first += 1
                result.quarantined.append(
                    QuarantinedRow(
                        home=None,
                        member=member_default,
                        row=line_no,
                        reason="invalid JSON",
                        raw=line.rstrip("\n"),
                    )
                )
                continue
            if not isinstance(record, dict):
                result.ledger.quarantined_first += 1
                result.quarantined.append(
                    QuarantinedRow(
                        home=None,
                        member=member_default,
                        row=line_no,
                        reason="record is not a JSON object",
                        raw=line.rstrip("\n"),
                    )
                )
                continue
            member = record.get("member") or member_default
            row = record.get("row")
            if isinstance(member, str) and isinstance(row, int) and not isinstance(row, bool):
                key = (release, member, row)
                digest = canonical_hash(record)
                if key in seen:
                    if seen[key] == digest:
                        result.ledger.replays += 1
                        continue
                    # Same row number, different content: quarantine, but keep
                    # whatever location the conflicting record itself carries
                    # so window exclusion stays bounded to that time/day.
                    conflict = parse_record(
                        record,
                        release=release,
                        member_default=member_default,
                        row_default=line_no,
                        timezone_name=timezone_name,
                        raw=line.rstrip("\n"),
                    )
                    if isinstance(conflict, Event):
                        loc_time, loc_day = conflict.time, None
                    else:
                        loc_time, loc_day = conflict.time, conflict.day
                    conflict_home = record.get("home")
                    if not isinstance(conflict_home, str):
                        conflict_home = None
                    result.ledger.quarantined_first += 1
                    result.quarantined.append(
                        QuarantinedRow(
                            home=conflict_home,
                            member=member,
                            row=row,
                            reason="conflicting duplicate row number",
                            raw=line.rstrip("\n"),
                            time=loc_time,
                            day=loc_day,
                        )
                    )
                    continue
                seen[key] = digest
            parsed = parse_record(
                record,
                release=release,
                member_default=member_default,
                row_default=line_no,
                timezone_name=timezone_name,
                raw=line.rstrip("\n"),
            )
            if isinstance(parsed, Event):
                result.ledger.accepted_first += 1
                result.events.append(parsed)
            else:
                result.ledger.quarantined_first += 1
                result.quarantined.append(parsed)
    return result


def events_to_dataframe(events: list[Event]) -> pd.DataFrame:
    """Typed normalized-events frame (stable order = ingestion order)."""
    if not events:
        return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in EVENT_COLUMNS.items()})
    df = pd.DataFrame(
        [
            {
                "home": e.home,
                "member": e.member,
                "row": e.row,
                "time": e.time,
                "sensor": e.sensor,
                "type": e.type,
                "message": e.message,
                "is_positive": e.is_positive,
                "release": e.release,
                "checksum": e.checksum,
            }
            for e in events
        ]
    )
    return df.astype(EVENT_COLUMNS)


def _segment_windows(
    segment: Segment, quarantined: list[QuarantinedRow]
) -> tuple[list[dict], int]:
    """Fast-path windows for one segment; returns (rows, n_excluded)."""
    # Stable sort once by time (Python sort is stable: ties keep row order).
    ordered = sorted(segment.events, key=lambda e: e.time)
    times = np.array([e.time for e in ordered], dtype="datetime64[ns]")
    times_ns = times.astype("datetime64[ns]").astype(np.int64)
    is_door = np.array([e.type == "door" for e in ordered], dtype=np.int64)
    is_pos = np.array([e.is_positive for e in ordered], dtype=np.int64)
    door_prefix = np.concatenate([[0], np.cumsum(is_door)])
    pos_prefix = np.concatenate([[0], np.cumsum(is_pos)])

    keys = generate_window_keys(segment.span_start, segment.span_end)
    member_quarantine = [q for q in quarantined if q.member == segment.member]
    bad_times = sorted(q.time for q in member_quarantine if q.time is not None)
    bad_days = {q.day for q in member_quarantine if q.day is not None}
    unlocatable = any(q.time is None and q.day is None for q in member_quarantine)
    if unlocatable:
        return [], len(keys)

    rows: list[dict] = []
    excluded = 0
    for t in keys:
        t_ns = np.datetime64(t, "ns").astype(np.int64)
        target_end = t + timedelta(minutes=WINDOW_MINUTES)
        if any(t <= bad < target_end for bad in bad_times):
            excluded += 1
            continue
        if t.date() in bad_days:
            excluded += 1
            continue
        idx_t = int(np.searchsorted(times_ns, t_ns, side="left"))
        idx_end = int(
            np.searchsorted(times_ns, np.datetime64(target_end, "ns").astype(np.int64), side="left")
        )
        counts: dict[int, int] = {}
        doors: dict[int, int] = {}
        for w in HISTORY_MINUTES:
            lo_ns = np.datetime64(t - timedelta(minutes=w), "ns").astype(np.int64)
            lo = int(np.searchsorted(times_ns, lo_ns, side="left"))
            counts[w] = idx_t - lo
            doors[w] = int(door_prefix[idx_t] - door_prefix[lo])
        if idx_t > 0:
            recency: int | None = int((t_ns - int(times_ns[idx_t - 1])) // 1_000_000_000)
            recency_missing = False
        else:
            recency = None
            recency_missing = True
        label = int(pos_prefix[idx_end] - pos_prefix[idx_t] > 0)
        rows.append(
            {
                "home": segment.home,
                "t": t,
                "count_5m": counts[5],
                "count_30m": counts[30],
                "count_120m": counts[120],
                "door_5m": doors[5],
                "door_30m": doors[30],
                "door_120m": doors[120],
                "recency_seconds": recency,
                "recency_missing": recency_missing,
                "hour": t.hour,
                "weekday": t.weekday(),
                "prior_freq_per_hour": counts[120] / 2.0,
                "label": label,
                "member": segment.member,
                "segment": segment.index,
            }
        )
    return rows, excluded


def compute_windows(
    events: list[Event], quarantined: list[QuarantinedRow] | None = None
) -> tuple[pd.DataFrame, dict]:
    """Compute forecast windows for all segments.

    Returns ``(windows_df, report)`` where the report counts excluded and
    discarded-duplicate windows. Overlapping spans for the same home emit a
    window key at most once: the first segment in deterministic
    ``(member, segment index)`` order wins.
    """
    quarantined = quarantined or []
    segments = assign_segments(events)
    segments.sort(key=lambda s: (s.member, s.index, s.span_start))
    all_rows: list[dict] = []
    excluded = 0
    for segment in segments:
        rows, n_excluded = _segment_windows(segment, quarantined)
        all_rows.extend(rows)
        excluded += n_excluded
    if not all_rows:
        empty = pd.DataFrame(
            {
                "home": pd.Series(dtype="string"),
                "t": pd.Series(dtype="datetime64[ns]"),
                "count_5m": pd.Series(dtype="int64"),
                "count_30m": pd.Series(dtype="int64"),
                "count_120m": pd.Series(dtype="int64"),
                "door_5m": pd.Series(dtype="int64"),
                "door_30m": pd.Series(dtype="int64"),
                "door_120m": pd.Series(dtype="int64"),
                "recency_seconds": pd.Series(dtype="Int64"),
                "recency_missing": pd.Series(dtype="bool"),
                "hour": pd.Series(dtype="int64"),
                "weekday": pd.Series(dtype="int64"),
                "prior_freq_per_hour": pd.Series(dtype="float64"),
                "label": pd.Series(dtype="int64"),
                "member": pd.Series(dtype="string"),
                "segment": pd.Series(dtype="int64"),
            }
        )
        return empty, {"excluded_windows": excluded, "discarded_duplicate_windows": 0}
    df = pd.DataFrame(all_rows)
    before = len(df)
    df = df.drop_duplicates(subset=["home", "t"], keep="first")
    discarded = before - len(df)
    df = df.sort_values(["home", "t"], kind="stable").reset_index(drop=True)
    df["recency_seconds"] = df["recency_seconds"].astype("Int64")
    for col in ("count_5m", "count_30m", "count_120m", "door_5m", "door_30m", "door_120m",
                "hour", "weekday", "label", "segment"):
        df[col] = df[col].astype("int64")
    df["recency_missing"] = df["recency_missing"].astype("bool")
    df["prior_freq_per_hour"] = df["prior_freq_per_hour"].astype("float64")
    df = df[WINDOW_COLUMNS]
    return df, {"excluded_windows": excluded, "discarded_duplicate_windows": discarded}


def mark_long_gap_windows(
    windows: pd.DataFrame, events: list[Event], *, threshold_hours: float
) -> pd.Series:
    """Flag windows whose target intersects an event-free gap > threshold.

    Gaps are measured between consecutive accepted events *within one
    segment* (never across files / reversals). This is a retrospective
    sensitivity mask only; it never changes the nominal windows.
    """
    flagged = pd.Series(False, index=windows.index)
    if windows.empty or not events:
        return flagged
    threshold = timedelta(hours=threshold_hours)
    by_home: dict[str, list[datetime]] = {}
    for segment in assign_segments(events):
        ordered = sorted(segment.events, key=lambda e: e.time)
        by_home.setdefault(segment.home, []).extend(
            (ordered[i].time, ordered[i + 1].time)
            for i in range(len(ordered) - 1)
            if ordered[i + 1].time - ordered[i].time > threshold
        )
    for idx, row in windows.iterrows():
        start, end = row["t"], row["t"] + timedelta(minutes=WINDOW_MINUTES)
        for gap_start, gap_end in by_home.get(row["home"], []):
            if start < gap_end and gap_start < end:
                flagged.loc[idx] = True
                break
    return flagged


def _write_partitioned(df: pd.DataFrame, base: Path, *, time_col: str, stem: str) -> list[Path]:
    """Write ``df`` as Snappy Parquet partitioned by home + year-month."""
    written: list[Path] = []
    if df.empty:
        return written
    frame = df.copy()
    frame["_ym"] = pd.to_datetime(frame[time_col]).dt.strftime("%Y-%m")
    for (home, ym), group in frame.groupby(["home", "_ym"], sort=True):
        out = base / f"home={home}" / f"year_month={ym}" / f"{stem}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        group.drop(columns=["_ym"]).to_parquet(
            out, engine="pyarrow", compression="snappy", index=False
        )
        written.append(out)
    return written


def _publish(tmp_dir: Path, output_dir: Path) -> None:
    """Atomically replace ``output_dir`` with the completed ``tmp_dir``."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    tmp_dir.rename(output_dir)


def _rebase(files: list[Path], tmp_base: Path, out_base: Path) -> list[Path]:
    """Map temp-dir output paths onto their published locations."""
    return [out_base / p.relative_to(tmp_base) for p in files]


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    *,
    release: str = "",
    timezone_name: str | None = None,
) -> PipelineResult:
    """Run the full JSONL pipeline and publish partitioned Parquet output.

    Outputs are built in a run-specific temp directory next to
    ``output_dir`` and published only after success, with a ``_COMPLETE``
    manifest recording schema versions and reconciliation counts.
    """
    ingest = ingest_jsonl(input_path, release=release, timezone_name=timezone_name)
    if not ingest.ledger.reconciles:
        raise ValueError(f"ledger does not reconcile for {input_path}: {ingest.ledger}")
    events_df = events_to_dataframe(ingest.events)
    windows_df, report = compute_windows(ingest.events, ingest.quarantined)
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=str(output_dir.parent)))
    try:
        normalized_files = _write_partitioned(
            events_df, tmp_dir / "normalized", time_col="time", stem="events"
        )
        window_files = _write_partitioned(
            windows_df, tmp_dir / "windows", time_col="t", stem="windows"
        )
        manifest = {
            "schema_events": SCHEMA_EVENTS,
            "schema_windows": SCHEMA_WINDOWS,
            "warmup_minutes": WARMUP_MINUTES,
            "window_minutes": WINDOW_MINUTES,
            "history_minutes": list(HISTORY_MINUTES),
            **ingest.ledger.__dict__,
            "ledger_reconciles": ingest.ledger.reconciles,
            "n_events": len(ingest.events),
            "n_windows": len(windows_df),
            "n_excluded_windows": report["excluded_windows"],
            "n_discarded_duplicate_windows": report["discarded_duplicate_windows"],
        }
        (tmp_dir / "_COMPLETE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _publish(tmp_dir, output_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return PipelineResult(
        output_dir=output_dir,
        ledger=ingest.ledger,
        n_events=len(ingest.events),
        n_windows=len(windows_df),
        n_quarantined=ingest.ledger.quarantined_first,
        n_excluded_windows=report["excluded_windows"],
        n_discarded_duplicate_windows=report["discarded_duplicate_windows"],
        normalized_files=_rebase(
            normalized_files, tmp_dir / "normalized", output_dir / "normalized"
        ),
        window_files=_rebase(window_files, tmp_dir / "windows", output_dir / "windows"),
    )


def run_pipeline_from_normalized(normalized_dir: Path, output_dir: Path) -> PipelineResult:
    """Compute windows from an already-normalized Parquet tree (real workload).

    Raises :class:`RealDataUnavailableError` when the directory is missing
    or holds no Parquet files. Never falls back to fixtures.

    Memory note (Step 08): this path materializes every event as a
    dataclass, so it is memory-bound at cohort scale (78M events OOMs
    this VM). Cohort-scale windows must use the vectorized
    ``kindwindow.features`` path; this function stays for small
    normalized inputs and the CLI ``--normalized-dir`` test case.
    """
    normalized_dir = Path(normalized_dir)
    files = sorted(normalized_dir.rglob("*.parquet")) if normalized_dir.is_dir() else []
    if not files:
        raise RealDataUnavailableError(
            f"no real normalized data at {normalized_dir}: refusing to substitute fixtures"
        )
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    required = {"home", "member", "row", "time", "sensor", "type", "message", "is_positive"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"normalized data at {normalized_dir} is missing columns: {sorted(missing)}"
        )
    events = [
        Event(
            home=str(r.home),
            member=str(r.member),
            row=int(r.row),
            time=pd.Timestamp(r.time).to_pydatetime(),
            sensor=str(r.sensor),
            type=str(r.type),
            message=str(r.message),
            is_positive=bool(r.is_positive),
            release=str(getattr(r, "release", "")),
            checksum=str(getattr(r, "checksum", "")),
        )
        for r in df.itertuples(index=False)
    ]
    # Preserve file order per member as ingestion order for segment splitting.
    events.sort(key=lambda e: (e.member, e.row))
    windows_df, report = compute_windows(events, [])
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=str(output_dir.parent)))
    try:
        window_files = _write_partitioned(
            windows_df, tmp_dir / "windows", time_col="t", stem="windows"
        )
        manifest = {
            "schema_windows": SCHEMA_WINDOWS,
            "warmup_minutes": WARMUP_MINUTES,
            "window_minutes": WINDOW_MINUTES,
            "history_minutes": list(HISTORY_MINUTES),
            "n_events": len(events),
            "n_windows": len(windows_df),
            "n_excluded_windows": report["excluded_windows"],
            "n_discarded_duplicate_windows": report["discarded_duplicate_windows"],
            "normalized_inputs": [str(f) for f in files],
        }
        (tmp_dir / "_COMPLETE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _publish(tmp_dir, output_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    ledger = Ledger(total_lines=len(events), accepted_first=len(events))
    return PipelineResult(
        output_dir=output_dir,
        ledger=ledger,
        n_events=len(events),
        n_windows=len(windows_df),
        n_excluded_windows=report["excluded_windows"],
        n_discarded_duplicate_windows=report["discarded_duplicate_windows"],
        window_files=_rebase(window_files, tmp_dir / "windows", output_dir / "windows"),
    )
