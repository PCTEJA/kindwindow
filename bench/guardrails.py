"""Benchmark guardrails (Step 10): preflight, lockfile, caps, smoke checks.

Preflight conditions that block a run (inadequate disk, no JVM, another
timed run in progress) are *blocked*, distinct from measured failures
(``timeout`` / ``memory_limit`` / ``oom`` / ``disk_limit`` / ``error``)
recorded per attempt by :mod:`bench.run_benchmark`.

The lockfile lives under the repo's git-ignored ``local/`` directory
(``local/bench.lock``) so it can never be committed. It stores the
owner PID; a lock whose PID is dead is stale and may be reclaimed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CAP_FRACTION = 0.7
DEFAULT_MIN_FREE_BYTES = 2 * 1024**3  # 2 GiB free required before a real run
DEFAULT_LOCK_NAME = "bench.lock"


class BenchmarkInProgressError(RuntimeError):
    """Another timed benchmark run holds the lock."""


class PreflightError(RuntimeError):
    """A fatal preflight condition blocks the run (not a measured failure)."""


@dataclass
class PreflightResult:
    """Outcome of :func:`preflight`; ``ok`` False means blocked, not measured."""

    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def raise_if_blocked(self) -> None:
        if not self.ok:
            raise PreflightError("; ".join(self.failures))


def installed_ram_bytes() -> int:
    """Installed RAM in bytes (Linux ``/proc/meminfo``; falls back to psutil)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except ImportError:
        raise RuntimeError("cannot determine installed RAM: no /proc/meminfo and no psutil")


