# Benchmark harness (Step 10)

How the KindWindow systems experiment (RQ2) is run, compared and
guarded. **Step 10 builds the harness; Step 12 runs the real study.**
No timed 1M/10M benchmarks are run here — only tiny smoke checks
(<= 10k events) that validate the harness itself.

## Layout

| Path | What it is |
|---|---|
| `generators/synthetic.py` | Train-only synthetic generator (identical bytes for both engines) |
| `generators/params_dev.json` | DEV-ONLY params, fitted from the 9-event golden fixture |
| `bench/parity.py` | Chunked pandas-vs-Spark window parity checker |
| `bench/run_benchmark.py` | Parent monitor: fresh child per attempt, caps, attempt CSV |
| `bench/worker.py` | Timed child: one engine, one input, one attempt |
| `bench/guardrails.py` | Preflight, timed-run lockfile, cap math, failure smoke checks |
| `bench/runs/example_attempt.csv` | Attempt-CSV schema + one clearly-labelled example row (not a measurement) |
| `local/bench-runs/` | Real attempt records (git-ignored via `local/`; `bench/runs/` is NOT git-ignored, so real runs never land there) |

## What is timed vs not

Timed (parent monotonic clock, started **before** the child is
launched, stopped only after output is committed):

* the whole child process, **including engine startup** — the
  SparkSession-ready time is additionally recorded separately
  (`startup_seconds` in the worker result and the attempt row), so
  startup can be reported apart from the end-to-end time;
* both engines run the same pipeline on the same immutable input
  bytes: read JSONL, normalize, write Snappy Parquet, reread, compute
  windows, complete the output write. Generation is outside timing.

Not timed: input generation, parity comparison, attempt-CSV writing,
environment recording. Parity runs outside timed intervals.

## Running the harness

```bash
# 1. Generate identical input bytes for both engines (outside timing)
export PATH="$HOME/.local/bin:$PATH"
uv run --locked python -m generators.synthetic \
  --params generators/params_dev.json --n 1000000 --seed 941 \
  --out local/synthetic-1m.jsonl

# 2. One timed attempt (standalone script, NOT the kindwindow CLI)
uv run --locked python -m bench.run_benchmark \
  --engine pandas --workload synthetic-1m \
  --input local/synthetic-1m.jsonl --attempt 1 --order-index 0

# 3. Compare the two engines' window outputs (outside timing)
uv run --locked python -m bench.parity \
  local/bench-runs/outputs/<pandas-run>/windows \
  local/bench-runs/outputs/<spark-run>/windows
```

Attempt order for the study (3 attempts per engine per workload):
pairs alternate P/S, S/P, P/S for one workload and reverse for the
next — see `bench.run_benchmark.plan_attempts()` / `--plan`.
Only one timed attempt runs at a time: `run_attempt` holds the
`local/bench.lock` lockfile; a second invocation fails fast instead of
contaminating the measurement. Run on a quiet machine with other heavy
jobs paused.

Spark settings are recorded per attempt (local[N] worker count,
shuffle partitions, AQE off, session timezone UTC, Parquet codec
snappy). The worker builds its own SparkSession with those settings
because `get_spark()` hardcodes `local[2]` / shuffle 4 for the
existing tests and the engines must not be edited in this step; the
safe defaults (UTC timezone, loopback driver host, `PYSPARK_PYTHON`)
are mirrored. Spark application caching is disabled and there is no
`.cache()` / `.persist()` anywhere in the timed path.

## Parity rules

`python -m bench.parity <a> <b>` (exit 0 pass, 1 fail, 2 error) or
`bench.parity.compare_paths(a, b)`. Inputs are Parquet files,
partitioned Parquet directories, or CSVs (golden shape).

* Keys `(home, t)` must be unique per side; comparison is a chunked
  sorted merge (default 100k rows/chunk), giving full outer coverage:
  keys only on one side fail the check.
* Integer features and labels compare exactly; floats compare with
  `abs(a-b) <= 1e-8`, relative tolerance 0.
* Missing/infinite conventions: null matches null only (null masks are
  compared); NaN matches NaN only; +inf matches +inf only, -inf
  matches -inf only; inf-vs-finite and NaN-vs-finite are mismatches.
  `recency_missing` must agree with the `recency_seconds` null mask
  where present.
