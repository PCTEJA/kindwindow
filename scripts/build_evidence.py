#!/usr/bin/env python3
"""KindWindow Step 15 — build / verify the evidence index.

Writes reports/evidence.csv: one row per claim/artifact with its SHA-256,
size, how it was produced/verified, and the command that re-verifies it.

Sources of rows:
  * every git-tracked file (committed artifacts of record), except this
    index itself (a file cannot contain its own hash);
  * local (git-ignored, not committed) study artifacts: the frozen model,
    the forest sidecar, the Step 09/11/12 run manifests and results, and
    the raw benchmark attempt records;
  * the private CASAS v1 archive (external to the repo; hashes only).

Usage:
  python3 scripts/build_evidence.py            # (re)build reports/evidence.csv
  python3 scripts/build_evidence.py --check    # verify the committed index
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "evidence.csv"

CLAIMS: dict[str, tuple[str, str]] = {
    # (claim, how produced / verified)
    "reports/tables/test_scores.csv": (
        "Test-once scores: frozen forest vs 3 baselines + retained/gap/degraded "
        "variants, per home and macro (189 rows)",
        "Step 11, `kindwindow evaluate` run once (290 s); sealed — never re-scored",
    ),
    "reports/tables/step11_summary.md": (
        "Test-once narrative: RQ1/RQ3, abstention, 12 h gap, 30% degradation",
        "Step 11 reduced scope; numbers read from local/runs/step11/results.json",
    ),
    "reports/tables/selection_scores.csv": (
        "Candidate selection scores on the selection split (7 candidates)",
        "Step 09, `kindwindow train`; winner forest-d10-leaf10 macro Brier 0.151403",
    ),
    "reports/tables/baselines_selection.csv": (
        "Baseline Brier losses on train and selection splits, per home",
        "Step 08, `kindwindow baselines` on train/selection rows only",
    ),
    "reports/tables/split_summary.csv": (
        "Per-home chronological split boundaries, purge counts, split sizes",
        "Step 08, `kindwindow features`; split SHA-256 65f5a997...",
    ),
    "reports/tables/training_eda.csv": (
        "Training-split hourly prevalence / EDA (500 rows)",
        "Step 08, `kindwindow features` (training rows only)",
    ),
    "reports/tables/message_vocabulary.csv": (
        "Observed (sensor, message) vocabulary survey from the real ingest",
        "Step 08 ingest survey pass over the cohort members",
    ),
    "data/manifests/cohort.csv": (
        "Cohort manifest: 190 rows, 20 selected / 34 eligible-beyond-cap / "
        "exclusions with reasons; SHA-256 407d8ecf...",
        "Step 03 bounded streaming audit of 89 candidates, 183,102,524 rows",
    ),
    "data/manifests/split.csv": (
        "Per-window split assignment for all 1,039,639 eligible windows; SHA-256 65f5a997...",
        "Step 08, `kindwindow features`; per-home chronological 60/10/10/20, 24 h purge",
    ),
    "configs/study.json": (
        "Frozen study configuration (features, seeds, split, calibration); SHA-256 628db5f2...",
        "Frozen in Step 02/08; config hash pinned in docs/freeze-receipt.md",
    ),
    "docs/freeze-receipt.md": (
        "Freeze receipt: winner, selection ranking, calibration gate, "
        "retention gates, frozen-model SHA-256 b2989ae5...",
        "Step 09, `kindwindow freeze`; round-trip reload agreement 1e-12",
    ),
    "docs/cohort-audit.md": (
        "Cohort audit narrative: archive verification, reconciliation, "
        "per-candidate table, exclusions",
        "Step 03 audit run 2026-11-23 01:19-01:32 UTC (739.4 s)",
    ),
    "docs/course-decision.md": (
        "Redacted course decision: TA acceptance of the proposal (student-confirmed)",
        "Recorded from the accepted proposal; no TA correspondence in the packet",
    ),
    "docs/demo.md": (
        "Demo protocol, walkthrough narrative, latency results (52/52 checks)",
        "Step 14, demo/run_demo.py run 22 Nov 2026; demo_results.json + log",
    ),
    "docs/data-dictionary.md": (
        "Source schema, message mappings, units, ambiguous examples",
        "Step 02 contracts freeze; validated against real ingest in Step 08",
    ),
    "docs/message-vocabulary.md": (
        "Message-vocabulary notes (numeric/button payloads, anomalies)",
        "Step 08 ingest survey; see reports/tables/message_vocabulary.csv",
    ),
    "reports/benchmarks/attempts.csv": (
        "All 18 timed benchmark attempts incl. 3 pandas 10M memory_limit failures",
        "Step 12, local/runs/step12/run_study.py (48 min, exclusive machine)",
    ),
    "reports/benchmarks/summary.csv": (
        "Per-workload x engine medians/min/max, startup, peak memory, throughput",
        "Step 12 aggregation from attempts.csv (medians of n=3 successes)",
    ),
    "reports/benchmarks/parity.json": (
        "Per-pair parity outcomes: 6/6 completed pairs pass, 10M not run",
        "Step 12 parity checker (chunked sorted merge, 1e-8 abs tol, rtol 0)",
    ),
    "reports/benchmarks/README.md": (
        "Systems-study narrative (RQ2): machine, scope, results, interpretation",
        "Step 12; medians n=3, parity-passing pairs only",
    ),
    "reports/literature_comparison.csv": (
        "29 literature measurements across 6 papers; 0 genuinely comparable",
        "Step 13, primary sources checked 22 Nov 2026 (see method_comparison.md)",
    ),
    "reports/method_comparison.md": (
        "Literature-comparison narrative: paper-by-paper verdict, non-comparability",
        "Step 13; anchored on frozen test-once + systems numbers, not re-derived",
    ),
    "reports/figures/per_home_brier.svg": (
        "F3: per-home Brier, frozen calibrated vs recency (+ MACRO pair)",
        "Step 11 hand-drawn SVG; bar heights verified by scripts/make_figures.py --check",
    ),
    "reports/figures/runtime_vs_events.svg": (
        "F6: median end-to-end runtime vs input events (pandas vs Spark)",
        "Step 15, scripts/make_figures.py from reports/benchmarks/summary.csv",
    ),
    "reports/figures/memory_vs_events.svg": (
        "F7: median peak process-tree memory vs input events (pandas vs Spark)",
        "Step 15, scripts/make_figures.py from reports/benchmarks/summary.csv",
    ),
    "reports/figures/risk_coverage.svg": (
        "F5: selective macro Brier vs coverage at measured retentions (50%, 100%)",
        "Step 15, scripts/make_figures.py from reports/tables/test_scores.csv",
    ),
    "reports/report.md": (
        "Final report (Step 15): abstract, RQ1/RQ2/RQ3, methods, limitations, "
        "course matrix, references",
        "Step 15; every number traced to a committed table in this index",
    ),
    "reports/course_matrix.md": (
        "Course matrix: every packet-required item present or explicitly omitted",
        "Step 15, checked against packet 01_COURSE_AND_REPORT.md",
    ),
    "reports/evidence.csv": (
        "This evidence index (self-referential row intentionally absent)",
        "Step 15, scripts/build_evidence.py",
    ),
    "docs/release.md": (
        "Release notes: contents, reproduction, known limits, commit ledger, "
        "fresh-clone + verified-data outcomes",
        "Step 15; fresh-clone run and hash re-verification recorded there",
    ),
    "slides/slides.md": (
        "15-minute presentation source (Markdown) + backup slides",
        "Step 14; numbers are the verified frozen results, not re-derived",
    ),
    "demo/demo_results.json": (
        "Demo machine-readable summary: 52/52 checks, latency p50/p95, cold start",
        "Step 14, demo/run_demo.py (commit 2942dfb), 2026-11-23T04:30:16Z",
    ),
    "demo/demo_log.txt": (
        "Captured full output of the recorded demo run (52 checks)",
        "Step 14, demo/run_demo.py; working demo + captured log is the evidence",
    ),
    "demo/latency_samples.csv": (
        "Raw latency samples: 500 rows (5 endpoints x 100 measured calls)",
        "Step 14, demo/run_demo.py; 10 warm-ups discarded per endpoint",
    ),
    "demo/server.log": (
        "Uvicorn server log from the recorded demo run",
        "Step 14, demo/run_demo.py",
    ),
    "demo/run_demo.py": (
        "Demo runner (stdlib only): doctor, golden pipeline, walkthrough, latency",
        "Step 14; re-runnable with `uv run --locked python demo/run_demo.py`",
    ),
    "demo/reset_demo.sh": (
        "One-command reset of the synthetic demo database only",
        "Step 14; removes local/demo.db* only",
    ),
    "generators/params_train.json": (
        "Synthetic generator parameters fitted from TRAINING data only",
        "Step 12; fit documented in generators/params_train_fit.md",
    ),
    "generators/params_train_fit.md": (
        "Generator fit note: train-only distributions, seed 941",
        "Step 12; compare only aggregate summaries to train",
    ),
    "generators/synthetic.py": (
        "Deterministic synthetic workload generator (1M / 10M events)",
        "Step 10/12; identical immutable JSONL bytes for both engines",
    ),
    "bench/parity.py": (
        "Parity checker: chunked sorted merge, 1e-8 abs tol, rtol 0",
        "Step 10; used outside timed intervals in Step 12",
    ),
    "bench/run_benchmark.py": (
        "Benchmark harness: fresh processes, tree-RSS sampler, ceilings",
        "Step 10; psutil absent so /proc fallback sampler (0.2 s)",
    ),
    "bench/worker.py": (
        "Benchmark worker: per-engine pipeline entry points for timed runs",
        "Step 10/12; identical input bytes, Snappy Parquet outputs",
    ),
    "bench/guardrails.py": (
        "Benchmark guardrails: timeout / memory / disk / orphan-Java checks",
        "Step 10; forced timeout/memory-failure smoke checks",
    ),
    "bench/README.md": (
        "Systems harness notes (sampler, ceilings, scope asymmetries)",
        "Step 10/12",
    ),
}

LOCAL_ARTIFACTS: dict[str, tuple[str, str]] = {
    "local/models/frozen_model.json": (
        "Frozen model of record: winner, calibration, gates, split/config hashes; "
        "SHA-256 b2989ae5...",
        "Step 09 `kindwindow freeze`; LOCAL ONLY (git-ignored) — hash is the identity",
    ),
    "local/models/frozen_forest.joblib": (
        "Fitted forest sidecar for the frozen model (200 trees, depth 10, leaf 10)",
        "Step 09 `kindwindow freeze`; LOCAL ONLY — reload agreement 1e-12",
    ),
    "local/models/candidates_step09.joblib": (
        "All 7 fitted candidates from selection (pre-freeze)",
        "Step 09 `kindwindow train`; LOCAL ONLY",
    ),
    "local/runs/step09/manifest.json": (
        "Step 09 freeze manifest (winner, hashes, counts, code revision c4dfa98)",
        "Step 09; written by `kindwindow freeze`",
    ),
    "local/runs/step11/manifest.json": (
        "Step 11 freeze manifest — written BEFORE any test row was read",
        "Step 11; the test-once seal record",
    ),
    "local/runs/step11/results.json": (
        "Step 11 machine-readable results (all conditions, denominators, omissions)",
        "Step 11, `kindwindow evaluate` run once (289.8 s)",
    ),
    "local/runs/step12/manifest.json": (
        "Step 12 machine + run inventory (CPU, RAM, threads, revisions)",
        "Step 12, run_study.py; study wall time 2026-11-23T03:02:37Z-03:50:35Z",
    ),
    "local/bench-runs/attempts.csv": (
        "Raw 18-attempt benchmark records (kit header schema)",
        "Step 12, run_study.py; aggregate copy is reports/benchmarks/attempts.csv",
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def claim_for(path: str) -> tuple[str, str]:
    if path in CLAIMS:
        return CLAIMS[path]
    if path.startswith("reports/"):
        return (f"Committed report artifact: {path}", "Committed in its step PR; see git log")
    if path.startswith("docs/"):
        return (f"Committed documentation: {path}", "Committed in its step PR; see git log")
    if path.startswith("src/kindwindow/"):
        return (
            f"Source implementing the CLI/pipeline: {path}",
            "Committed in its step PR; tests in tests/",
        )
    if path.startswith("tests/"):
        return (f"Test / fixture: {path}", "Run by `uv run --locked pytest -q`")
    if path.startswith("configs/"):
        return (f"Study configuration: {path}", "Frozen in Step 02; see docs/freeze-receipt.md")
    if path.startswith("demo/"):
        return (f"Demo artifact: {path}", "Step 14; re-runnable via demo/run_demo.py")
    if path.startswith("slides/"):
        return (f"Presentation artifact: {path}", "Step 14")
    if path.startswith("bench/") or path.startswith("generators/"):
        return (f"Systems harness / generator: {path}", "Steps 10/12")
    return (f"Committed file: {path}", "Committed in its step PR; see git log")


def build_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rel in sorted(tracked_files()):
        if rel == "reports/evidence.csv":
            continue
        path = ROOT / rel
        claim, how = claim_for(rel)
        rows.append(
            {
                "claim": claim,
                "artifact": rel,
                "sha256": sha256_file(path),
                "bytes": str(path.stat().st_size),
                "produced_verified": how,
                "reverify_command": f"sha256sum {rel}",
                "status": "committed",
            }
        )
    for rel in sorted(LOCAL_ARTIFACTS):
        path = ROOT / rel
        if not path.exists():
            rows.append(
                {
                    "claim": LOCAL_ARTIFACTS[rel][0],
                    "artifact": rel,
                    "sha256": "MISSING",
                    "bytes": "",
                    "produced_verified": LOCAL_ARTIFACTS[rel][1],
                    "reverify_command": f"sha256sum {rel}",
                    "status": "local-not-committed (MISSING at build time)",
                }
            )
            continue
        claim, how = LOCAL_ARTIFACTS[rel]
        rows.append(
            {
                "claim": claim,
                "artifact": rel,
                "sha256": sha256_file(path),
                "bytes": str(path.stat().st_size),
                "produced_verified": how,
                "reverify_command": f"sha256sum {rel}",
                "status": "local-not-committed",
            }
        )
    archive = Path.home() / "workspace" / "kindwindow-data" / "data.zip"
    if archive.exists():
        rows.append(
            {
                "claim": "CASAS v1 data.zip: 2,723,059,864 bytes, 189 members, "
                "MD5 973a4ec0..., SHA-256 fa6d2a9d...",
                "artifact": str(archive),
                "sha256": sha256_file(archive),
                "bytes": str(archive.stat().st_size),
                "produced_verified": "Zenodo DOI 10.5281/zenodo.15708568; MD5 matched "
                "published value; re-verified in Step 15",
                "reverify_command": "sha256sum ~/workspace/kindwindow-data/data.zip; "
                "md5sum ~/workspace/kindwindow-data/data.zip",
                "status": "external-private (never in git)",
            }
        )
    return rows


def build() -> int:
    rows = build_rows()
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "claim",
                "artifact",
                "sha256",
                "bytes",
                "produced_verified",
                "reverify_command",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


def check() -> int:
    if not OUT.exists():
        print("evidence index missing:", OUT)
        return 1
    with OUT.open(newline="", encoding="utf-8") as fh:
        saved = list(csv.DictReader(fh))
    fresh = {(r["artifact"]): r for r in build_rows()}
    ok = True
    for row in saved:
        current = fresh.get(row["artifact"])
        if current is None:
            print(f"REMOVED from tree: {row['artifact']}")
            ok = False
        elif current["sha256"] != row["sha256"]:
            print(
                f"CHANGED: {row['artifact']} (was {row['sha256'][:16]}..., "
                f"now {current['sha256'][:16]}...)"
            )
            ok = False
    saved_paths = {r["artifact"] for r in saved}
    for artifact in fresh:
        if artifact not in saved_paths:
            print(f"NEW (not in index): {artifact}")
            ok = False
    print("evidence index check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed index")
    args = parser.parse_args(argv)
    return check() if args.check else build()


if __name__ == "__main__":
    raise SystemExit(main())