def _cgroup_memory_limit_bytes() -> int | None:
    """Lower OS/container memory quota, if one is set (cgroup v1/v2)."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("max", ""):
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 reports a huge sentinel (~2**63) when unlimited.
        if value > 2**62:
            continue
        return value
    return None


def effective_ram_bytes() -> int:
    """RAM the benchmark may actually use: min(installed, cgroup quota)."""
    installed = installed_ram_bytes()
    quota = _cgroup_memory_limit_bytes()
    return min(installed, quota) if quota is not None else installed


def memory_cap_bytes(cap_fraction: float = DEFAULT_CAP_FRACTION) -> int:
    """Aggregate process-tree memory cap: ``cap_fraction`` of effective RAM."""
    if not 0.0 < cap_fraction <= 1.0:
        raise ValueError(f"cap_fraction must be in (0, 1], got {cap_fraction!r}")
    return int(effective_ram_bytes() * cap_fraction)


def disk_free_bytes(path: str | Path) -> int:
    """Free bytes on the filesystem holding ``path``."""
    return shutil.disk_usage(str(path)).free


def java_version() -> str | None:
    """``java -version`` stderr first line, or None when no JVM is found."""
    java = shutil.which("java")
    if java is None:
        return None
    try:
        proc = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (proc.stderr or proc.stdout or "").strip().splitlines()
    return first[0] if first else "java (version unknown)"


def scan_java_processes() -> list[dict]:
    """All live ``java`` processes: pid, ppid, cmdline (for orphan checks).

    Uses psutil when available, else ``/proc``. Never kills anything.
    """
    found: list[dict] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
            cmdline = proc.info.get("cmdline") or []
            if cmdline and os.path.basename(cmdline[0]) == "java":
                found.append(
                    {
                        "pid": proc.info["pid"],
                        "ppid": proc.info["ppid"],
                        "cmdline": " ".join(cmdline),
                    }
                )
        return found
    except ImportError:
        pass
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return found
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
            status = (entry / "status").read_text(encoding="utf-8")
        except OSError:
            continue
        if not cmdline:
            continue
        parts = cmdline.split("\0")
        if os.path.basename(parts[0]) != "java":
            continue
        ppid = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
        found.append({"pid": int(entry.name), "ppid": ppid, "cmdline": " ".join(parts)})
    return found


@dataclass
class BenchmarkLock:
    """Exclusive timed-run lock backed by a file under ``local/``.

    Use as a context manager; raises :class:`BenchmarkInProgressError`
    when a live owner holds the lock. A stale lock (owner PID dead) is
    reclaimed once, and the reclaim is recorded.
    """

    lock_path: str | Path
    owner_pid: int = field(default_factory=os.getpid)
    acquired: bool = False
    reclaimed_stale: bool = False

    def acquire(self) -> None:
        path = Path(self.lock_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": self.owner_pid,
                "acquired_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        for attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    owner = json.loads(path.read_text(encoding="utf-8"))
                    owner_pid = int(owner.get("pid", 0))
                except (OSError, ValueError, TypeError):
                    owner_pid = 0
                alive = False
                if owner_pid > 0:
                    try:
                        os.kill(owner_pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if alive:
                    raise BenchmarkInProgressError(
                        f"another timed benchmark run is in progress "
                        f"(lock {path}, owner pid {owner_pid})"
                    ) from None
                path.unlink(missing_ok=True)
                self.reclaimed_stale = True
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            self.acquired = True
            return
        raise BenchmarkInProgressError(f"could not acquire benchmark lock {path}")

    def release(self) -> None:
        if not self.acquired:
            return
        path = Path(self.lock_path)
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
            if int(owner.get("pid", -1)) == self.owner_pid:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        self.acquired = False

    def __enter__(self) -> "BenchmarkLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def default_lock_path(repo_root: str | Path) -> Path:
    """Lockfile path: ``<repo>/local/bench.lock`` (git-ignored)."""
    return Path(repo_root) / "local" / DEFAULT_LOCK_NAME


def preflight(
    repo_root: str | Path,
    *,
    cap_fraction: float = DEFAULT_CAP_FRACTION,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    check_path: str | Path | None = None,
    require_jvm: bool = True,
) -> PreflightResult:
    """Fatal preflight checks before any timed attempt.

    Checks: no other timed run holds the lock (checked, not acquired),
    JVM present (when ``require_jvm``), enough free disk on the output
    filesystem, and a computable memory cap. Returns a result; call
    ``raise_if_blocked()`` to turn failures into :class:`PreflightError`.
    """
    result = PreflightResult(ok=True)
    root = Path(repo_root)
    lock_path = default_lock_path(root)
    if lock_path.exists():
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            owner_pid = int(owner.get("pid", 0))
            alive = False
            if owner_pid > 0:
                try:
                    os.kill(owner_pid, 0)
                    alive = True
                except OSError:
                    alive = False
            if alive:
                result.failures.append(
                    f"another timed run holds {lock_path} (owner pid {owner_pid})"
                )
            else:
                result.warnings.append(f"stale benchmark lock at {lock_path} (owner pid dead)")
        except (OSError, ValueError, TypeError):
            result.warnings.append(f"unreadable benchmark lock at {lock_path}")
    jvm = java_version()
    if require_jvm and jvm is None:
        result.failures.append("no JVM found on PATH (java required for the Spark engine)")
    target = Path(check_path) if check_path is not None else root
    try:
        free = disk_free_bytes(target)
    except OSError as exc:
        result.failures.append(f"cannot check disk space for {target}: {exc}")
        free = 0
    if free < min_free_bytes:
        result.failures.append(
            f"insufficient free disk on {target}: {free} bytes free, need at least {min_free_bytes}"
        )
    try:
        cap = memory_cap_bytes(cap_fraction)
        installed = installed_ram_bytes()
        effective = effective_ram_bytes()
    except (RuntimeError, ValueError) as exc:
        result.failures.append(f"cannot compute memory cap: {exc}")
        cap, installed, effective = 0, 0, 0
    if effective and effective < installed:
        result.warnings.append(
            f"cgroup quota {effective} bytes is below installed RAM {installed} bytes; "
            "the lower quota governs the cap"
        )
    result.checks = {
        "lock_path": str(lock_path),
        "lock_held_by_live_process": any("another timed run" in f for f in result.failures),
        "java_version": jvm,
        "disk_free_bytes": free,
        "min_free_bytes": min_free_bytes,
        "installed_ram_bytes": installed,
        "effective_ram_bytes": effective,
        "cap_fraction": cap_fraction,
        "memory_cap_bytes": cap,
    }
    result.ok = not result.failures
    return result


# ---------------------------------------------------------------------------
# Forced failure smoke checks (tiny workloads, absurdly small caps)
# ---------------------------------------------------------------------------


def smoke_timeout_check(
    *,
    repo_root: str | Path,
    engine: str = "pandas",
    cap_seconds: float = 1.0,
    sleep_seconds: float = 5.0,
    runs_dir: str | Path | None = None,
    tmp_path: str | Path | None = None,
) -> dict:
    """Force a timeout: a tiny workload whose worker sleeps past the cap.

    Returns the attempt record. Asserts (raises ``AssertionError``) that
    the status is ``timeout`` and that no partial output remains at the
    attempt's output path. Uses the test-only ``--sleep-seconds`` worker
    hook; never used in a real timed attempt.
    """
    from bench.run_benchmark import run_attempt  # local import: avoids a cycle

    record = run_attempt(
        repo_root=repo_root,
        engine=engine,
        workload="smoke-timeout",
        input_path=_tiny_input(repo_root, tmp_path),
        attempt=1,
        order_index=0,
        cap_seconds=cap_seconds,
        cap_memory_fraction=DEFAULT_CAP_FRACTION,
        runs_dir=runs_dir,
        worker_extra_args=["--sleep-seconds", str(sleep_seconds)],
        skip_lock=True,
    )
    assert record["status"] == "timeout", f"expected timeout, got {record['status']!r}"
    assert not Path(record["output_dir"]).exists(), "partial output left after timeout"
    return record


def smoke_memory_check(
    *,
    repo_root: str | Path,
    engine: str = "pandas",
    cap_memory_bytes: int = 1_000_000,
    runs_dir: str | Path | None = None,
    tmp_path: str | Path | None = None,
) -> dict:
    """Force a memory-limit failure: a tiny workload with a 1 MB cap.

    Any real Python process exceeds 1 MB of RSS immediately, so the
    monitor must report ``memory_limit`` and leave no partial output.
    Returns the attempt record.
    """
    from bench.run_benchmark import run_attempt  # local import: avoids a cycle

    record = run_attempt(
        repo_root=repo_root,
        engine=engine,
        workload="smoke-memory",
        input_path=_tiny_input(repo_root, tmp_path),
        attempt=1,
        order_index=0,
        cap_seconds=60.0,
        cap_memory_fraction=DEFAULT_CAP_FRACTION,
        cap_memory_bytes_override=cap_memory_bytes,
        runs_dir=runs_dir,
        skip_lock=True,
    )
    assert record["status"] == "memory_limit", f"expected memory_limit, got {record['status']!r}"
    assert not Path(record["output_dir"]).exists(), "partial output left after memory_limit"
    return record


def _tiny_input(repo_root: str | Path, tmp_path: str | Path | None) -> Path:
    """Tiny (golden-fixture) input for smoke checks; never real data."""
    golden = Path(repo_root) / "tests" / "fixtures" / "golden_events.jsonl"
    if golden.is_file():
        return golden
    if tmp_path is None:
        raise FileNotFoundError("golden fixture missing and no tmp_path given for smoke input")
    dest = Path(tmp_path) / "tiny_events.jsonl"
    dest.write_text(
        '{"home": "smoke-A", "member": "synthetic.jsonl", "row": 1, '
        '"time": "2030-02-01T08:00:00", "sensor": "M1", "type": "motion", "message": "ON"}\n'
        '{"home": "smoke-A", "member": "synthetic.jsonl", "row": 2, '
        '"time": "2030-02-01T10:00:00", "sensor": "M1", "type": "motion", "message": "OFF"}\n'
        '{"home": "smoke-A", "member": "synthetic.jsonl", "row": 3, '
        '"time": "2030-02-01T10:30:00", "sensor": "M1", "type": "motion", "message": "ON"}\n',
        encoding="utf-8",
    )
    return dest
