# KindWindow — release notes (Step 15, final)

**Release:** final report and reproduction package, built on `main` at
`21ce26a66cd9e03fa72c674c7e86b5cbdf3521ec` ("Add integrated demo, slides, and
demo protocol", Step 14). This file describes the release as it stands in
the working tree: the Step 15 files listed below are **uncommitted local
work** for the coordinator to review and commit. This worker committed,
pushed, and created no PRs.

**Author/presenter:** Teja Piepur Chakravarthi — UNT CSCE 5300, Fall 2026.

## What is in this release

- **Final report:** `reports/report.md` — Abstract, Introduction, Problem
  statement and existing methods, Method/algorithm, Strengths/weaknesses/
  applications, Own simulations vs paper results (RQ1/RQ2/RQ3, parser,
  demo), Conclusion, References, plus reproduction and submission status.
  Every figure/table names the command that regenerates it.
- **Course matrix:** `reports/course_matrix.md` — every packet-required
  item, present or explicitly omitted with a reason.
- **Evidence index:** `reports/evidence.csv` — one row per claim/artifact
  with SHA-256, size, how it was produced/verified, and a re-verify
  command. Covers every committed file, the local (git-ignored) study
  artifacts, and the private CASAS archive (hashes only).
- **Reproduction scripts:** `scripts/reproduce.py` (profiles: fixture,
  verify, figures, demo, systems, real), `scripts/reproduce.sh` (POSIX
  wrapper), `scripts/reproduce.ps1` (PowerShell wrapper).
- **Figure generator:** `scripts/make_figures.py` — regenerates F5/F6/F7
  SVGs from committed tables (stdlib only; no matplotlib in the venv) and
  verifies the committed F3 figure's bar heights against
  `reports/tables/test_scores.csv` (`--check`: PASS).
- **Evidence builder:** `scripts/build_evidence.py` — (re)builds
  `reports/evidence.csv`; `--check` verifies the committed index.
- **New figures:** `reports/figures/runtime_vs_events.svg` (F6),
  `reports/figures/memory_vs_events.svg` (F7),
  `reports/figures/risk_coverage.svg` (F5).
- **Everything from Steps 01–14, unchanged:** cohort, pipelines, frozen
  model, test-once results, systems study, literature comparison, demo,
  slides. No committed report, table, model, generator, bench file, or
  config was modified in Step 15. The test split was not re-scored, the
  frozen model was not refit or modified, and `data/heldout/` was never
  read by any Step 15 script.

## Headline results (verified numbers — not re-derived here)

- RQ1: frozen calibrated forest macro Brier **0.140965** (AP 0.805909) vs
  recency baseline 0.184562; paired improvement **+0.043597 (23.6%)**,
  19/20 homes (sole exception hh119).
- RQ3: abstention 50% retention → Brier **0.069071** on 100,812 retained
  windows (coverage 0.4871); 30% events gone (seed 2207) → Brier
  **0.142559 (+0.0016)**; gap 12 h exclusion → Brier **0.162033** on
  176,506 kept windows.
- RQ2: pandas faster where both finish — 1M: 44.154 s vs 60.790 s; real
  78.3M events: 109.847 s vs 305.412 s (medians, n = 3, parity-passing
  pairs); 10M: pandas 0/3 memory_limit, Spark 228.088 s (feasibility only).
- Literature: 29 measurements, 6 papers, **0 genuinely comparable** — no
  published Brier/AP anchor for this task.
- Demo: 52/52 checks; worst recorded p95 4.259 ms (budget ≤ 200 ms);
  cold launch 0.550 s.

Full numbers, per-home tables, and limitations are in `reports/report.md`.

## How to reproduce it

Prerequisites: Python 3.12.3, uv 0.13.9 (`~/.local/bin/uv`), Java 17
(`~/workspace/tools/jdk-17.0.21.1+2/bin/java`, on PATH for Spark work).
No network, CASAS data, or credentials are needed for the fixture/demo/
figures profiles; the verify/real profiles need the private archive at
`~/workspace/kindwindow-data/data.zip`.

```bash
# Fixture profile — environment, doctor, golden pipeline, full test suite
sh scripts/reproduce.sh --profile fixture
# or: python3 scripts/reproduce.py --profile fixture

# Hash verification — CASAS archive, frozen model, split, cohort, config
python3 scripts/reproduce.py --profile verify

# Figures from committed tables + evidence-index check
python3 scripts/reproduce.py --profile figures

# Integrated synthetic demo (52 checks + latency)
python3 scripts/reproduce.py --profile demo

# Systems, fixture-scale (1M generation + bench/parity tests)
python3 scripts/reproduce.py --profile systems
# Full 18-attempt study (~48 min, exclusive machine, needs local/normalized):
python3 scripts/reproduce.py --profile systems --full

# Real-data profile — verification + documented commands ONLY.
# It never runs `kindwindow evaluate`, `train`, or `freeze`.
python3 scripts/reproduce.py --profile real
```

PowerShell: `.\scripts\reproduce.ps1 -Profile fixture` (add `-Full` for the
full systems study). Individual commands are also listed per figure/table
in `reports/report.md` §6.

**Test-once seal.** `kindwindow evaluate` ran exactly once in Step 11
(manifest written before any test row was read; 289.8 s). `reproduce.py`
refuses `--run-evaluate` and never invokes `evaluate`, `train`, or
`freeze`. A rerun of the real pipeline would also produce non-byte-identical
frozen artifacts (last-bit float nondeterminism in parallel forest refits);
the artifact of record is the hashed one in `docs/freeze-receipt.md`.

## Fresh-clone verification (Step 15, performed for real)

**Attempt 1 — FAILED on disk space (environmental, not a code failure).**
Cloned to `/tmp/kindwindow-fresh`; `uv sync` failed with
`No space left on device` because `/tmp` on this VM is a 512 MB tmpfs
(100% full). Nothing else ran. The clone directory was removed and
`/tmp` was left clean.

**Attempt 2 — PASS (with one documented, environmental test failure).**
Cloned `~/workspace/kindwindow` at `21ce26a` to
`~/workspace/kw-work/kindwindow-fresh` (144 GB free there), with `TMPDIR`
pointed at `~/workspace/kw-work/tmp` so test temp files do not fill `/tmp`.

- `git log`: `21ce26a Add integrated demo, slides, and demo protocol`.
- `git status`: clean at clone time (see note below on
  `tests/spark_plan.txt` — it is regenerated by running the suite).
- `uv sync --locked --group dev --group spark`: OK.
- `kindwindow doctor`: OK.
- `pytest -q`: **212 passed, 1 failed, 2 skipped** (83.5 s).
  The single failure is `tests/test_bench.py::test_attempt_csv_header_matches_kit_schema`,
  which resolves the kit as a sibling of the checkout
  (`ROOT.parent / "kindwindow-kit" / "data" / "benchmark_attempt_header.csv"`).
  In the fresh clone under `~/workspace/kw-work/` there is no sibling
  `kindwindow-kit`, so the file is missing and the test raises
  `FileNotFoundError`. It **passes in the original checkout**
  (`~/workspace/kindwindow`, kit at `~/workspace/kindwindow-kit` — verified
  in Step 15: `1 passed`). This is a pre-existing test portability
  limitation (the test assumes the repo sits next to the kit), not a
  regression and not a product failure. The same test passes at HEAD in
  the original location, where the full suite was 213 passed, 1 skipped.
- `demo/run_demo.py`: **52/52 checks passed**, 0 failures; cold launch
  0.801 s (vs 0.550 s in the recorded run — a different run under load);
  latencies p50/p95: GET / 1.366/1.901 ms, GET /preferences 3.815/5.836 ms,
  GET /invitations 3.814/6.152 ms, GET /contact/invitations 3.583/5.208 ms,
  POST /preferences 76.254/99.907 ms (n = 100, 0 errors each). Worst p95
  99.907 ms is still inside the packet budget (p95 ≤ 200 ms); the
  POST /preferences slowdown vs the recorded run (p95 3.841 ms) is
  consistent with the VM being busy with concurrent work during the
  fresh-clone run, and is reported as observed, not smoothed over.
  Fresh demo output: `~/workspace/kw-work/fresh-demo-out/` (not in git).
- Overall fresh-clone exit code: 0 (script `FRESH_CLONE_DONE exit=0`).

**Note on `tests/spark_plan.txt`.** Running the test suite regenerates
this file (Spark plan node IDs and startup timings differ per run), so it
shows as modified in the working tree both in the original repo and in
the fresh clone. It is a test artifact, not a source change; Step 15 did
not edit it. The pre-existing untracked file `PR01_DRAFT.md` is likewise
untouched by Step 15.

## Verified-data rerun (Step 15, performed for real)

Re-verified 22 November 2026 (US Central) by recomputing hashes over the
local artifacts — no test re-scoring, no retraining:

| Artifact | Expected | Recomputed | Result |
|---|---|---|---|
| CASAS `data.zip` SHA-256 | `fa6d2a9d74e2529304ff9632929658433580b5a44559ac9b3c6e2de9d9b7c83c` | same | MATCH |
| CASAS `data.zip` MD5 | `973a4ec0b67269566227ce8a5a3a9e21` | same | MATCH |
| CASAS `data.zip` bytes | 2,723,059,864 | 2,723,059,864 | MATCH |
| CASAS `data.zip` file members | 189 | 189 (190 namelist entries incl. the `data/` directory entry) | MATCH |
| Frozen model `local/models/frozen_model.json` SHA-256 | `b2989ae57cd8c7c5d64f8913b60299910dbacd24f490775a90c7e84354527a81` | same | MATCH |
| Split `data/manifests/split.csv` SHA-256 | `65f5a9974f944e7c23e9fb7e7a03d749459ba3ae02adc5aadc1419dd74d038cb` | same | MATCH |
| Cohort `data/manifests/cohort.csv` SHA-256 | `407d8ecff36a50d64ecb234641e8e5e5aa1c36c47821d8408377d69951f89f2f` | same | MATCH |
| Study config `configs/study.json` SHA-256 | `628db5f27bb48ea991333261e9a1533628cbbf26d817fa16ab72207f2b5d35b2` | same | MATCH |

Command: `python3 scripts/reproduce.py --profile verify` (or
`sha256sum`/`md5sum` on the paths above). The archive was verified with
the same values at acquisition (Step 03) and at setup; this rerun confirms
the local copy is unchanged.

## Known limits (carried from earlier steps, unchanged)

- Frozen artifacts are **not byte-reproducible** across refits (last-bit
  float nondeterminism in the parallel forest; artifact of record is the
  hashed one; predictions agree with recorded selection scores to 1e-12).
- **Reduced Step 11 scope:** gap 6 h/24 h, degradation 10% events /
  25% sensors / other seeds, abstention 75%, notes-eval — omitted, not
  measured. Paired household-bootstrap intervals and reliability bins
  (F4) were also not produced.
- **+3 floored windows vs Step 08:** 1,039,642 floored windows vs
  1,039,639 (one extra in each of hh104, hh111, hh116, all at exact hour
  boundaries); both engines agree exactly, so it is a property of the
  floored pipeline, not an engine difference; mechanism not isolated
  (open note).
- **psutil absent:** the benchmark memory sampler is a `/proc` fallback
  (0.2 s; shared pages double-counted; short peaks can be missed).
- **No screen recording:** working demo + captured log is the evidence
  (headless VM; a video would have been staged). Recorded fallback is
  for the presenter to produce on the presentation laptop.
- **Known homes only:** no claim about unseen homes; invitation utility
  is untested; per-home Brier ranges 0.0573–0.2057.
- **Scope asymmetry in the systems study:** the synthetic pandas path
  writes/rereads a normalized Parquet copy; the Spark path does not
  (documented in `reports/benchmarks/README.md`).
- **CLI stubs:** `kindwindow report|audit|cohort|generate|benchmark|parity|notes-eval`
  are registered but unimplemented (exit 2); the systems work used
  `generators.synthetic` and `local/runs/step12/*.py` directly.
- **Test portability:** `tests/test_bench.py::test_attempt_csv_header_matches_kit_schema`
  assumes the checkout sits next to the handoff kit; it fails in a
  fresh clone placed elsewhere (see fresh-clone section).
- **Fresh-clone environment note:** `/tmp` is a 512 MB tmpfs — do not
  clone or build there; use a directory on the main filesystem.

## Open items

1. **Slide/report export naming — OPEN.** The packet says PDF/PowerPoint
   export naming follows the Canvas/group-number convention and the group
   number was not in the packet. Checked again in Step 15 — still absent.
   **No group number is invented and no `.ppt`/`.pptx` filename is
   asserted.** Naming is TBD by the presenter once the group number is
   confirmed on Canvas.
2. **Report PDF — not built.** No PDF toolchain is pinned in the repo;
   `reports/report.md` is the source. PDF export (cover, pagination,
   light-background code listings) is a presenter step at submission.
3. **Canvas facts — to confirm at submission:** group number, Canvas
   destination, deadline, timezone, required formats, presentation and
   assistance rules. The brief's dates conflict with the proposal
   schedule; nothing is backdated and no extension is inferred.
4. **Turnitin value and submission receipt — OPEN** until the actual
   Canvas submission; to be recorded privately by the student.
5. **Omitted measurements** (reduced Step 11 scope; see §Known limits):
   gap 6 h/24 h, degradation 10%/25%-sensors/other seeds, abstention 75%,
   notes-eval, bootstrap intervals, reliability bins (F4).

## Final verified commit ledger (base → HEAD)

Every commit was checked with the kit identity guard
(`python3 ~/workspace/kindwindow-kit/starter/identity_guard.py --login PCTEJA --revision <sha>`)
in Step 15: **all 17 PASS** — verified login PCTEJA, GitHub user ID
39472754, email pcteja2000@gmail.com. Each commit's author and committer
are both `Teja Piepur Chakravarthi <pcteja2000@gmail.com>` (verified from
`git log --format='%an <%ae> | %cn <%ce>'`).

| # | Commit (SHA) | Subject | Identity guard |
|---|---|---|---|
| 1 | `7020ac4b9348144a20e0c3faafdb85a1b5b56710` | Initialize KindWindow project | PASS (PCTEJA 39472754) |
| 2 | `494360ecdbfb885ab61b85a680200cb98f02380a` | Add KindWindow foundation: README, CLI doctor, configs and fixtures | PASS (PCTEJA 39472754) |
| 3 | `05808fdb0682a5f5943a7e372f8b0ff158f28de5` | Add uv.lock and dependency-groups for dev/spark (uv 0.13.9, pytest 3 passed, doctor OK) | PASS (PCTEJA 39472754) |
| 4 | `112d756f06a312fe150c002a11d0946fcfd554d9` | Freeze contracts: data dictionary, sensor map, contracts module and tests (39 passed) | PASS (PCTEJA 39472754) |
| 5 | `b15000635e8f32e360f580cd8d92e8ebb43853a9` | Add preference parser v1.0.0 (dev 40/40 exact, 78 tests passed) | PASS (PCTEJA 39472754) |
| 6 | `c98f39d594c7ef0564c49426f229b9739989840e` | Add resident interface: FastAPI app, SQLite store, demo CLI (99 tests passed) | PASS (PCTEJA 39472754) |
| 7 | `7a7504bf813f798aec07245dd29a1b9eb02563ca` | Wire Step 06 parser into POST /notes/parse as pending-only (100 tests passed) | PASS (PCTEJA 39472754) |
| 8 | `5d3eb4ba2e79c4cc249e9a28a2c69e76ce3cb39e` | Add pandas pipeline: validate/reconcile, Parquet, forecast keys (119 tests passed) | PASS (PCTEJA 39472754) |
| 9 | `52ab44c31e65ce2a47c7b06475a54337d507b910` | Add Spark pipeline: JVM-only dense-grid engine, golden parity (124 tests passed, 1 skipped) | PASS (PCTEJA 39472754) |
| 10 | `7c1b9f0c2dee8300b84178988169043f55590179` | Freeze cohort: 20 selected homes, 190-row audit manifest (Step 03) | PASS (PCTEJA 39472754) |
| 11 | `46349116722dfc8d83b7851763000c4cc136bbc3` | Add systems harness and parity: generator, bench monitor, parity checker (Step 10) | PASS (PCTEJA 39472754) |
| 12 | `c4dfa98a5050841e44eac05699b6f1e48d2c6e18` | Add real features and baselines: CASAS ingest, vectorized windows, split, 3 baselines (Step 08) | PASS (PCTEJA 39472754) |
| 13 | `420d6445fc583a40ad85761e94170c007ee008ee` | Add candidate selection, calibration, and model freeze | PASS (PCTEJA 39472754) |
| 14 | `f14541229d14841f07f654ba00785e69ebce5922` | Add measured systems study: 18 timed attempts, parity, train-only generator params | PASS (PCTEJA 39472754) |
| 15 | `3fdf7105f1543d3f727474072df1502dad615f2d` | Add reduced Step 11 test-once evaluation: RQ1/RQ3, gap 12h, degradation, abstention | PASS (PCTEJA 39472754) |
| 16 | `2942dfb15245592bdf007f3d62b7e222e3bd317c` | Add literature comparison: 29 measurements, 0 genuinely comparable | PASS (PCTEJA 39472754) |
| 17 | `21ce26a66cd9e03fa72c674c7e86b5cbdf3521ec` | Add integrated demo, slides, and demo protocol | PASS (PCTEJA 39472754) |

HEAD is commit 17 (`21ce26a`). The Step 15 files in this release are
uncommitted working-tree additions; their own commit/PR record is for the
coordinator to add after review and merge (this worker performs no remote
writes). No Step 15 change touches `src/kindwindow/models/`, `bench/`,
`generators/`, `configs/study.json`, the frozen model, or any
already-committed report or table.

## Environment (Step 15 verification runs)

- Python 3.12.3, uv 0.13.9, Java OpenJDK 17.0.21.1
  (`~/workspace/tools/jdk-17.0.21.1+2/bin/java`).
- OS: Linux 7.0.0-44-generic x86_64, glibc 2.39; 4 CPUs visible in the VM.
- Full test suite at HEAD in the original checkout: **214 passed, 1 skipped**
  (measured in Step 15, 53.5 s; Step 13 recorded 213 passed, 1 skipped and
  Step 14 added the demo smoke test, bringing it to 214 passed, 1 skipped;
  the Step 15 fresh-clone run is 212 passed, 1 failed-environmental,
  2 skipped — see above).
- `ruff check` and `ruff format --check` clean on all files touched in
  Step 15 (`scripts/make_figures.py`, `scripts/reproduce.py`,
  `scripts/build_evidence.py`).
