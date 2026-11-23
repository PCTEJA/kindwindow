# KindWindow

Household Event Prediction and Scalable Sensor Data Processing — UNT CSCE 5300, Fall 2026, Teja Piepur Chakravarthi.

KindWindow is a reproducible study of recorded household sensor events and the computational cost of processing them. It tests whether recent event history improves 30-minute probability forecasts for known homes, when PySpark offers an advantage over a single-machine Python pipeline, and what abstention costs under degraded inputs.

## Research questions

- **RQ1:** Can recent sensor features improve future recorded-event probability estimates over constant-rate, hour-of-week, and recency baselines?
- **RQ2:** At what tested event volumes, if any, is PySpark faster than equivalent pandas transformations on the same machine?
- **RQ3:** Can predictive loss be reduced by withholding uncertain predictions under degraded inputs, and how much prediction coverage would be lost?

## Data

CASAS Smart Home dataset — free living, motion, door, activity labels, v1, Zenodo, 2025, DOI `10.5281/zenodo.15708568` (Cook). Only `data.zip` is used for forecasting. Up to 20 documented single-resident older-adult homes, at least 90 calendar days of interpretable logs, target at least 10 homes.

**Data privacy:** CASAS raw logs stay local and are never committed to git. Keep them under a local directory outside this checkout (see `KINDWINDOW_DATA`, e.g. `local/`), and use only retrieval instructions and checksums in the repo. Synthetic fixtures in `tests/fixtures/` are the only event data in this repository.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --group dev --group spark
```

Spark is an optional group so the laptop demo can launch without a JVM. For the timed Spark/pandas comparison, use WSL2/Linux for both engines on the same machine/filesystem where applicable.

## CLI

```bash
uv run --locked pytest -q
uv run --locked kindwindow doctor
uv run --locked kindwindow audit --archive <private-data.zip>
uv run --locked kindwindow cohort --freeze
uv run --locked kindwindow pipeline --engine pandas --workload real
uv run --locked kindwindow pipeline --engine spark --workload real
uv run --locked kindwindow train --config configs/study.json
uv run --locked kindwindow freeze
uv run --locked kindwindow evaluate --frozen --include-stress --include-gap-sensitivity
uv run --locked kindwindow generate --size 1000000
uv run --locked kindwindow generate --size 10000000
uv run --locked kindwindow benchmark --workloads real,1m,10m --attempts 3
uv run --locked kindwindow parity --workloads real,1m,10m
uv run --locked kindwindow notes-eval --frozen
uv run --locked kindwindow demo --synthetic --host 127.0.0.1
uv run --locked kindwindow report --from-manifest <verified-run-manifest>
```

Only `doctor` is implemented in PR 01; the other commands are the acceptance contract for later PRs and currently exit with a not-implemented message. Errors return nonzero and never silently switch real data to fixtures.

## Offline fixture demo

```bash
uv run --locked kindwindow doctor
```

`doctor` prints the Python version and checks that the golden fixture (`tests/fixtures/golden_events.jsonl`, 9 fabricated events, and `tests/fixtures/golden_windows.csv`, 3 windows) is present. No network, CASAS data, or credentials are needed for this check.

## Integrated demo and slides

```bash
uv run --locked python demo/run_demo.py
```

Runs the scripted synthetic resident/contact walkthrough against the
local app and measures loopback latency. See `docs/demo.md` for the
protocol, measured results, and the walkthrough narrative, and
`slides/slides.md` for the 15-minute presentation (presenter: Teja
Piepur Chakravarthi).

## Project layout

- `src/kindwindow/` — package and CLI
- `configs/study.json` — frozen study configuration template (pre-freeze in PR 01)
- `docs/course-decision.md` — redacted TA-acceptance decision record
- `tests/fixtures/` — synthetic, redistributable fixtures only
- `data/heldout/` — evaluation-only (created in later PRs); parser development must never read it

See `AGENTS.md` for agent rules, `docs/course-decision.md` for the course decision, and the handoff kit (`~/workspace/kindwindow-kit`, not in this repo) for the full delivery plan.
