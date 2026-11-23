# KindWindow demo — protocol and results (Step 14)

Local integrated demo of the resident invitation app (Step 07) with the
frozen study results as disclosure-safe aggregates. Everything shown live
uses **synthetic fixture data only**: no real CASAS rows, no held-out
labels, no real people contacted. The demo runner is `demo/run_demo.py`
(stdlib only); its captured output for the run below is `demo/demo_log.txt`,
raw latency samples are `demo/latency_samples.csv`, and the machine-readable
summary is `demo/demo_results.json`.

## How to run

From the repo root, after `uv sync --locked`:

```bash
uv run --locked python demo/run_demo.py [--port 8001] [--out-dir demo]
    [--latency-n 100] [--warmup 10]
```

The runner:

0. prints the release commit and runs `kindwindow doctor`;
1. runs the golden JSON fixture through the clean pandas pipeline;
2. starts the app with `kindwindow demo --synthetic --host 127.0.0.1
   --port <port> --db <fresh temp dir>/demo.db` (loopback only, fresh
   synthetic database every run);
3. walks the scripted resident/contact scenario through real HTTP calls
   (separate cookie jars per identity);
4. measures loopback latency;
5. deletes the synthetic demo records through the app and verifies the
   sessions are invalidated.

One-command reset of the default on-disk demo database only (synthetic
data; nothing else is touched):

```bash
sh demo/reset_demo.sh   # removes local/demo.db* only
```

To serve the app interactively instead:

```bash
uv run --locked kindwindow demo --synthetic --host 127.0.0.1
```

## Recorded run

- Date: 22 November 2026 (US Central); branch `main`, commit
  `2942dfb15245592bdf007f3d62b7e222e3bd317c` (Step 13 merged).
- `kindwindow doctor`: OK — Python 3.12.3, golden fixture present
  (`tests/fixtures/golden_events.jsonl`, 9 events;
  `tests/fixtures/golden_windows.csv`, 3 windows), study config valid JSON.
- Golden fixture through the clean pandas pipeline: 9 lines in,
  9 accepted, 0 quarantined, 0 replays, ledger reconciles, **3 windows**,
  0 excluded, 0 discarded duplicates.
- Cold launch to ready (process spawn → first HTTP 200 on `GET /`):
  **0.550 s** (includes `uv run` startup; measured once, this run).
- Walkthrough: **all checks passed** (see `demo/demo_log.txt` for the
  full pass/fail list; 0 failures).

### Why the event at 10:30 belongs to the next forecast

The target interval is half-open `[t, t + 30m)`: an event exactly at `t`
belongs to that window's target; an event exactly at `t + 30m` belongs to
the next target. Golden fixture (`tests/fixtures/golden_windows.csv`):

| t | count_30m | recency_s | label |
|---|---|---:|---:|
| 2030-02-01T10:00:00 | 3 | 1 | 0 |
| 2030-02-01T10:30:00 | 1 | 1800 | 1 |
| 2030-02-01T11:00:00 | 2 | 1800 | 0 |

The 10:30 motion ON (and door OPEN) fall at the end of the 10:00 window's
target `[10:00, 10:30)`, so the 10:00 window has label 0 and the 10:30
window — target `[10:30, 11:00)` — has label 1. History is `[t-w, t)`, so
the 10:30 window's 30-minute history counts the 10:00 OFF (count_30m = 1)
but not the 10:30 events themselves.

## Walkthrough narrative

Synthetic identities: **Rosa**, the resident (app identity `resident`),
and **Contact A** (app identity `contact-a`, the invited contact);
**Contact B** (`contact-b`) is an unrelated contact who must see nothing.
All three log in with separate cookie jars.

1. **Missing input.** Proposing before any preferences exist returns
   409 `no preferred hours set; set preferred hours first`. The app
   refuses and says what is missing; it never invents a time or a forecast.
2. **Preferences.** Rosa sets weekdays, 09:00–17:00, quiet 12:00–13:00,
   contacts A and B.
3. **Note parse — cancel once, confirm once.** Rosa's note
   `Monday: available 09:00-12:00; quiet 10:00-10:30.` parses to days
   `[1]`, preferred `09:00–12:00`, quiet `10:00–10:30` and sits **pending**.
   She cancels deliberately: live preferences stay weekdays 09:00–17:00.
   She parses again and confirms: live preferences become Monday
   09:00–12:00, quiet 10:00–10:30. Nothing text-derived becomes active
   without explicit confirmation, and a partially parsed note never
   silently overwrites live preferences.
4. **Forecast and abstention.** The resident view of an invitation carries
   a synthetic experimental probability of **0.42** (a fixed demo value,
   `EXPERIMENTAL_PROBABILITY` in the app). It is shown only to the
   resident; the approval path works without it and it is never sent to
   contacts. The app's own abstention is refusal with a reason: proposing
   in quiet hours (Mon 10:15), outside preferred hours (Mon 13:00), on a
   non-preferred day (Tue 11:00), or to an ungranted contact all return
   409/403 with an explicit message — the app never guesses a time.
   The study's abstention is separate: frozen gates withhold uncertain
   windows (50% retention → macro Brier 0.069071 on the 100,812 retained
   test windows; see `reports/tables/step11_summary.md`).
