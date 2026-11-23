"""Minimal KindWindow CLI.

PR 01 implements only ``doctor``. Step 06 adds a real ``notes-parse``
command, Step 07 adds a real ``demo`` command, and Step 04 adds a real
``pipeline`` command (pandas engine, fixture workload); all other commands
in the README acceptance contract are stubs that exit nonzero with a
not-implemented message, so a caller can never mistake a stub for a real run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Commands promised by the README / delivery plan. Only "doctor" is real in PR 01.
_FUTURE_COMMANDS = (
    "audit",
    "cohort",
    "train",
    "freeze",
    "evaluate",
    "generate",
    "benchmark",
    "parity",
    "notes-eval",
    "report",
)


def _repo_root() -> Path:
    # src/kindwindow/cli.py -> repo root is three levels up.
    return Path(__file__).resolve().parents[2]


def _fixture_paths() -> dict[str, Path]:
    root = _repo_root()
    return {
        "golden_events": root / "tests" / "fixtures" / "golden_events.jsonl",
        "golden_windows": root / "tests" / "fixtures" / "golden_windows.csv",
    }


def cmd_notes_parse(args: argparse.Namespace) -> int:
    """Parse one free-text note and print the structured fields as JSON."""
    from kindwindow.preferences import parse_note

    print(json.dumps(parse_note(args.text)))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Print the Python version and check the golden fixture exists."""
    print(f"Python {sys.version.split()[0]}")
    print(f"kindwindow: {_repo_root()}")
    ok = True
    for name, path in _fixture_paths().items():
        if path.is_file():
            if name == "golden_events":
                n = sum(1 for _ in path.open(encoding="utf-8"))
                print(f"OK  {name}: {path} ({n} events)")
            else:
                print(f"OK  {name}: {path}")
        else:
            print(f"MISSING  {name}: {path}")
            ok = False
    # Also sanity-check the study config template is valid JSON if present.
    config = _repo_root() / "configs" / "study.json"
    if config.is_file():
        try:
            json.loads(config.read_text(encoding="utf-8"))
            print(f"OK  study config: {config} (valid JSON, pre-freeze template)")
        except json.JSONDecodeError as exc:
            print(f"BAD  study config: {config}: {exc}")
            ok = False
    else:
        print(f"MISSING  study config: {config}")
        ok = False
    if not ok:
        print("doctor: FAILED — see MISSING/BAD lines above")
        return 1
    print("doctor: OK")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Serve the local resident demo app on loopback only (synthetic only)."""
    if not args.synthetic:
        print(
            "kindwindow demo: --synthetic is required (only synthetic demo identities)",
            file=sys.stderr,
        )
        return 2
    if args.host not in ("127.0.0.1", "::1"):
        print(
            f"kindwindow demo: host must be loopback (127.0.0.1 or ::1), got {args.host}",
            file=sys.stderr,
        )
        return 2
    try:
        import uvicorn

        from kindwindow.app import create_app
    except ImportError as exc:  # pragma: no cover - missing runtime dep
        print(f"kindwindow demo: cannot start server: {exc}", file=sys.stderr)
        return 2
    db_path = Path(args.db) if args.db else _repo_root() / "local" / "demo.db"
    app = create_app(db_path)
    print(f"KindWindow local demo (synthetic only) at http://{args.host}:{args.port}")
    print(f"Demo database: {db_path}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_pipeline_spark(args: argparse.Namespace, root: Path) -> int:
    """Spark pipeline: JVM readers only, golden fixture; real refuses cleanly."""
    if args.workload == "real":
        print(
            "kindwindow pipeline: --workload real is not available yet — "
            "no real study data is staged (local/ is empty in this step)",
            file=sys.stderr,
        )
        return 2
    try:
        from kindwindow.engines.spark_engine import (
            build_windows,
            get_spark,
            load_events,
            write_windows,
        )
    except ImportError as exc:
        print(f"kindwindow pipeline: Spark engine unavailable: {exc}", file=sys.stderr)
        return 2
    output = Path(args.out) if args.out else root / "local" / "features" / "spark-fixture"
    try:
        spark = get_spark()
        windows = build_windows(load_events(spark, _fixture_paths()["golden_events"]))
        n = windows.count()
        write_windows(windows, output)
    except Exception as exc:  # Java gateway / Spark startup failures, etc.
        print(f"kindwindow pipeline: Spark run failed: {exc}", file=sys.stderr)
        return 1
    print(f"pipeline: engine=spark workload=fixture windows={n} output={output}")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Run the pipeline (pandas or Spark) on fixtures, or refuse a real run."""
    root = _repo_root()
    if args.engine == "spark":
        return _cmd_pipeline_spark(args, root)
    from kindwindow.engines.pandas_engine import (
        RealDataUnavailableError,
        run_pipeline,
        run_pipeline_from_normalized,
    )

    if args.workload == "real":
        normalized_dir = root / "local" / "normalized"
        out = Path(args.out) if args.out else root / "local" / "features" / "pipeline-real"
        try:
            result = run_pipeline_from_normalized(normalized_dir, out)
        except RealDataUnavailableError as exc:
            print(f"kindwindow pipeline: {exc}", file=sys.stderr)
            return 2
        except (ValueError, OSError) as exc:
            print(f"kindwindow pipeline: real workload failed: {exc}", file=sys.stderr)
            return 1
    else:
        input_path = Path(args.input) if args.input else _fixture_paths()["golden_events"]
        out = Path(args.out) if args.out else root / "local" / "features" / "pipeline-fixture"
        if not input_path.is_file():
            print(f"kindwindow pipeline: fixture input not found: {input_path}", file=sys.stderr)
            return 2
        try:
            result = run_pipeline(input_path, out, timezone_name=args.timezone)
        except (ValueError, OSError) as exc:
            print(f"kindwindow pipeline: fixture workload failed: {exc}", file=sys.stderr)
            return 1
    summary = result.summary()
    print(f"kindwindow pipeline: {args.engine}/{args.workload} -> {result.output_dir}")
    for key in ("total_lines", "accepted_first", "quarantined_first", "replays", "ignored"):
        print(f"  {key}: {summary[key]}")
    print(f"  ledger_reconciles: {summary['ledger_reconciles']}")
    print(f"  n_events: {summary['n_events']}  n_windows: {summary['n_windows']}")
    print(
        f"  n_excluded_windows: {summary['n_excluded_windows']}  "
        f"n_discarded_duplicate_windows: {summary['n_discarded_duplicate_windows']}"
    )
    return 0


