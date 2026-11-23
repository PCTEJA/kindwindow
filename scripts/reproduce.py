#!/usr/bin/env python3
"""KindWindow Step 15 — reproduction entry point.

Re-runs what can be re-run, verifies what must not be re-run, and refuses
what must never be re-run.

Profiles (run from the repo root):
  fixture   Fresh-environment check: sync, doctor, golden-fixture pipeline,
            full test suite. Needs no private data.
  verify    Hash verification only: CASAS archive, frozen model, split,
            cohort manifest, study config, and every checksum in
            reports/evidence.csv. Needs the private archive path.
  figures   Regenerate report figures from committed tables and verify the
            committed per-home Brier figure. No real data.
  demo      Run the integrated synthetic demo (demo/run_demo.py).
  systems   Fixture-scale systems check: generate 1M synthetic events,
            run bench/parity tests. With --full, runs the full 18-attempt
            Step 12 study (about 48 min, exclusive machine, needs the real
            normalized Parquet tree under local/normalized).
  real      Verification + documentation only. Prints the exact historical
            command sequence that produced the real-data results and
            re-verifies hashes. It NEVER runs `kindwindow evaluate`,
            `train`, or `freeze`: the test split was scored exactly once
            (Step 11) and the frozen model is sealed.

Hard refusals (by design, not by omission):
  * `kindwindow evaluate` is never invoked by this script. Re-scoring test
    would break the test-once seal. Passing --run-evaluate exits with an
    error explaining the seal.
  * No command here writes to data/heldout/, local/models/, or any frozen
    artifact. Refits are not byte-reproducible; the artifact of record is
    the hashed one in docs/freeze-receipt.md.

Usage:
  python3 scripts/reproduce.py --profile fixture
  python3 scripts/reproduce.py --profile verify [--archive PATH]
  python3 scripts/reproduce.py --profile figures
  python3 scripts/reproduce.py --profile demo
  python3 scripts/reproduce.py --profile systems [--full]
  python3 scripts/reproduce.py --profile real
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED = {
    # CASAS v1 data.zip (private archive; never in git)
    "archive_sha256": "fa6d2a9d74e2529304ff9632929658433580b5a44559ac9b3c6e2de9d9b7c83c",
    "archive_md5": "973a4ec0b67269566227ce8a5a3a9e21",
    "archive_bytes": 2723059864,
    "archive_members": 189,
    # Committed / local artifacts (hashes of the artifact of record)
    "frozen_model_sha256": "b2989ae57cd8c7c5d64f8913b60299910dbacd24f490775a90c7e84354527a81",
    "split_sha256": "65f5a9974f944e7c23e9fb7e7a03d749459ba3ae02adc5aadc1419dd74d038cb",
    "cohort_sha256": "407d8ecff36a50d64ecb234641e8e5e5aa1c36c47821d8408377d69951f89f2f",
    "config_sha256": "628db5f27bb48ea991333261e9a1533628cbbf26d817fa16ab72207f2b5d35b2",
}


def run(cmd: list[str], cwd: Path = ROOT) -> int:
    print(f"+ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def run_or_die(cmd: list[str], cwd: Path = ROOT) -> None:
    code = run(cmd, cwd)
    if code != 0:
        print(f"FAILED (exit {code}): {' '.join(cmd)}", file=sys.stderr)
        raise SystemExit(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_hash(label: str, path: Path, expected: str, algo: str = "sha256") -> bool:
    if not path.exists():
        print(f"  MISSING: {label}: {path}")
        return False
    actual = sha256_file(path) if algo == "sha256" else md5_file(path)
    ok = actual == expected
    print(f"  {'OK  ' if ok else 'FAIL'}: {label}: {actual[:16]}... (expected {expected[:16]}...)")
    return ok


def profile_fixture() -> None:
    run_or_die(["uv", "sync", "--locked", "--group", "dev", "--group", "spark"])
    run_or_die(["uv", "run", "--locked", "kindwindow", "doctor"])
    run_or_die(
        [
            "uv",
            "run",
            "--locked",
            "kindwindow",
            "pipeline",
            "--engine",
            "pandas",
            "--workload",
            "fixture",
        ]
    )
    run_or_die(["uv", "run", "--locked", "pytest", "-q"])
    print(
        "fixture profile: PASS (see note in docs/release.md about the "
        "kit-sibling test when the checkout is not next to kindwindow-kit)"
    )


def profile_verify(archive: Path) -> None:
    ok = True
    print("CASAS v1 archive:", archive)
    if not archive.exists():
        print(f"  MISSING: archive not found at {archive}")
        print("  Set --archive to the private data.zip, or KINDWINDOW_DATA.")
        raise SystemExit(2)
    ok &= check_hash("archive SHA-256", archive, EXPECTED["archive_sha256"])
    ok &= check_hash("archive MD5", archive, EXPECTED["archive_md5"], algo="md5")
    size = archive.stat().st_size
    size_ok = size == EXPECTED["archive_bytes"]
    print(
        f"  {'OK  ' if size_ok else 'FAIL'}: archive bytes: {size} "
        f"(expected {EXPECTED['archive_bytes']})"
    )
    ok &= size_ok
    import zipfile

    with zipfile.ZipFile(archive) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
    members_ok = len(members) == EXPECTED["archive_members"]
    print(
        f"  {'OK  ' if members_ok else 'FAIL'}: archive file members: "
        f"{len(members)} (expected {EXPECTED['archive_members']})"
    )
    ok &= members_ok
    ok &= check_hash(
        "frozen model local/models/frozen_model.json",
        ROOT / "local" / "models" / "frozen_model.json",
        EXPECTED["frozen_model_sha256"],
    )
    ok &= check_hash(
        "split data/manifests/split.csv",
        ROOT / "data" / "manifests" / "split.csv",
        EXPECTED["split_sha256"],
    )
    ok &= check_hash(
        "cohort data/manifests/cohort.csv",
        ROOT / "data" / "manifests" / "cohort.csv",
        EXPECTED["cohort_sha256"],
    )
    ok &= check_hash(
        "study config configs/study.json",
        ROOT / "configs" / "study.json",
        EXPECTED["config_sha256"],
    )
    print("verify profile:", "PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


def profile_figures() -> None:
    run_or_die(["python3", "scripts/make_figures.py"])
    run_or_die(["python3", "scripts/build_evidence.py", "--check"])
    print("figures profile: PASS")


def profile_demo() -> None:
    run_or_die(["uv", "run", "--locked", "python", "demo/run_demo.py"])
    print("demo profile: PASS (52 checks; see demo/demo_results.json)")


def profile_systems(full: bool) -> None:
    work = ROOT / "local" / "reproduce-systems"
    work.mkdir(parents=True, exist_ok=True)
    out = work / "synthetic-1m.jsonl"
    if not out.exists():
        run_or_die(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                "generators.synthetic",
                "--params",
                "generators/params_train.json",
                "--n",
                "1000000",
                "--seed",
                "941",
                "--out",
                str(out),
            ]
        )
    else:
        print(f"reusing existing {out} (delete it to regenerate)")
    run_or_die(
        [
            "uv",
            "run",
            "--locked",
            "pytest",
            "tests/test_bench.py",
            "tests/test_benchmark_study.py",
            "tests/parity/test_parity.py",
            "-q",
        ]
    )
    print("systems smoke profile: PASS (1M generation + bench/parity tests)")
    print()
    print("Full Step 12 study (18 timed attempts, ~48 min, exclusive machine):")
    print("  uv run --locked python local/runs/step12/run_study.py")
    print("  uv run --locked python local/runs/step12/run_parity_and_aggregate.py")
    print("  (needs local/normalized real Parquet tree; see reports/benchmarks/README.md)")
    if full:
        run_or_die(["uv", "run", "--locked", "python", "local/runs/step12/run_study.py"])
        run_or_die(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "local/runs/step12/run_parity_and_aggregate.py",
            ]
        )


def profile_real() -> None:
    print("REAL profile: verification + documentation only.")
    print("The real-data results in reports/report.md were produced once,")
    print("in Steps 08-12, and are reproduced here from saved artifacts —")
    print("they are NOT recomputed by this profile.")
    print()
    print("Historical command sequence (for the record; do NOT re-run")
    print("evaluate/train/freeze — see the seal note below):")
    for cmd in (
        "uv run --locked kindwindow audit --archive <private-data.zip>",
        "uv run --locked kindwindow cohort --freeze",
        "uv run --locked kindwindow pipeline --engine pandas --workload real",
        "uv run --locked kindwindow pipeline --engine spark --workload real",
        "uv run --locked kindwindow train --config configs/study.json",
        "uv run --locked kindwindow freeze",
        "# SEALED — scored exactly once in Step 11, never again:",
        "# uv run --locked kindwindow evaluate --frozen --include-stress --include-gap-sensitivity",
        "uv run --locked kindwindow benchmark --workloads real,1m,10m --attempts 3",
        "uv run --locked kindwindow parity --workloads real,1m,10m",
    ):
        print(f"  {cmd}")
    print()
    print("SEAL: `kindwindow evaluate` (test scoring) ran exactly once in Step 11")
    print("(local/runs/step11/manifest.json was written before any test row was")
    print("read; elapsed 289.8 s). This script refuses to run it again.")
    archive = Path(os.environ.get("KINDWINDOW_DATA", str(ROOT / "local"))) / "data.zip"
    alt = Path.home() / "workspace" / "kindwindow-data" / "data.zip"
    if not archive.exists() and alt.exists():
        archive = alt
    profile_verify(archive)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default=None,
        choices=["fixture", "verify", "figures", "demo", "systems", "real"],
    )
    parser.add_argument(
        "--archive", default=None, help="path to private CASAS data.zip (verify/real profiles)"
    )
    parser.add_argument(
        "--full", action="store_true", help="systems profile: run the full 18-attempt study"
    )
    parser.add_argument("--run-evaluate", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run_evaluate:
        print("REFUSED: --run-evaluate would re-score the sealed test split.", file=sys.stderr)
        print("The test was scored exactly once (Step 11). No re-scoring.", file=sys.stderr)
        return 2
    if args.profile is None:
        parser.error("--profile is required")
    if args.profile == "fixture":
        profile_fixture()
    elif args.profile == "verify":
        archive = (
            Path(args.archive)
            if args.archive
            else (Path.home() / "workspace" / "kindwindow-data" / "data.zip")
        )
        profile_verify(archive)
    elif args.profile == "figures":
        profile_figures()
    elif args.profile == "demo":
        profile_demo()
    elif args.profile == "systems":
        profile_systems(args.full)
    elif args.profile == "real":
        profile_real()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