5. **Propose → approve.** Rosa proposes Mon 2026-11-23 11:00 UTC to
   Contact A (201, pending). Contact A's inbox is empty while pending.
   Replaying the same `request_key` returns 200 with the same invitation
   id (duplicate prevention). Rosa approves (200, expires now + 3600 s);
   Contact A now sees exactly one invitation with whitelisted fields only
   (`invitation_id`, `slot_start_utc`, `expires_utc`) — no probability,
   no token, no internal fields. Contact B's inbox stays empty.
6. **Postpone (separate proposal).** A second invitation (Mon 11:30) is
   approved, then postponed to next Mon 11:00: state `postponed`, expiry
   cleared, Contact A sees only the first invitation until Rosa approves
   the postponed one fresh (200).
7. **Reject (separate proposal).** An invitation to Contact B (Mon 11:00)
   is rejected (200); a stale approval attempt then returns 409 — a
   rejected proposal cannot be revived.
8. **Pause / resume.** Pausing invalidates both approved invitations
   (`invalidated_approved: 2`); Contact A's inbox empties. After resume
   the inbox stays empty until Rosa re-approves fresh (200) — nothing is
   auto-revived.
9. **Revoke.** Revoking the re-approved invitation is immediate (Contact
   A's inbox empties) and idempotent (a second revoke still returns 200
   `revoked`).
10. **Delete.** `POST /demo/delete` removes the synthetic records
    (`remaining_invitations: 0`); resident and contact sessions are
    invalidated (401) and login afterwards fails (401). A fresh start
    reseeds identical synthetic fixtures for repeat presentations.

## Latency measurement protocol and results

**Protocol (packet 07 §"Demo script and measurements", item 5).**

- Boundary: request start → response fully read, measured on the client
  with a monotonic timer (`time.perf_counter_ns`), one client at a time
  (concurrency 1), over real loopback HTTP against the running server
  (not the in-process test client).
- Per endpoint: 10 warm-up calls (discarded) + 100 measured calls.
- Percentiles: nearest-rank p50/p95 over the 100 measured samples.
- The `POST /preferences` case re-applies the identical confirmed
  preferences (an idempotent upsert), so no state reset is needed between
  timed calls; mutating state was not reset between samples, by design.
- No other heavy work ran on the machine during timing.
- Raw samples: `demo/latency_samples.csv` (500 rows + header).

**Machine conditions (this run).** Linux 7.0.0-44-generic x86_64,
4 CPUs, 8,124,900 KB RAM, Python 3.12.3, FastAPI 0.115.12,
uvicorn 0.34.2, SQLite 3.45.1, fresh temp SQLite database in WAL mode
with foreign keys on. (The Step 12 systems study ran on a different
machine — AMD EPYC 9D25, 4 cores, 8.32 GB RAM — so these latencies are
not comparable to those benchmark times.)

**Measured results (n = 100 measured calls per endpoint, 0 errors).**

| Endpoint | p50 (ms) | p95 (ms) | min (ms) | max (ms) | mean (ms) |
|---|---:|---:|---:|---:|---:|
| GET / | 1.641 | 2.238 | 1.216 | 3.199 | 1.671 |
| GET /preferences | 2.814 | 3.376 | 2.420 | 3.977 | 2.864 |
| GET /invitations | 3.012 | 3.765 | 2.548 | 7.619 | 3.140 |
| GET /contact/invitations | 3.294 | 4.259 | 2.489 | 5.926 | 3.385 |
| POST /preferences | 3.332 | 3.841 | 2.878 | 4.160 | 3.355 |

Suggested development budget in the packet: p95 ≤ 200 ms for simple
reads/writes on an idle laptop. **Verdict: every measured endpoint is
far inside the budget** (worst p95 4.259 ms, worst single call 7.619 ms).
These are single-run measurements on this VM, not a claim about other
hardware; re-run `demo/run_demo.py` to reproduce them.

## Recording

No screen recording is included: this VM is headless, so a video would
have been staged rather than real. The deliverable is the working demo
plus its captured evidence — `demo/demo_log.txt` (full run output),
`demo/demo_results.json` (machine-readable summary) and
`demo/latency_samples.csv` (raw samples) from the run above, all produced
by the actual run, plus `demo/server.log` (uvicorn output). A recorded
fallback (screen capture with captions) is left for the presenter to
produce on the presentation laptop; the demo is deterministic enough to
re-record from a fresh checkout with one command.

## Omissions and limits (recorded honestly)

- **Expiry is not demonstrated live.** The live server uses the real
  clock, so the demo does not sleep an hour to show an invite expiring.
  Exact-expiry behaviour (access ends at exactly `expires_at`) is covered
  by `tests/test_app.py::test_expiry_at_exact_expires_at` using a fake
  clock. The demo shows the 1-hour `expires_utc` on approval.
- **Concurrent duplicate proposals are not re-run live.** The
  small-concurrency duplicate test in the packet is covered by
  `tests/test_app.py::test_concurrent_duplicate_proposals` (two threads,
  one invitation); the live demo shows the sequential replay case.
- **Notes-eval on held-out notes was not run** (reduced Step 11 scope);
  the parser shown is the frozen Step 06 parser on a synthetic dev-style
  note, not a held-out evaluation.
- **Reduced Step 11 scope** (stated wherever relevant): gap 6h/24h,
  degradation 10% events / 25% sensors / other seeds, abstention 75% —
  all omitted, not measured here.
- Latency is a single run (n = 100 per endpoint) on the VM above; no
  multi-run summary is claimed.