def cmd_not_implemented(name: str) -> int:
    print(f"kindwindow {name}: not implemented in PR 01 (see README CLI contract)", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kindwindow", description="KindWindow CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Print Python version and check the golden fixture exists")
    notes_parse = sub.add_parser(
        "notes-parse", help="Parse one free-text note to JSON preference fields"
    )
    notes_parse.add_argument("--text", required=True, help="Free-text note to parse")
    demo = sub.add_parser("demo", help="Serve the local resident demo app (synthetic only)")
    demo.add_argument(
        "--synthetic",
        action="store_true",
        help="Required: use synthetic demo identities only",
    )
    demo.add_argument(
        "--host", default="127.0.0.1", help="Loopback host only (127.0.0.1 or ::1)"
    )
    demo.add_argument("--port", type=int, default=8000, help="Port (default 8000)")
    demo.add_argument(
        "--db", default=None, help="SQLite demo database path (default <repo>/local/demo.db)"
    )
    pipe = sub.add_parser("pipeline", help="Run the validate -> Parquet -> forecast-keys pipeline")
    pipe.add_argument("--engine", choices=["pandas", "spark"], default="pandas")
    pipe.add_argument("--workload", choices=["fixture", "real"], default="fixture")
    pipe.add_argument(
        "--input", default=None, help="JSONL input (fixture workload; default: golden fixture)"
    )
    pipe.add_argument(
        "--out",
        "--output",
        dest="out",
        default=None,
        help="Output directory (default: local/features/pipeline-<workload>)",
    )
    pipe.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone for wall-clock validation, e.g. America/Chicago",
    )
    for name in _FUTURE_COMMANDS:
        sub.add_parser(name, help=f"(not implemented in PR 01) {name}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "notes-parse":
        return cmd_notes_parse(args)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "pipeline":
        return cmd_pipeline(args)
    return cmd_not_implemented(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
