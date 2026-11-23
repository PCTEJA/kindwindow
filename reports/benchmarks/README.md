# Step 12 — systems study (RQ2): measured comparison, one machine

Workloads, scope, results, and interpretation. Raw attempts:
`local/bench-runs/attempts.csv` (kit header schema) and the aggregate copy
`reports/benchmarks/attempts.csv`; per-workload × engine medians in
`reports/benchmarks/summary.csv`; per-pair parity in
`reports/benchmarks/parity.json` (detail: `local/bench-runs/parity/`).
Machine record and run inventory: `local/runs/step12/manifest.json`.

## Machine (from the manifest)

- CPU: AMD EPYC 9D25 126-Core Processor; 4 logical cores visible, affinity [0,1,2,3]
- RAM: 8,319,897,600 bytes installed, no cgroup quota (effective = installed);
  aggregate process-tree cap 70% = 5,823,928,320 bytes
- OS: Linux 7.0.0-44-generic x86_64, glibc 2.39; Python 3.12.3; pandas 2.2.3;
  pyarrow 19.0.1; numpy 2.2.5; pyspark 3.5.5; Java OpenJDK 17.0.21.1
  (workspace install at `~/workspace/tools/jdk-17.0.21.1+2/bin/java` —
  must be on PATH for Spark/preflight; not a system package)
- Run revision: `420d6445fc583a40ad85761e94170c007ee008ee` (working tree dirty:
  the Step 12 harness/params/tests, uncommitted — the coordinator commits)
- Threads: pandas/Arrow/BLAS 2; Spark `local[2]`, shuffle partitions 4,
  AQE off, session timezone UTC, Parquet codec snappy; Spark caching
  disabled, no `.cache()` / `.persist()`, no Python UDFs, no full-driver
  collect (only `count()`)
- Sampler: 0.2 s tree-RSS sampler (psutil absent → `/proc` fallback);
  shared pages double-counted; short peaks can be missed (see harness README)
- Page cache not controlled; not claimed cold. Quiet machine, one timed
  attempt at a time (bench lock), fresh processes per attempt.

## Workloads

| Workload | Input events | Input bytes | SHA-256 |
|---|---|---|---|
| synthetic-1m | 1,000,000 | 150,803,541 | `aee2ec73…` |
| synthetic-10m | 10,000,000 | 1,518,041,008 | `499f1682…` |
| real | 78,275,425 | 1.2 GB Parquet (700 files) | tree hash, see manifest |

- Synthetic inputs: `generators/params_train.json` (train-only fit; see
  `generators/params_train_fit.md`), seed 941, generated once outside
  timing. Generator emits identical immutable JSONL bytes for both engines.
- Real input: the Step 08 normalized Parquet tree (`local/normalized`).
  Both engines read the same tree; no JSONL round-trip at cohort scale
  (the pandas JSONL path materializes one dataclass per event and OOMs
  this VM at 78M events).

## Timed scope (what is inside timing, per workload family)

**Synthetic (JSONL pipeline).** Parent monotonic clock starts before the
child launches and stops after output is committed. Inside: engine
startup (recorded separately as `startup_seconds`), read JSONL,
normalize, compute windows, complete the output write. Scope asymmetry
(documented, not hidden): the pandas path (`run_pipeline`) additionally
writes the normalized events as Snappy Parquet and rereads them before
windowing; the Spark path (`load_events` → `build_windows` →
`write_windows`) does not persist a normalized copy — it is transient.
Both are each engine's canonical pipeline as frozen in Steps 05/07/10;
the comparison is end-to-end cost on identical input bytes, not a
line-for-line identical step list. The extra pandas normalized write is
small next to the runtimes (per-class bytes are recorded in the manifest
sidecars; see "Output bytes" below).

**Real (normalized Parquet → windows).** Inside timing: read the
normalized Parquet tree, compute windows, write windows as Snappy
Parquet partitioned by home + year-month, commit the output. No
normalized copy is written (the input already is the normalized
Parquet); no splits/EDA run inside timing. Shared normalizations,
applied identically to both engines (see `bench/worker.py`):
- event times floored to whole seconds (the JSONL interchange format and
  both engines' window contracts are second-resolution; the normalized
  Parquet stores millisecond-precision timestamps as `timestamp[ns]`).
  Validated on aruba: the floored pandas output matches Step 08
  `local/features` on all keys and all columns except `recency_seconds`,
  which is exactly +1 s on every row (flooring the previous event's time
  lengthens the floored gap by its fractional part).
