"""KindWindow Step 14 integrated demo runner (local only, synthetic only).

Runs the full packet 07 demo script against the resident app in this repo:

  0. release commit + ``kindwindow doctor``
  1. golden JSON fixture through the clean pandas pipeline, including why
     the 10:30 event belongs to the next forecast window
  2. scripted synthetic resident/contact walkthrough (Rosa, Contact A,
     Contact B — synthetic demo identities only; no real people, no real
     CASAS rows, no held-out labels)
  3. loopback latency measurement (10 warm-ups + 100 measured calls per
     endpoint, concurrency 1, monotonic client timer)
  4. demo-database delete, leaving no synthetic records behind

Usage (from the repo root)::

    uv run --locked python demo/run_demo.py [--port 8001] [--out-dir demo]
        [--latency-n 100] [--warmup 10]

Outputs (under ``--out-dir``): ``demo_log.txt`` (this run's captured
output), ``latency_samples.csv`` (raw per-call samples), ``demo_results.json``
(machine-readable summary) and ``server.log`` (uvicorn output). The demo
database lives in a fresh temporary directory on every run, so repeat
presentations start from identical state; ``demo/reset_demo.sh`` removes
only the default on-disk demo database (``local/demo.db*``).

Only the standard library is used, so the runner works from a fresh
checkout after ``uv sync --locked``. Invite/session tokens are never
written to the log.
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")

# Synthetic walkthrough identities (labels only; the app's own identities
# are "resident", "contact-a" and "contact-b").
RESIDENT_NAME = "Rosa (synthetic resident, app identity 'resident')"
CONTACT_A_NAME = "Contact A (synthetic contact, app identity 'contact-a')"
CONTACT_B_NAME = "Contact B (synthetic contact, app identity 'contact-b')"
NOTE_TEXT = "Monday: available 09:00-12:00; quiet 10:00-10:30."
INITIAL_PREFS = {
    "preferred_days": [1, 2, 3, 4, 5],
    "preferred_range": {"start": "09:00", "end": "17:00"},
    "quiet_range": {"start": "12:00", "end": "13:00"},
    "contact_ids": [1, 2],
}
# Prefs after the confirmed note: Monday only, 09:00-12:00, quiet 10:00-10:30.
CONFIRMED_PREFS = {
    "preferred_days": [1],
    "preferred_range": {"start": "09:00", "end": "12:00"},
    "quiet_range": {"start": "10:00", "end": "10:30"},
    "contact_ids": [1, 2],
}


class DemoError(Exception):
    """A demo step failed; the log records where."""


class Log:
    """Write every line to stdout and to the captured log file."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")

    def write(self, text: str = "") -> None:
        print(text)
        self._file.write(text + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def redact(obj: object) -> object:
    """Drop token material from anything destined for the log."""
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items() if k not in ("invite_token",)}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def run_cmd(args: list[str], log: Log, cwd: Path = REPO_ROOT) -> str:
    """Run a command, echo its output to the log, return stdout."""
    log.write(f"$ {' '.join(args)}")
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600)
    out = (proc.stdout + proc.stderr).strip()
    if out:
        log.write(out)
    if proc.returncode != 0:
        raise DemoError(f"command failed ({proc.returncode}): {' '.join(args)}")
    return proc.stdout


def next_weekday_10am(weekday: int) -> datetime:
    """Next occurrence of the given ISO weekday at 10:00 UTC, in the future."""
    now = datetime.now(timezone.utc)
    day = now.replace(hour=10, minute=0, second=0, microsecond=0)
    delta = (weekday - now.isoweekday()) % 7
    day += timedelta(days=delta)
    if day <= now:
        day += timedelta(days=7)
    return day


def slot_ts(day: datetime, hour: int, minute: int = 0) -> int:
    return int(day.replace(hour=hour, minute=minute).timestamp())


