"""Benchmark harness: parent monitor outside each timed child (Step 10).

One timed attempt = one fresh child process (:mod:`bench.worker`) running
one engine on one input. The parent:

* starts a monotonic clock BEFORE launching the child,
* records the SparkSession-ready timestamp separately (the worker writes
  it into its result JSON as ``startup_seconds``),
* stops only after all output is committed (child exited 0 AND its
  result JSON says success AND the output directory exists),
* enforces the 1800 s / 70%-of-installed-RAM caps on the WHOLE process
  tree (Java + Python children) with a 0.2 s sampler,
* terminates ONLY this benchmark's owned processes (its own process
  group) and verifies no orphan Java remains,
* alternates engine order per pair (P/S, S/P, P/S for one workload,
  reversed for the next),
* writes one attempt row (kit ``benchmark_attempt_header.csv`` schema)
  plus a JSON sidecar (environment, caps, samples) per attempt.

Memory measurement caveat (documented, kit 06_PERFORMANCE.md): the
sampler sums simultaneous resident working sets (RSS) across the tree.
Shared pages are double-counted, the 0.2 s interval can miss short
peaks, and a lower cgroup quota governs when present. ``psutil`` is
used when installed; otherwise the sampler reads ``/proc`` directly
(no new dependency — ``uv.lock`` is owned by the coordinator).

Statuses: ``success`` | ``timeout`` | ``memory_limit`` | ``oom`` |
``disk_limit`` | ``error``. A non-success attempt has its partial
output removed, so it can never reach parity. Fatal preflight
conditions (no disk, JVM missing, another run holding the lock) are
``blocked`` and are NOT recorded as measured attempts.

This module is a standalone script, NOT the ``kindwindow`` CLI:
``python -m bench.run_benchmark --engine pandas --workload synthetic-1m
--input <jsonl> --attempt 1``. Step 12 runs the real study
(3 attempts per engine per core workload); Step 10 runs only tiny
smoke checks.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bench.guardrails import (
    DEFAULT_CAP_FRACTION,
    BenchmarkLock,
    default_lock_path,
    disk_free_bytes,
    effective_ram_bytes,
    installed_ram_bytes,
    java_version,
    memory_cap_bytes,
    scan_java_processes,
)

ATTEMPT_CSV_HEADER = [
    "run_id",
    "commit",
    "config_sha256",
    "input_sha256",
    "workload",
    "engine",
    "input_events",
    "attempt",
    "order_index",
    "status",
    "elapsed_seconds",
    "startup_seconds",
    "peak_tree_bytes",
    "output_bytes",
    "events_per_second",
    "cache_conditions",
    "cpu_cap",
    "memory_cap_bytes",
    "parity_status",
    "reason",
]
PARQUET_CODEC = "snappy"
SAMPLE_SECONDS = 0.2
DISK_RESERVE_BYTES = 256 * 1024**2  # abort as disk_limit below 256 MiB free
TERMINATE_GRACE_SECONDS = 10.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(repo_root: Path) -> dict:
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        return {"commit": commit, "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def collect_environment(
    repo_root: Path,
    *,
    threads: int,
    shuffle_partitions: int,
    cap_fraction: float,
    cap_seconds: float,
) -> dict:
    """Environment record for an attempt (versions, CPU/RAM, settings)."""
    config_path = repo_root / "configs" / "study.json"
    return {
        "recorded_utc": _utc_now(),
        "cpu_model": _cpu_model(),
        "cpu_cores_logical": os.cpu_count(),
        "cpu_affinity": (
            sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
        ),
        "installed_ram_bytes": installed_ram_bytes(),
        "effective_ram_bytes": effective_ram_bytes(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "pandas": _package_version("pandas"),
        "pyarrow": _package_version("pyarrow"),
        "numpy": _package_version("numpy"),
        "pyspark": _package_version("pyspark"),
        "java_version": java_version(),
        "jvm_flags": {
            "JAVA_TOOL_OPTIONS": os.environ.get("JAVA_TOOL_OPTIONS"),
            "_JAVA_OPTIONS": os.environ.get("_JAVA_OPTIONS"),
        },
        "thread_settings": {
            "OMP_NUM_THREADS": str(threads),
            "OPENBLAS_NUM_THREADS": str(threads),
            "MKL_NUM_THREADS": str(threads),
            "NUMEXPR_NUM_THREADS": str(threads),
        },
        "spark_local_threads": threads,
        "spark_shuffle_partitions": shuffle_partitions,
        "spark_app_caching": "disabled (no .cache()/.persist())",
        "parquet_codec": PARQUET_CODEC,
        "cap_seconds": cap_seconds,
        "cap_memory_fraction": cap_fraction,
        "memory_cap_bytes": memory_cap_bytes(cap_fraction),
        "config_sha256": _sha256_file(config_path) if config_path.is_file() else None,
        "git": _git_revision(repo_root),
        "cache_conditions": "page cache not controlled; not claimed cold",
    }


# ---------------------------------------------------------------------------
# Process-tree RSS sampler (psutil when present, /proc fallback)
# ---------------------------------------------------------------------------


def _proc_tree_pids(root_pid: int) -> set[int]:
    """All PIDs in the tree rooted at ``root_pid`` (root included)."""
    try:
        import psutil

        try:
            root = psutil.Process(root_pid)
        except psutil.NoSuchProcess:
            return set()
        pids = {root_pid}
        try:
            pids |= {child.pid for child in root.children(recursive=True)}
        except psutil.NoSuchProcess:
            pass
        return pids
    except ImportError:
        pass
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
        except OSError:
            continue
        ppid = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
                break
        if ppid is not None:
            children.setdefault(ppid, []).append(int(entry.name))
    seen = {root_pid}
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _tree_rss_bytes(root_pid: int) -> tuple[int, set[int]]:
    """Sum of simultaneous RSS over the tree; returns (bytes, pids)."""
    pids = _proc_tree_pids(root_pid)
    total = 0
    try:
        import psutil

        for pid in pids:
            try:
                total += psutil.Process(pid).memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total, pids
    except ImportError:
        pass
    page_size = os.sysconf("SC_PAGE_SIZE")
    for pid in pids:
        try:
            statm = (Path("/proc") / str(pid) / "statm").read_text(encoding="utf-8").split()
            total += int(statm[1]) * page_size  # resident pages
        except (OSError, ValueError, IndexError):
            continue
    return total, pids


def attempt_order(workload_index: int, attempt_index: int) -> str:
    """Engine for one pair position: P/S, S/P, P/S on workload 0, reversed on 1.

    ``attempt_index`` is 0-based over the 6 attempts of a workload
    (3 per engine); pairs are (0,1), (2,3), (4,5).
    """
    pair = attempt_index // 2
    first_in_pair = attempt_index % 2 == 0
    base = ["pandas", "spark"] if pair % 2 == 0 else ["spark", "pandas"]
    if workload_index % 2 == 1:
        base = list(reversed(base))
    return base[0] if first_in_pair else base[1]


def plan_attempts(workloads: list[str], *, attempts_per_engine: int = 3) -> list[dict]:
    """Full attempt plan: 2*attempts_per_engine attempts per workload.

    ``attempt`` counts per engine (1..attempts_per_engine); ``order_index``
    counts globally across the plan.
    """
    plan: list[dict] = []
    order_index = 0
    for workload_index, workload in enumerate(workloads):
        per_engine_count = {"pandas": 0, "spark": 0}
        for attempt_index in range(2 * attempts_per_engine):
            engine = attempt_order(workload_index, attempt_index)
            per_engine_count[engine] += 1
            plan.append(
                {
                    "workload": workload,
                    "engine": engine,
                    "attempt": per_engine_count[engine],
                    "order_index": order_index,
                }
            )
            order_index += 1
    return plan


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate ONLY this benchmark's owned process group (SIGTERM, then SIGKILL)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _remove_partial_output(output_dir: Path) -> bool:
    """Remove a partial/failed output dir and its bench temp siblings."""
    removed = False
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
        removed = True
    parent = output_dir.parent
    for sibling in parent.glob(f".{output_dir.name}.tmp-*"):
        shutil.rmtree(sibling, ignore_errors=True)
        removed = True
    return removed


def _count_input_events(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as fh:
        for _ in fh:
            n += 1
    return n


def run_attempt(
    *,
    repo_root: str | Path,
    engine: str,
    workload: str,
    input_path: str | Path,
    attempt: int = 1,
    order_index: int = 0,
    cap_seconds: float = 1800.0,
    cap_memory_fraction: float = DEFAULT_CAP_FRACTION,
    cap_memory_bytes_override: int | None = None,
    runs_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    threads: int = 2,
    shuffle_partitions: int = 4,
    worker_extra_args: list[str] | None = None,
    skip_lock: bool = False,
    min_free_bytes: int = 256 * 1024**2,
) -> dict:
    """Run ONE timed attempt under the parent monitor; returns the record.

    The record is also appended to ``<runs_dir>/attempts.csv`` with a
    JSON sidecar ``<runs_dir>/<run_id>.json``. Non-success attempts have
    their partial output removed before returning.
    """
    repo_root = Path(repo_root)
    input_path = Path(input_path)
    if engine not in ("pandas", "spark"):
        raise ValueError(f"engine must be 'pandas' or 'spark', got {engine!r}")
    if not input_path.is_file():
        raise FileNotFoundError(f"input not found: {input_path}")
    runs_dir = Path(runs_dir) if runs_dir is not None else repo_root / "local" / "bench-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    if disk_free_bytes(runs_dir) < min_free_bytes:
        raise RuntimeError(
            f"insufficient free disk in {runs_dir}: need {min_free_bytes} bytes free"
        )
    cap_bytes = (
        cap_memory_bytes_override
        if cap_memory_bytes_override is not None
        else memory_cap_bytes(cap_memory_fraction)
    )
    started = datetime.now(timezone.utc)
    run_id = (
        f"{workload}-{engine}-a{attempt}-{started.strftime('%Y%m%dT%H%M%SZ')}-{order_index:02d}"
    )
    output_dir = Path(output_dir) if output_dir is not None else runs_dir / "outputs" / run_id
    worker_result_path = runs_dir / f"{run_id}.worker.json"
    stdout_log = runs_dir / f"{run_id}.stdout.log"
    stderr_log = runs_dir / f"{run_id}.stderr.log"
    environment = collect_environment(
        repo_root,
        threads=threads,
        shuffle_partitions=shuffle_partitions,
        cap_fraction=cap_memory_fraction,
        cap_seconds=cap_seconds,
    )
    environment["memory_cap_bytes"] = cap_bytes
    input_events = _count_input_events(input_path)
    input_sha256 = _sha256_file(input_path)

    record: dict = {
        "run_id": run_id,
        "commit": environment["git"]["commit"],
        "config_sha256": environment["config_sha256"],
        "input_sha256": input_sha256,
        "workload": workload,
        "engine": engine,
        "input_events": input_events,
        "attempt": attempt,
        "order_index": order_index,
        "status": "error",
        "elapsed_seconds": None,
        "startup_seconds": None,
        "peak_tree_bytes": 0,
        "output_bytes": 0,
        "events_per_second": None,
        "cache_conditions": environment["cache_conditions"],
        "cpu_cap": environment["cpu_cores_logical"],
        "memory_cap_bytes": cap_bytes,
        "parity_status": "not_run",
        "reason": "",
        "started_utc": started.isoformat(),
        "output_dir": str(output_dir),
    }

    lock = BenchmarkLock(default_lock_path(repo_root)) if not skip_lock else None
    if lock is not None:
        lock.acquire()
    try:
        record.update(
            _run_child(
                repo_root=repo_root,
                engine=engine,
                input_path=input_path,
                output_dir=output_dir,
                worker_result_path=worker_result_path,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                cap_seconds=cap_seconds,
                cap_bytes=cap_bytes,
                threads=threads,
                shuffle_partitions=shuffle_partitions,
                worker_extra_args=worker_extra_args or [],
                record=record,
            )
        )
    finally:
        if lock is not None:
            lock.release()

    if record["status"] != "success":
        record["output_removed"] = _remove_partial_output(output_dir)
        record["output_bytes"] = 0
        record["events_per_second"] = None
    else:
        record["output_removed"] = False
    record["finished_utc"] = _utc_now()
    _append_attempt_csv(runs_dir, record)
    sidecar = {
        **record,
        "environment": environment,
        "input_path": str(input_path),
        "runs_dir": str(runs_dir),
    }
    (runs_dir / f"{run_id}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return record


def _run_child(
    *,
    repo_root: Path,
    engine: str,
    input_path: Path,
    output_dir: Path,
    worker_result_path: Path,
    stdout_log: Path,
    stderr_log: Path,
    cap_seconds: float,
    cap_bytes: int,
    threads: int,
    shuffle_partitions: int,
    worker_extra_args: list[str],
    record: dict,
) -> dict:
    """Launch the worker, sample its tree, enforce caps, verify cleanup."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), str(repo_root / "src"), env.get("PYTHONPATH", "")]
    )
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[var] = str(threads)
    cmd = [
        sys.executable,
        "-m",
        "bench.worker",
        "--engine",
        engine,
        "--input",
        str(input_path),
        "--output",
        str(output_dir),
        "--result",
        str(worker_result_path),
        "--threads",
        str(threads),
        "--shuffle-partitions",
        str(shuffle_partitions),
        *worker_extra_args,
    ]
    # Monotonic clock starts BEFORE launch; this is the authoritative time.
    t_start = time.monotonic()
    stdout_fh = stdout_log.open("w", encoding="utf-8")
    stderr_fh = stderr_log.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            env=env,
            stdout=stdout_fh,
            stderr=stderr_fh,
            start_new_session=True,  # own process group: kill only our tree
        )
    finally:
        stdout_fh.close()
        stderr_fh.close()

    peak_bytes = 0
    samples: list[dict] = []
    seen_pids: set[int] = set()
    violation: str | None = None
    stop = threading.Event()

    def sampler() -> None:
        nonlocal peak_bytes, violation
        while not stop.is_set():
            rss, pids = _tree_rss_bytes(proc.pid)
            seen_pids.update(pids)
            peak_bytes = max(peak_bytes, rss)
            samples.append({"elapsed": round(time.monotonic() - t_start, 3), "tree_rss": rss})
            if rss > cap_bytes:
                violation = "memory_limit"
                return
            try:
                if disk_free_bytes(output_dir.parent) < DISK_RESERVE_BYTES:
                    violation = "disk_limit"
                    return
            except OSError:
                pass
            if time.monotonic() - t_start > cap_seconds:
                violation = "timeout"
                return
            stop.wait(SAMPLE_SECONDS)

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()
    returncode: int | None = None
    while returncode is None:
        try:
            returncode = proc.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            if violation is not None:
                break
            if time.monotonic() - t_start > cap_seconds:
                violation = "timeout"
                break
    terminated_by_monitor = False
    if violation is not None:
        terminated_by_monitor = True
        _terminate_tree(proc)
        returncode = proc.wait()
    stop.set()
    sampler_thread.join(timeout=5)
    elapsed = time.monotonic() - t_start
    # Final sample after exit (peak may be the last live reading).
    rss, pids = _tree_rss_bytes(proc.pid)
    seen_pids |= pids
    peak_bytes = max(peak_bytes, rss)

    # Verify no orphan Java (or any owned process) remains.
    orphan_pids = [pid for pid in seen_pids if pid != proc.pid and _pid_alive(pid)]
    for pid in orphan_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)
    orphan_pids = [pid for pid in seen_pids if pid != proc.pid and _pid_alive(pid)]
    java_now = scan_java_processes()
    orphan_java = [j for j in java_now if j["pid"] in seen_pids]

    worker_result: dict = {}
    if worker_result_path.is_file():
        try:
            worker_result = json.loads(worker_result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            worker_result = {}
    stderr_text = ""
    if stderr_log.is_file():
        stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")

    status: str
    reason = ""
    if violation == "timeout":
        status, reason = "timeout", f"exceeded cap_seconds={cap_seconds}"
    elif violation == "memory_limit":
        status, reason = "memory_limit", f"tree RSS exceeded cap {cap_bytes} bytes"
    elif violation == "disk_limit":
        status, reason = "disk_limit", "free disk fell below reserve during run"
    elif terminated_by_monitor:
        status, reason = "error", "monitor terminated child without a recorded violation"
    elif returncode == 0 and worker_result.get("status") == "success" and output_dir.exists():
        status, reason = "success", ""
    elif returncode in (-9, 137) or "OutOfMemoryError" in stderr_text:
        status, reason = "oom", f"child killed (returncode={returncode})"
        if "OutOfMemoryError" in stderr_text:
            reason += "; OutOfMemoryError in stderr"
    elif "No space left on device" in stderr_text:
        status, reason = "disk_limit", "No space left on device in child stderr"
    else:
        status = "error"
        reason = worker_result.get("error") or f"child exit code {returncode}"

    output_bytes = 0
    if status == "success" and output_dir.exists():
        output_bytes = sum(p.stat().st_size for p in output_dir.rglob("*") if p.is_file())
    return {
        "status": status,
        "reason": reason,
        "elapsed_seconds": round(elapsed, 3),
        "startup_seconds": worker_result.get("startup_seconds"),
        "peak_tree_bytes": peak_bytes,
        "peak_tree_samples": len(samples),
        "output_bytes": output_bytes,
        "events_per_second": (
            round(record["input_events"] / elapsed, 2)
            if status == "success" and elapsed > 0
            else None
        ),
        "returncode": returncode,
        "terminated_by_monitor": terminated_by_monitor,
        "orphan_pids_remaining": orphan_pids,
        "orphan_java_remaining": orphan_java,
        "n_windows": worker_result.get("n_windows"),
        "samples": samples[-5:],
    }


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _append_attempt_csv(runs_dir: Path, record: dict) -> Path:
    """Append one row to ``<runs_dir>/attempts.csv`` (kit header schema)."""
    path = runs_dir / "attempts.csv"
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=ATTEMPT_CSV_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerow({key: record.get(key, "") for key in ATTEMPT_CSV_HEADER})
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m bench.run_benchmark ...`` (standalone, not the CLI)."""
    parser = argparse.ArgumentParser(prog="bench.run_benchmark")
    parser.add_argument("--engine", choices=["pandas", "spark"], default="pandas")
    parser.add_argument("--workload", default="smoke")
    parser.add_argument("--input", help="Input normalized-events JSONL")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--order-index", type=int, default=0)
    parser.add_argument("--cap-seconds", type=float, default=1800.0)
    parser.add_argument("--cap-memory-fraction", type=float, default=DEFAULT_CAP_FRACTION)
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--shuffle-partitions", type=int, default=4)
    parser.add_argument("--no-lock", action="store_true", help="Skip the timed-run lock")
    parser.add_argument("--plan", action="store_true", help="Print the attempt plan and exit")
    parser.add_argument("--workloads", nargs="*", default=["synthetic-1m", "synthetic-10m"])
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    if args.plan:
        print(json.dumps(plan_attempts(args.workloads), indent=2))
        return 0
    if not args.input:
        print("bench.run_benchmark: --input is required (except with --plan)", file=sys.stderr)
        return 2
    record = run_attempt(
        repo_root=repo_root,
        engine=args.engine,
        workload=args.workload,
        input_path=args.input,
        attempt=args.attempt,
        order_index=args.order_index,
        cap_seconds=args.cap_seconds,
        cap_memory_fraction=args.cap_memory_fraction,
        runs_dir=args.runs_dir,
        output_dir=args.output_dir,
        threads=args.threads,
        shuffle_partitions=args.shuffle_partitions,
        skip_lock=args.no_lock,
    )
    print(json.dumps({k: record.get(k) for k in ATTEMPT_CSV_HEADER}, indent=2))
    return 0 if record["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