- clock-reversal segmentation follows the frozen
  `assign_segments_from_epochs` rule. Pandas computes it in-process;
  Spark applies the same rule's boundaries from a precomputed sidecar
  (`local/runs/step12/segment_splits.json`: aruba, hh108, hh111 have 2
  segments; the other 17 homes have 1), plus a first-segment-wins
  dedup of cross-segment duplicate keys (aruba: 37). Validated on the
  two multi-segment homes: pandas-vs-Spark parity passes on all 6
  compared columns (aruba 10,553 windows; hh108 103,540 windows).
- Spark reads the Parquet with an explicit schema (PySpark 3.5 rejects
  `TIMESTAMP(NANOS)`: `Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))`);
  `time` is declared `LongType` and arrives as epoch nanoseconds, floored
  to epoch seconds — the same values pandas floors from `timestamp[ns]`.
- The floored real outputs contain **1,039,642 windows vs Step 08's
  1,039,639**: exactly one extra window in each of hh104, hh111, and
  hh116, all at exact hour boundaries (hh104 and hh116:
  2011-06-15 02:00:00; hh111: 2014-11-02 03:00:00), with no Step 08
  window missing. Both engines agree exactly on all 1,039,642 floored
  keys (parity pass), so the +3 is a property of the floored pipeline,
  not an engine difference; its precise mechanism (second-resolution
  flooring interacting with window anchoring at hour boundaries) was
  not isolated and is recorded here as an open note, not a claim.

## Results

18 attempts planned, 18 recorded, **15 successes, 3 failures** (all 3
are pandas synthetic-10m `memory_limit`). All 6 completed pairs pass
parity. Study wall time: 2026-11-23T03:02:37Z → 03:50:35Z (48 min).

### Attempt table (raw)

See `reports/benchmarks/attempts.csv` (all 18 attempts, failures
included, with `parity_status`). Summary:

| Workload | Engine | Attempts | Success | Median (min–max) s | Startup median s | Peak tree median (max) | Throughput ev/s | Parity |
|---|---|---|---|---|---|---|---|---|
| synthetic-1m (1M ev, 82,773 win) | pandas | 3 | 3/3 | **44.154** (43.621–44.206) | 0.626 | 1.27 GB (1.31 GB) | 22,648 | 3/3 pass |
| synthetic-1m (1M ev, 82,773 win) | spark | 3 | 3/3 | **60.790** (57.051–60.856) | 8.991 | 1.06 GB (1.89 GB) | 16,450 | 3/3 pass |
| real (78,275,425 ev, 1,039,642 win) | pandas | 3 | 3/3 | **109.847** (109.354–112.332) | 1.302 | 1.24 GB (1.27 GB) | 712,586 | 3/3 pass |
| real (78,275,425 ev, 1,039,642 win) | spark | 3 | 3/3 | **305.412** (301.973–305.533) | 8.974 | 1.55 GB (2.60 GB) | 256,295 | 3/3 pass |
| synthetic-10m (10M ev) | pandas | 3 | **0/3** | unavailable (memory_limit) | — | hit 5.82 GB cap | unavailable | not run |
| synthetic-10m (10M ev, 829,024 win) | spark | 3 | 3/3 | **228.088** (225.321–240.610) | 9.139 | 1.73 GB (2.51 GB) | 43,843 | not run (no pair) |

Full medians/min/max, startup, peak, output bytes, and
throughput are in `reports/benchmarks/summary.csv` (throughput is
`input_events / median elapsed`).

### Median / min / max of successful end-to-end runtimes

See the table above and `reports/benchmarks/summary.csv`. Startup is
reported separately and is included in the end-to-end time. With zero
completions the value is unavailable (never 0); every successful
cell here has n=3 except synthetic-10m pandas (n=0).

### Output bytes

`output_bytes` counts everything under the attempt output directory
(median across attempts of that workload × engine):

- synthetic-1m pandas 17,911,042 = `normalized/` 16,098,967 +
  `windows/` 1,811,633 + `_COMPLETE.json` 442.
- synthetic-1m spark 860,044 = `windows/` only (8 columns vs the
  pandas 16, hence ~half the windows bytes: 860k vs 1,811k).
- real pandas 29,596,166 = `windows/` 29,473,071 + `_COMPLETE.json`
  123,095 (the manifest lists ~500 window files).
- real spark 16,421,473 = `windows/` 16,421,221 + `_COMPLETE.json` 252.
- synthetic-10m spark 8,149,978 = `windows/` only (829,024 windows,
  8 columns). Pandas 10M wrote nothing (partial output removed on
  `memory_limit`).

### Failures