class Client:
    """One cookie jar = one demo identity (resident, Contact A, Contact B)."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return resp.status, {"_html_bytes": len(raw)}
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b"{}"
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"_html_bytes": len(raw)}

    def timed(self, method: str, path: str, body: dict | None = None) -> tuple[float, int]:
        """Latency of one call in ms: request start to fully read response."""
        start = time.perf_counter_ns()
        status, _ = self.call(method, path, body)
        return (time.perf_counter_ns() - start) / 1e6, status


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile (the protocol used for the reported p50/p95)."""
    if not sorted_values:
        raise DemoError("percentile of empty sample")
    rank = max(1, int(-(-pct * len(sorted_values) // 100)))  # ceil(pct/100 * n)
    return sorted_values[min(rank, len(sorted_values)) - 1]


def main() -> int:
    parser = argparse.ArgumentParser(description="KindWindow integrated demo runner")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "demo")
    parser.add_argument(
        "--db", type=Path, default=None, help="demo SQLite path (default: fresh temp dir)"
    )
    parser.add_argument("--latency-n", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Log(out_dir / "demo_log.txt")
    results: dict = {"steps": [], "checks": []}
    failures: list[str] = []

    def step(title: str) -> None:
        log.write("")
        log.write(f"== {title} ==")
        results["steps"].append(title)

    def check(name: str, ok: bool, detail: str = "") -> None:
        results["checks"].append({"name": name, "ok": ok, "detail": detail})
        log.write(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    def require(name: str, ok: bool, detail: str = "") -> None:
        check(name, ok, detail)
        if not ok:
            raise DemoError(f"demo step failed: {name} — {detail}")

    try:
        # ---------------------------------------------------------- step 0
        step("0. Release commit and doctor")
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        results["commit"] = commit
        results["branch"] = branch
        log.write(f"branch: {branch}  commit: {commit}")
        doctor_out = run_cmd([UV, "run", "--locked", "kindwindow", "doctor"], log)
        results["doctor"] = doctor_out.strip()
        require("doctor OK", "doctor: OK" in doctor_out)

        # ---------------------------------------------------------- step 1
        step("1. Golden fixture through the clean pandas pipeline")
        with tempfile.TemporaryDirectory(prefix="kindwindow-pipeline-") as tmp:
            pipe_out = run_cmd(
                [
                    UV,
                    "run",
                    "--locked",
                    "kindwindow",
                    "pipeline",
                    "--engine",
                    "pandas",
                    "--workload",
                    "fixture",
                    "--out",
                    tmp,
                ],
                log,
            )
            results["pipeline"] = pipe_out.strip()
        require("golden pipeline runs", "n_windows: 3" in pipe_out, pipe_out.strip())
        rows = list(csv.DictReader((REPO_ROOT / "tests/fixtures/golden_windows.csv").open()))
        log.write("Golden windows (tests/fixtures/golden_windows.csv):")
        for row in rows:
            log.write(
                f"  t={row['t']} count_30m={row['count_30m']} "
                f"recency_s={row['recency_seconds']} label={row['y']}"
            )
        log.write("Why the 10:30 event belongs to the NEXT forecast:")
        log.write("  The target interval is half-open [t, t+30m): an event exactly")
        log.write("  at t belongs to that window's target; an event exactly at t+30m")
        log.write("  belongs to the next target. The 10:30 motion ON (and door OPEN)")
        log.write("  fall at the end of the 10:00 window's target [10:00, 10:30), so")
        log.write("  the 10:00 window has label 0, and the 10:30 window — whose target")
        log.write("  is [10:30, 11:00) — has label 1. History is [t-w, t), so the")
        log.write("  10:30 window's 30-minute history counts the 10:00 OFF (count_30m=1)")
        log.write("  but not the 10:30 events themselves.")
        results["golden_windows"] = rows

        # ---------------------------------------------------------- step 2
        step("2. Start the local demo app (loopback, synthetic only)")
        tmp_db = tempfile.TemporaryDirectory(prefix="kindwindow-demo-db-")
        db_path = args.db or (Path(tmp_db.name) / "demo.db")
        server_log = (out_dir / "server.log").open("w", encoding="utf-8")
        server = subprocess.Popen(
            [
                UV,
                "run",
                "--locked",
                "kindwindow",
                "demo",
                "--synthetic",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--db",
                str(db_path),
            ],
            cwd=REPO_ROOT,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{args.port}"
        cold_start = time.perf_counter()
        ready = False
        while time.perf_counter() - cold_start < 120:
            if server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(base + "/", timeout=2) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.25)
        cold_seconds = time.perf_counter() - cold_start
        results["cold_start_seconds"] = round(cold_seconds, 3)
        log.write(
            f"cold launch to ready: {cold_seconds:.3f} s (process spawn -> first HTTP 200 on GET /)"
        )
        require(
            "server ready",
            ready and server.poll() is None,
            "server exited early; see demo/server.log",
        )

        try:
            resident = Client(base)
            contact_a = Client(base)
            contact_b = Client(base)
            anon = Client(base)

            step("3. Walkthrough — synthetic resident Rosa and Contact A")
            log.write(f"Resident: {RESIDENT_NAME}")
            log.write(
                f"Contact:  {CONTACT_A_NAME} (invited); "
                f"{CONTACT_B_NAME} (unrelated, must see nothing)"
            )
            for client, identity in (
                (resident, "resident"),
                (contact_a, "contact-a"),
                (contact_b, "contact-b"),
            ):
                status, body = client.call("POST", "/demo/login", {"identity": identity})
                require(f"login {identity}", status == 200, str(redact(body)))
            status, _ = anon.call("GET", "/preferences")
            check("unauthenticated /preferences -> 401", status == 401, f"got {status}")

            # Missing input: propose before any preferences exist.
            monday = next_weekday_10am(1)
            mon_1100 = slot_ts(monday, 11)
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 1,
                    "slot_start_utc": mon_1100,
                    "request_key": "walk-early",
                },
            )
            check(
                "propose with no preferences -> 409, nothing invented",
                status == 409 and "preferred hours" in body.get("detail", ""),
                f"{status} {body.get('detail')}",
            )
            log.write(
                "  (Missing input: the app refuses and says what is missing;"
                " it never invents a time or a forecast.)"
            )

            # Preferences, then the note parse: cancel once, confirm once.
            status, _ = resident.call("POST", "/preferences", INITIAL_PREFS)
            require(
                "set initial preferences (weekdays 09:00-17:00, quiet 12:00-13:00)", status == 200
            )
            status, body = resident.call("POST", "/notes/parse", {"text": NOTE_TEXT})
            require(
                "parse note -> pending only",
                status == 200
                and body.get("parsed", {}).get("preferred_range") == ["09:00", "12:00"],
                str(redact(body)),
            )
            log.write(f"  note: {NOTE_TEXT!r}")
            log.write(f"  parsed (pending, not yet live): {body['parsed']}")
            status, body = resident.call("POST", "/preferences/cancel")
            require(
                "cancel pending changes (deliberate cancel)",
                status == 200 and body.get("confirmed") is False,
            )
            _, prefs = resident.call("GET", "/preferences")
            check(
                "after cancel, live prefs unchanged (weekdays 09:00-17:00)",
                prefs.get("preferences", {}).get("preferred_days") == [1, 2, 3, 4, 5],
            )
            status, body = resident.call("POST", "/notes/parse", {"text": NOTE_TEXT})
            require("parse note again", status == 200)
            status, body = resident.call("POST", "/preferences/confirm", {"confirm": True})
            require(
                "confirm pending changes -> now live",
                status == 200 and body.get("confirmed") is True,
            )
            _, prefs = resident.call("GET", "/preferences")
            check(
                "confirmed prefs are Monday 09:00-12:00, quiet 10:00-10:30",
                prefs.get("preferences", {}).get("preferred_days") == [1]
                and prefs.get("preferences", {}).get("preferred_start") == "09:00"
                and prefs.get("preferences", {}).get("quiet_start") == "10:00",
            )

            # Invalid slots are refused, not guessed (the app's abstention).
            log.write("  Abstention in the app: an invalid slot is refused with a")
            log.write("  reason (409); the app never guesses a time. The study's")
            log.write("  abstention is separate: frozen gates withhold uncertain")
            log.write("  windows (50% retention -> macro Brier 0.069 on retained).")
            for label, slot, key in (
                ("quiet hours (Mon 10:15)", slot_ts(monday, 10, 15), "walk-quiet"),
                ("outside preferred hours (Mon 13:00)", slot_ts(monday, 13), "walk-out"),
                (
                    "non-preferred day (Tue 11:00)",
                    slot_ts(monday + timedelta(days=1), 11),
                    "walk-day",
                ),
            ):
                status, body = resident.call(
                    "POST",
                    "/invitations",
                    {
                        "contact_id": 1,
                        "slot_start_utc": slot,
                        "request_key": key,
                    },
                )
                check(f"propose {label} -> 409", status == 409, body.get("detail", ""))
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 99,
                    "slot_start_utc": mon_1100,
                    "request_key": "walk-nogrant",
                },
            )
            check("propose to ungranted contact -> 403", status == 403, f"got {status}")

            # Propose -> pending: contact A sees nothing yet.
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 1,
                    "slot_start_utc": mon_1100,
                    "request_key": "walk-a1",
                },
            )
            require(
                "propose Mon 11:00 to Contact A -> 201 pending",
                status == 201 and body.get("state") == "pending",
                str(redact(body)),
            )
            inv_a1 = body["invitation_id"]
            check(
                "resident view carries synthetic experimental probability 0.42",
                body.get("experimental_probability") == 0.42,
            )
            log.write("  Forecast context: the resident view shows a synthetic")
            log.write("  experimental probability (0.42, fixed demo value). The")
            log.write("  approval path works without it, and it is never sent to")
            log.write("  contacts (see the whitelisted inbox below).")
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check("Contact A inbox empty while pending", inbox.get("invitations") == [])
            # Duplicate replay: same request_key returns the same invitation.
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 1,
                    "slot_start_utc": mon_1100,
                    "request_key": "walk-a1",
                },
            )
            check(
                "duplicate replay -> 200, same invitation id",
                status == 200 and body.get("invitation_id") == inv_a1,
                f"{status} id={body.get('invitation_id')}",
            )

            # Approve -> A sees exactly one whitelisted invitation; B sees none.
            status, body = resident.call("POST", f"/invitations/{inv_a1}/approve")
            require(
                "approve -> 200 approved with 1h expiry",
                status == 200
                and body.get("state") == "approved"
                and body.get("expires_utc") is not None,
                str(redact(body)),
            )
            log.write(
                f"  approved; expires_utc={body['expires_utc']} "
                f"(now + 3600 s). Expiry at the exact second is covered by"
            )
            log.write("  tests/test_app.py::test_expiry_at_exact_expires_at (fake clock);")
            log.write("  the live demo does not sleep for an hour to show it.")
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check(
                "Contact A inbox has the approved invitation, whitelisted fields only",
                inbox.get("invitations")
                == [
                    {
                        "invitation_id": inv_a1,
                        "slot_start_utc": mon_1100,
                        "expires_utc": body["expires_utc"],
                    }
                ],
                str(redact(inbox)),
            )
            _, inbox_b = contact_b.call("GET", "/contact/invitations")
            check("Contact B inbox stays empty", inbox_b.get("invitations") == [])

            # Postpone on a second proposal: needs fresh approval.
            mon_1130 = slot_ts(monday, 11, 30)
            next_mon_1100 = slot_ts(monday + timedelta(days=7), 11)
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 1,
                    "slot_start_utc": mon_1130,
                    "request_key": "walk-a2",
                },
            )
            require("propose second invitation (Mon 11:30) -> 201", status == 201)
            inv_a2 = body["invitation_id"]
            require(
                "approve second invitation",
                resident.call("POST", f"/invitations/{inv_a2}/approve")[0] == 200,
            )
            status, body = resident.call(
                "POST", f"/invitations/{inv_a2}/postpone", {"new_slot_start_utc": next_mon_1100}
            )
            check(
                "postpone to next Mon 11:00 -> postponed, expiry cleared",
                status == 200
                and body.get("state") == "postponed"
                and body.get("expires_utc") is None,
                str(redact(body)),
            )
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check(
                "after postpone, Contact A sees only the first invitation",
                len(inbox.get("invitations", [])) == 1
                and inbox["invitations"][0]["invitation_id"] == inv_a1,
            )
            status, _ = resident.call("POST", f"/invitations/{inv_a2}/approve")
            check("fresh approval after postpone -> 200", status == 200)

            # Reject on a separate proposal to Contact B: stale approval fails.
            status, body = resident.call(
                "POST",
                "/invitations",
                {
                    "contact_id": 2,
                    "slot_start_utc": mon_1100,
                    "request_key": "walk-b1",
                },
            )
            require("propose to Contact B (Mon 11:00) -> 201", status == 201)
            inv_b1 = body["invitation_id"]
            status, body = resident.call("POST", f"/invitations/{inv_b1}/reject")
            check("reject -> 200 rejected", status == 200 and body.get("state") == "rejected")
            status, _ = resident.call("POST", f"/invitations/{inv_b1}/approve")
            check("stale approval of rejected proposal -> 409", status == 409)

            # Pause invalidates approved invitations; resume needs fresh approval.
            status, body = resident.call("POST", "/pause")
            check(
                "pause invalidates approved invitations",
                status == 200 and body.get("invalidated_approved") == 2,
                str(redact(body)),
            )
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check("Contact A inbox empty while paused", inbox.get("invitations") == [])
            status, _ = resident.call("POST", "/resume")
            check("resume -> 200", status == 200)
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check(
                "after resume, still nothing until fresh approval", inbox.get("invitations") == []
            )
            status, _ = resident.call("POST", f"/invitations/{inv_a1}/approve")
            check("fresh approval after resume -> 200", status == 200)
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check(
                "Contact A sees the re-approved invitation", len(inbox.get("invitations", [])) == 1
            )

            # Revoke is immediate and idempotent.
            status, body = resident.call("POST", f"/invitations/{inv_a1}/revoke")
            check("revoke -> 200 revoked", status == 200 and body.get("state") == "revoked")
            _, inbox = contact_a.call("GET", "/contact/invitations")
            check("Contact A inbox empty after revoke", inbox.get("invitations") == [])
            status, body = resident.call("POST", f"/invitations/{inv_a1}/revoke")
            check(
                "second revoke -> 200 revoked (idempotent)",
                status == 200 and body.get("state") == "revoked",
            )

            # ------------------------------------------------ latency
            step("4. Loopback latency (10 warm-ups + 100 measured, concurrency 1)")
            log.write("Protocol: monotonic client timer (time.perf_counter_ns),")
            log.write("boundary = request start -> response fully read; one client")
            log.write("at a time; warm-up calls discarded; the POST /preferences")
            log.write("case re-applies the identical confirmed prefs (idempotent")
            log.write("upsert), so no state reset is needed between timed calls.")
            log.write(
                "No other heavy work ran during timing. Machine: "
                f"{platform.machine()}, {os.cpu_count()} CPUs, "
                f"Python {platform.python_version()}."
            )
            endpoints = [
                ("GET /", resident, "GET", "/", None),
                ("GET /preferences", resident, "GET", "/preferences", None),
                ("GET /invitations", resident, "GET", "/invitations", None),
                ("GET /contact/invitations", contact_a, "GET", "/contact/invitations", None),
                ("POST /preferences", resident, "POST", "/preferences", CONFIRMED_PREFS),
            ]
            latency: dict[str, dict] = {}
            samples_path = out_dir / "latency_samples.csv"
            with samples_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["endpoint", "seq", "latency_ms", "status"])
                for name, client, method, path, payload in endpoints:
                    for _ in range(args.warmup):
                        client.timed(method, path, payload)
                    samples: list[float] = []
                    errors = 0
                    for seq in range(args.latency_n):
                        ms, status = client.timed(method, path, payload)
                        samples.append(ms)
                        writer.writerow([name, seq, f"{ms:.3f}", status])
                        if status != 200:
                            errors += 1
                    ordered = sorted(samples)
                    summary = {
                        "n": len(samples),
                        "warmup": args.warmup,
                        "errors": errors,
                        "p50_ms": round(percentile(ordered, 50), 3),
                        "p95_ms": round(percentile(ordered, 95), 3),
                        "min_ms": round(min(samples), 3),
                        "max_ms": round(max(samples), 3),
                        "mean_ms": round(statistics.fmean(samples), 3),
                    }
                    latency[name] = summary
                    check(
                        f"latency {name}: n={summary['n']} errors=0",
                        errors == 0,
                        f"p50={summary['p50_ms']} ms p95={summary['p95_ms']} ms",
                    )
                    log.write(
                        f"    {name}: p50={summary['p50_ms']} ms "
                        f"p95={summary['p95_ms']} ms min={summary['min_ms']} ms "
                        f"max={summary['max_ms']} ms mean={summary['mean_ms']} ms"
                    )
            results["latency"] = latency
            results["latency_samples"] = str(samples_path)
            log.write("  Suggested dev budget (packet 07): p95 <= 200 ms for simple")
            log.write("  reads/writes on an idle laptop. Verdict per endpoint is in")
            log.write("  docs/demo.md; the numbers above are the measured values.")

            # ------------------------------------------------ delete
            step("5. Delete the synthetic demo records")
            status, body = resident.call("POST", "/demo/delete")
            check(
                "demo delete -> remaining_invitations 0",
                status == 200 and body.get("remaining_invitations") == 0,
                str(redact(body)),
            )
            status, _ = resident.call("GET", "/preferences")
            check("resident session invalidated after delete -> 401", status == 401)
            status, _ = contact_a.call("GET", "/contact/invitations")
            check("contact session invalidated after delete -> 401", status == 401)
            status, _ = anon.call("POST", "/demo/login", {"identity": "resident"})
            check("login after delete fails -> 401", status == 401)
            log.write("  Fresh synthetic fixtures are reseeded on the next start,")
            log.write("  so repeat presentations begin from identical state.")
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
            server_log.close()
            tmp_db.cleanup()

        # ---------------------------------------------------------- meta
        mem_total_kb = None
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
        except OSError:
            pass
        try:
            import importlib.metadata as md

            versions = {d: md.version(d) for d in ("fastapi", "uvicorn")}
        except Exception:
            versions = {}
        results["machine"] = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
            "mem_total_kb": mem_total_kb,
            "sqlite": sqlite3.sqlite_version,
            "versions": versions,
            "db": "fresh temp SQLite (WAL) per run; foreign_keys ON",
        }
        results["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        (out_dir / "demo_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        log.write("")
        if failures:
            log.write(f"DEMO FINISHED WITH {len(failures)} FAILED CHECK(S): " + "; ".join(failures))
        else:
            log.write("DEMO FINISHED: all checks passed.")
        log.write(f"Log: {out_dir / 'demo_log.txt'}")
        log.write(f"Results: {out_dir / 'demo_results.json'}")
        log.write(f"Raw latency samples: {out_dir / 'latency_samples.csv'}")
        return 1 if failures else 0
    except DemoError as exc:
        log.write(f"DEMO ABORTED: {exc}")
        results["aborted"] = str(exc)
        (out_dir / "demo_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
