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
        normalized_dir = (
            Path(args.normalized_dir) if args.normalized_dir else root / "local" / "normalized"
        )
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


def cmd_ingest_real(args: argparse.Namespace) -> int:
    """Stream the private CASAS archive: vocabulary + normalized Parquet.

    Real data only: refuses when the archive or the cohort file is
    missing, and never substitutes fixtures.
    """
    import csv
    import os
    import time

    import numpy as np

    from kindwindow.real_ingest import ingest_home, survey_vocabulary

    root = _repo_root()
    if args.archive:
        archive = Path(args.archive)
    elif os.environ.get("KINDWINDOW_DATA"):
        archive = Path(os.environ["KINDWINDOW_DATA"]) / "data.zip"
    else:
        archive = root / "local" / "data.zip"
    cohort_path = Path(args.cohort) if args.cohort else root / "data" / "manifests" / "cohort.csv"
    out_base = Path(args.out) if args.out else root / "local"
    if not archive.is_file():
        print(
            f"kindwindow ingest-real: archive not found: {archive} "
            f"(refusing to substitute fixtures)",
            file=sys.stderr,
        )
        return 2
    if not cohort_path.is_file():
        print(f"kindwindow ingest-real: cohort file not found: {cohort_path}", file=sys.stderr)
        return 2
    homes = []
    with cohort_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row["inclusion_category"] == "selected":
                homes.append(row["pseudonym"])
    homes.sort(key=str.casefold)
    if args.homes:
        wanted = set(args.homes.split(","))
        homes = [h for h in homes if h in wanted]
    if not homes:
        print("kindwindow ingest-real: no selected homes to ingest", file=sys.stderr)
        return 2
    run_dir = out_base / "runs" / "step08"
    sidecars = run_dir / "sidecars"
    sidecars.mkdir(parents=True, exist_ok=True)
    normalized_dir = out_base / "normalized"
    ledgers: dict = {}
    t_start = time.time()
    if args.survey:
        print("kindwindow ingest-real: vocabulary survey (one streaming pass)...")
        survey = survey_vocabulary(archive, homes)
        vocab_path = root / "reports" / "tables" / "message_vocabulary.csv"
        vocab_path.parent.mkdir(parents=True, exist_ok=True)
        with vocab_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["home", "sensor", "message", "count"])
            writer.writeheader()
            writer.writerows(survey.as_rows())
        for home in homes:
            led = survey.ledgers[home]
            ledgers.setdefault(home, {})["survey"] = {
                "total": led.total,
                "accepted": led.accepted,
                "quarantined": led.quarantined,
                "malformed": led.malformed,
                "ignored": led.ignored,
                "quarantine_by_category": dict(led.quarantine_by_category),
            }
        print(f"kindwindow ingest-real: survey done ({time.time() - t_start:.1f}s)")
    for home in homes:
        t0 = time.time()
        result = ingest_home(archive, home, normalized_dir)
        np.save(sidecars / f"{home}.quarantined_times_ns.npy", result.quarantined_times_ns)
        np.save(sidecars / f"{home}.malformed_times_ns.npy", result.malformed_times_ns)
        ledgers.setdefault(home, {})["ingest"] = {
            "total": result.ledger.total,
            "accepted": result.ledger.accepted,
            "quarantined": result.ledger.quarantined,
            "malformed": result.ledger.malformed,
            "ignored": result.ledger.ignored,
            "replays": 0,
            "quarantine_by_category": dict(result.ledger.quarantine_by_category),
            "malformed_days": sorted(d.isoformat() for d in result.malformed_days),
            "malformed_unlocatable": result.malformed_unlocatable,
            "n_normalized_files": len(result.normalized_files),
            "elapsed_seconds": result.elapsed_seconds,
            "ledger_reconciles": result.ledger.reconciles,
        }
        print(
            f"kindwindow ingest-real: {home}: total={result.ledger.total} "
            f"accepted={result.ledger.accepted} "
            f"quarantined={result.ledger.quarantined} "
            f"malformed={result.ledger.malformed} "
            f"({time.time() - t0:.1f}s)"
        )
        del result
    (run_dir / "ingest_ledgers.json").write_text(
        json.dumps(
            {"homes": homes, "elapsed_seconds": time.time() - t_start, "ledgers": ledgers}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"kindwindow ingest-real: done ({time.time() - t_start:.1f}s) -> {normalized_dir}")
    return 0


def cmd_features(args: argparse.Namespace) -> int:
    """Vectorized windows from normalized real data + splits + train EDA.

    Refuses when no real normalized data (or no ingest sidecars) exist;
    never substitutes fixtures.
    """
    import hashlib

    import numpy as np
    import pandas as pd

    from kindwindow.engines.pandas_engine import _write_partitioned
    from kindwindow.features.features import (
        assign_splits,
        compute_windows_vectorized,
        training_eda,
    )

    root = _repo_root()
    normalized_dir = Path(args.normalized) if args.normalized else root / "local" / "normalized"
    run_dir = root / "local" / "runs" / "step08"
    sidecars = run_dir / "sidecars"
    ledgers_path = run_dir / "ingest_ledgers.json"
    if not normalized_dir.is_dir() or not list(normalized_dir.rglob("*.parquet")):
        print(
            f"kindwindow features: no real normalized data at {normalized_dir} "
            f"(refusing to substitute fixtures)",
            file=sys.stderr,
        )
        return 2
    if not ledgers_path.is_file():
        print(
            f"kindwindow features: ingest ledgers not found: {ledgers_path} "
            f"(run kindwindow ingest-real first)",
            file=sys.stderr,
        )
        return 2
    ledgers = json.loads(ledgers_path.read_text(encoding="utf-8"))["ledgers"]
    homes = sorted(
        {p.parent.parent.name.removeprefix("home=") for p in normalized_dir.rglob("*.parquet")}
    )
    windows_dir = Path(args.out) if args.out else root / "local" / "features"
    all_windows: list[pd.DataFrame] = []
    reports: dict = {}
    for home in homes:
        files = sorted((normalized_dir / f"home={home}").rglob("*.parquet"))
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.sort_values(["row"], kind="stable").reset_index(drop=True)
        epoch_ns = pd.to_datetime(df["time"]).to_numpy(dtype="datetime64[ns]").astype(np.int64)
        is_door = df["type"].to_numpy() == "door"
        is_positive = df["is_positive"].to_numpy(dtype=bool)
        q_path = sidecars / f"{home}.quarantined_times_ns.npy"
        m_path = sidecars / f"{home}.malformed_times_ns.npy"
        q_times = np.load(q_path) if q_path.is_file() else None
        m_times = np.load(m_path) if m_path.is_file() else None
        ing = ledgers.get(home, {}).get("ingest", {})
        from datetime import date as _date

        m_days = {_date.fromisoformat(d) for d in ing.get("malformed_days", [])}
        windows, report = compute_windows_vectorized(
            home,
            f"data/{home}.csv",
            epoch_ns,
            is_door,
            is_positive,
            malformed_times_ns=m_times,
            malformed_days=m_days,
            malformed_unlocatable=ing.get("malformed_unlocatable", False),
            quarantined_times_ns=q_times,
        )
        reports[home] = report
        print(
            f"kindwindow features: {home}: windows={report['n_windows']} "
            f"segments={report['n_segments']} "
            f"excluded={report['n_excluded_windows']} "
            f"quarantine_counterfactual={report['quarantine_counterfactual_fraction']:.4f}"
        )
        all_windows.append(windows)
        del df, windows
    windows_all = pd.concat(all_windows, ignore_index=True) if all_windows else pd.DataFrame()
    _write_partitioned(windows_all, windows_dir, time_col="t", stem="windows")
    assigned, summary = assign_splits(windows_all)
    split_path = root / "data" / "manifests" / "split.csv"
    split_out = assigned[["home", "t", "split"]].copy()
    split_out["t"] = pd.to_datetime(split_out["t"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    split_out = split_out.sort_values(["home", "t"], kind="stable")
    split_out.to_csv(split_path, index=False)
    split_hash = hashlib.sha256(split_path.read_bytes()).hexdigest()
    summary_path = root / "reports" / "tables" / "split_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    eda = training_eda(assigned)
    eda_path = root / "reports" / "tables" / "training_eda.csv"
    eda.to_csv(eda_path, index=False)
    (run_dir / "windows_reports.json").write_text(
        json.dumps(
            {"windows_reports": reports, "split_hash": split_hash, "n_windows": len(windows_all)},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"kindwindow features: windows={len(windows_all)} -> {windows_dir}")
    print(f"kindwindow features: split.csv SHA-256: {split_hash}")
    return 0


def cmd_baselines(args: argparse.Namespace) -> int:
    """Fit the three baselines on TRAIN; report train + selection Brier.

    Test is sealed: this command never reads the test split's labels for
    reporting (it only needs train/selection rows).
    """
    import pandas as pd

    from kindwindow.models.baselines import evaluate_baselines

    root = _repo_root()
    windows_dir = Path(args.features) if args.features else root / "local" / "features"
    split_path = Path(args.split) if args.split else root / "data" / "manifests" / "split.csv"
    if not windows_dir.is_dir() or not list(windows_dir.rglob("*.parquet")):
        print(
            f"kindwindow baselines: no real windows at {windows_dir} "
            f"(refusing to substitute fixtures)",
            file=sys.stderr,
        )
        return 2
    if not split_path.is_file():
        print(
            f"kindwindow baselines: split file not found: {split_path} "
            f"(run kindwindow features first)",
            file=sys.stderr,
        )
        return 2
    windows = pd.concat(
        [pd.read_parquet(f) for f in sorted(windows_dir.rglob("*.parquet"))],
        ignore_index=True,
    )
    splits = pd.read_csv(split_path)
    splits["t"] = pd.to_datetime(splits["t"])
    merged = windows.merge(splits, on=["home", "t"], how="left", validate="one_to_one")
    if merged["split"].isna().any():
        print("kindwindow baselines: windows without a split assignment", file=sys.stderr)
        return 1
    train = merged[merged["split"] == "train"].reset_index(drop=True)
    selection = merged[merged["split"] == "selection"].reset_index(drop=True)
    result = evaluate_baselines(train, selection)
    out_path = root / "reports" / "tables" / "baselines_selection.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    macro = result[result["home"] == "MACRO"]
    for _, row in macro.iterrows():
        print(
            f"kindwindow baselines: {row['baseline']} {row['split']}: "
            f"macro Brier={row['brier']:.4f} (n={int(row['n'])})"
        )
    print(f"kindwindow baselines: -> {out_path}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Fit the 7 candidates on TRAIN; score on SELECTION only.

    Refuses when the eval split is test, when inputs are missing, or
    when split.csv fails its hash check. Test is sealed.
    """
    if getattr(args, "eval_split", "selection") == "test":
        print(
            "kindwindow train: --eval-split test is refused (test is sealed)",
            file=sys.stderr,
        )
        return 2
    from kindwindow.models.train import run_train

    root = _repo_root()
    try:
        result = run_train(
            root,
            features_dir=getattr(args, "features", None),
            split_path=getattr(args, "split", None),
        )
    except FileNotFoundError as exc:
        print(f"kindwindow train: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"kindwindow train: {exc}", file=sys.stderr)
        return 1
    print(
        f"kindwindow train: winner={result['winner']['name']} "
        f"selection macro Brier={result['winner']['loss']:.4f} "
        f"(n_train={result['n_train']}, n_selection={result['n_selection']})"
    )
    for name, secs in result["fit_timings_seconds"].items():
        print(f"  fit {name}: {secs:.1f}s")
    print(f"kindwindow train: -> {result['selection_scores_csv']}")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    """Freeze the train winner + calibration + retention gates.

    Refuses when test is requested, when train has not run, or when
    split.csv fails its hash check. Test is sealed.
    """
    if getattr(args, "eval_split", "selection") == "test":
        print(
            "kindwindow freeze: --eval-split test is refused (test is sealed)",
            file=sys.stderr,
        )
        return 2
    from kindwindow.models.freeze import run_freeze

    root = _repo_root()
    try:
        result = run_freeze(
            root,
            features_dir=getattr(args, "features", None),
            split_path=getattr(args, "split", None),
        )
    except FileNotFoundError as exc:
        print(f"kindwindow freeze: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"kindwindow freeze: {exc}", file=sys.stderr)
        return 1
    cal = result["calibration"]
    print(
        f"kindwindow freeze: winner={result['winner']['name']} "
        f"calibration={'fitted' if cal['fitted'] else 'identity'} "
        f"(pos={cal['n_positive']}, neg={cal['n_negative']})"
    )
    print(f"kindwindow freeze: -> {result['frozen_model_path']}")
    print(f"kindwindow freeze: receipt -> {result['freeze_receipt']}")
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
    demo.add_argument("--host", default="127.0.0.1", help="Loopback host only (127.0.0.1 or ::1)")
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
    pipe.add_argument(
        "--normalized-dir",
        default=None,
        help="Normalized Parquet dir for --workload real "
        "(default: <repo>/local/normalized). The dataclass path in "
        "run_pipeline_from_normalized is memory-bound: do not point it at "
        "cohort-scale data; use `kindwindow features` (vectorized) instead.",
    )
    for name in _FUTURE_COMMANDS:
        sub.add_parser(name, help=f"(not implemented in PR 01) {name}")
    ingest_real = sub.add_parser(
        "ingest-real", help="Stream the private CASAS archive to normalized Parquet"
    )
    ingest_real.add_argument("--archive", default=None, help="CASAS data.zip path")
    ingest_real.add_argument("--cohort", default=None, help="cohort.csv path")
    ingest_real.add_argument(
        "--out",
        "--output",
        dest="out",
        default=None,
        help="Local output base (default: <repo>/local)",
    )
    ingest_real.add_argument(
        "--homes", default=None, help="Comma-separated subset of selected homes"
    )
    ingest_real.add_argument(
        "--survey",
        action="store_true",
        help="Also run the (sensor, message) vocabulary survey pass",
    )
    features = sub.add_parser(
        "features", help="Vectorized windows + splits + train EDA from normalized real data"
    )
    features.add_argument("--normalized", default=None, help="Normalized Parquet dir")
    features.add_argument(
        "--out",
        "--output",
        dest="out",
        default=None,
        help="Windows output dir (default: <repo>/local/features)",
    )
    baselines = sub.add_parser(
        "baselines", help="Fit baselines on TRAIN; report train + selection Brier"
    )
    baselines.add_argument("--features", default=None, help="Windows Parquet dir")
    baselines.add_argument("--split", default=None, help="split.csv path")
    train_p = sub.add_parser(
        "train", help="Fit the 7 candidates on TRAIN; score on SELECTION only"
    )
    train_p.add_argument("--features", default=None, help="Windows Parquet dir")
    train_p.add_argument("--split", default=None, help="split.csv path")
    train_p.add_argument(
        "--eval-split", default="selection",
        help="Split to score on (selection; test is refused)",
    )
    freeze_p = sub.add_parser(
        "freeze", help="Freeze winner + calibration + retention gates"
    )
    freeze_p.add_argument("--features", default=None, help="Windows Parquet dir")
    freeze_p.add_argument("--split", default=None, help="split.csv path")
    freeze_p.add_argument(
        "--eval-split", default="selection",
        help="Split reference (selection; test is refused)",
    )
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
    if args.command == "ingest-real":
        return cmd_ingest_real(args)
    if args.command == "features":
        return cmd_features(args)
    if args.command == "baselines":
        return cmd_baselines(args)
    if args.command == "train":
        return cmd_train(args)
    if args.command == "freeze":
        return cmd_freeze(args)
    return cmd_not_implemented(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