- synthetic-10m pandas a1/a2/a3: `memory_limit` at 178.829 s /
  171.298 s / 167.746 s; peak tree RSS 5,824,860,160 /
  5,828,468,736 / 5,831,700,480 bytes against the 5,823,928,320-byte
  cap. The pandas JSONL path materializes one dataclass per event
  during ingest, so 10M events exhaust the 70% RAM cap before
  windowing starts. No partial output remains; no timeouts, OOM
  kills, disk errors, or other failures occurred in any attempt.
- No orphan Java remained after any attempt
  (`orphan_java_remaining == []` in every record).

## Parity

Every completed pandas/spark pair was compared outside timed intervals
with `bench.parity.py` (chunked sorted merge, 1e-8 abs tol, rtol 0,
full outer key coverage). Compared columns: `count_5m`, `count_30m`,
`count_120m`, `door_30m`, `recency_seconds`, `label`. Results:

- synthetic-1m a1/a2/a3: **pass** (82,773 / 82,773 keys, 0 mismatches).
- real a1/a2/a3: **pass** (1,039,642 / 1,039,642 keys, 0 mismatches).
- synthetic-10m a1/a2/a3: **not run** — no completed pair (pandas
  failed), so Spark's 10M outputs are uncompared; they are a
  feasibility observation, not a validated comparison.

A parity failure would have tagged both attempts
`parity_status=fail` (INVALID for interpretation); none occurred.
Interpretation below uses only parity-passing pairs (`valid_*`
fields in `summary.csv`, which equal the success fields here for
1M and real).

## Interpretation (parity-passing comparisons only)

- **Pandas was faster at both matched sizes, with n=3 valid pairs
  each.** Synthetic-1m: pandas median 44.2 s vs Spark 60.8 s
  (Spark 1.38× slower). Real cohort (78.3M events): pandas median
  109.8 s vs Spark 305.4 s (Spark 2.78× slower). Both orderings rest
  on 3/3 parity-passing pairs per workload.
- **No ordering reversal was observed over the tested interval.**
  The interval runs from 1M synthetic events to the 78M-event real
  cohort; pandas led at both ends. No crossover is claimed and none
  is interpolated — a crossover, if one exists, lies outside the
  tested interval or is masked by the 10M pandas feasibility
  boundary (below).
- **At 10M synthetic JSONL events, pandas cannot complete on this
  machine under the 70% RAM cap** (0/3, all `memory_limit` during
  ingest), while Spark completes in 225–241 s. That is a
  resource-feasibility boundary for the pandas JSONL pipeline, not
  a speed ordering: there is no matched 10M pair, so no
  pandas-vs-Spark ordering is claimed at 10M.
- **Throughput must not be compared across families.** Real pandas
  (712,586 ev/s) vs synthetic-1m pandas (22,648 ev/s) differs
  because the real path reads columnar Parquet while the synthetic
  path parses JSONL and materializes dataclasses — input format and
  pipeline scope, not engine scaling. Within a family, throughput
  follows the runtime ordering above.
- **Startup is a fixed Spark tax.** Spark pays ~9 s per attempt for
  the JVM session build (15% of the 1M runtime, ~3% of real, ~4%
  of 10M); pandas startup is 0.5–1.4 s. Startup is included in the
  end-to-end times above and reported separately in the summary.
- **Memory headroom was ample except for pandas at 10M.** All 15
  successes peaked at ≤ 2.60 GB (cap 5.82 GB). Spark's peak RSS
  varies more run to run than pandas' (e.g. 1.06–1.89 GB at 1M) —
  JVM heap nondeterminism, not a trend.
- **Runtime variance was small.** Pandas ranges: 0.6 s at 1M,
  3.0 s at real. Spark ranges: 3.8 s at 1M, 3.6 s at real,
  15.3 s at 10M. No attempt came close to the 1800 s time cap
  (longest success: real Spark, 305.5 s).

## Reproduction

- Fit: `uv run --locked python local/runs/step12/build_train_summary.py`
  then `generators.synthetic.fit_params_from_summary_csv` (see
  `generators/params_train_fit.md`).
- Generate: `uv run --locked python -m generators.synthetic --params
  generators/params_train.json --n 1000000 --seed 941 --out
  local/synthetic-1m.jsonl` (same for 10M).
- Run: `uv run --locked python local/runs/step12/run_study.py`
  (18 attempts, sequential, exclusive lock).
- Parity + aggregates: `uv run --locked python
  local/runs/step12/run_parity_and_aggregate.py`.
- Tests: `uv run --locked pytest tests/test_bench.py
  tests/test_benchmark_study.py -q` (19 passed at study time).