* The pandas engine emits 16 window columns, Spark emits 8 (`y` for
  `label`); only columns present on both sides are compared and the
  result lists them. Aggregate hash checks alone are never sufficient.
* Mismatch diagnostics are redacted: first N mismatched keys only,
  each with just the key, column and the two values. No raw events.
* A parity failure tags the attempt's measurements invalid — defer
  interpretation of an unmatched pair.

## Caps and guardrails

* Time cap 1800 s, memory cap 70% of installed RAM (honouring a lower
  cgroup quota), both enforced on the **entire process tree**
  (Python + Java) by a 0.2 s sampler summing simultaneous RSS.
  Caveats: shared pages are double-counted, 0.2 s sampling can miss
  short peaks. `psutil` is used when installed; otherwise the sampler
  reads `/proc` directly (no new dependency was added in Step 10).
* The monitor terminates only its own process group (SIGTERM, then
  SIGKILL after 10 s) and verifies no orphan Java remains; any
  leftover owned PID is killed and reported (`orphan_java_remaining`).
* Statuses: `success` | `timeout` | `memory_limit` | `oom` |
  `disk_limit` | `error`. Non-success attempts have their partial
  output removed, so partial output can never reach parity.
  `disk_limit` fires when free disk falls below 256 MiB during a run
  or the child reports "No space left on device".
* Preflight (`bench.guardrails.preflight`) blocks a run — distinct
  from a measured failure — when another timed run holds
  `local/bench.lock`, no JVM is found, free disk is below the minimum,
  or the memory cap cannot be computed.
* Forced failure smoke checks (`bench.guardrails.smoke_timeout_check`,
  `smoke_memory_check`): a tiny workload with a 1 s cap (worker sleeps
  5 s via the test-only `--sleep-seconds` hook) must report `timeout`;
  a tiny workload with a 1 MB cap must report `memory_limit`; both
  must leave no partial output.

## Attempt records

One row per attempt in `local/bench-runs/attempts.csv`, using exactly
the kit header (`data/benchmark_attempt_header.csv` in the handoff
kit). Each attempt also gets a JSON sidecar
`local/bench-runs/<run_id>.json` (environment: CPU model, cores,
RAM, OS, Python/pandas/pyarrow/Spark/Java versions, JVM flags, thread
settings, partition counts, codec, UTC timestamps, git revision,
input/output bytes, peak tree memory, startup separately, last
samples) plus `<run_id>.worker.json`, `<run_id>.stdout.log` and
`<run_id>.stderr.log`. All failed attempts are preserved, including
timing/reason and last resource sample.

## Step 12 (real study — run)

Step 12 ran the real study (18 attempts, 15 successes) with the
harness above plus a real-workload mode (`--input-kind parquet`):

* Synthetic workloads use the JSONL pipeline as described above.
  The pandas path additionally writes + rereads normalized Parquet
  (`run_pipeline`); the Spark path does not persist a normalized
  copy — see `reports/benchmarks/README.md` for the scope note.
* Real workload: both engines read the same normalized Parquet tree
  (`local/normalized`, 78,275,425 events), compute windows, and write
  windows as Snappy Parquet (home + year-month). Times are floored to
  whole seconds for both engines (shared second-resolution contract).
  Spark reads with an explicit schema (`time` as `LongType` epoch ns;
  PySpark 3.5 rejects `TIMESTAMP(NANOS)`) and applies the frozen
  segmentation rule from a precomputed sidecar
  (`local/runs/step12/segment_splits.json`) plus a first-segment-wins
  dedup; pandas computes the rule in-process. Validated pre-study on
  aruba and hh108 (parity passes on all compared columns).
* Results: `reports/benchmarks/` (README, attempts.csv, summary.csv,
  parity.json); machine record + inventory:
  `local/runs/step12/manifest.json`. Headline: pandas faster at 1M
  (44.2 s vs 60.8 s median, n=3 valid pairs) and at real (109.8 s vs
  305.4 s, n=3 valid pairs); pandas 10M 0/3 (`memory_limit`), Spark
  10M 3/3 (median 228.1 s, feasibility observation only).
