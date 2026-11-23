# AGENTS.md — KindWindow

Project-specific rules for agents working in this repo. The repo-wide identity rule is absolute: **every Git commit must have author and committer `Teja Piepur Chakravarthi <pcteja2000@gmail.com>`, and every GitHub action must run as the verified account for pcteja2000@gmail.com (login PCTEJA).** Do not use any other email or account. Do not set global git config; configure only locally in a repo checkout:

```bash
git config --local user.name 'Teja Piepur Chakravarthi'
git config --local user.email 'pcteja2000@gmail.com'
git config --local user.useConfigOnly true
```

Verify with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT` before committing, and run the kit identity guard (`python3 ~/workspace/kindwindow-kit/starter/identity_guard.py --login PCTEJA`) before any remote write.

## Paths

- `src/kindwindow/` — package, `src/layout`, single `kindwindow` CLI (`src/kindwindow/cli.py`)
- `configs/study.json` — frozen study config (PR 02 freezes it; PR 01 copy is a pre-freeze template)
- `tests/fixtures/` — synthetic fixtures only; golden fixture lives here
- `docs/` — decision records, data dictionary, demo notes
- `data/heldout/` — **evaluation-only. Parser development must NOT read it.** Only the evaluation worker may unlock it after the parser freeze (PR 06). Do not open, hash-compare against dev notes, or copy it into dev paths.
- `local/` — private, git-ignored study data (CASAS archive, normalized/derived real data, manifests). Never commit it. `KINDWINDOW_DATA` may point to a separate private study directory.
- Handoff kit is at `~/workspace/kindwindow-kit` — external reference only, never the repo, never committed.

## Setup / test commands (exact)

```bash
uv sync --locked --group dev --group spark
uv run --locked pytest -q
uv run --locked kindwindow doctor
```

If `uv` is not installed, note that and test with `python3 -m pytest -q` / `PYTHONPATH=src python3 -m kindwindow.cli doctor` as a fallback; do not block on uv, and do not invent a lockfile. One owner edits `uv.lock`, the CLI registry, and shared schemas at a time. Use `uv sync --locked`; never edit `uv.lock` by hand.

## Held-out data boundary

- Dev notes: 40 synthetic notes for parser development.
- Test notes: 20 synthetic notes in `data/heldout/` — evaluation-only, sealed until PR 06 freeze.
- Never train, tune, or debug on held-out labels. Record the freeze/hash before any evaluation.

## Benchmark exclusivity

Timed benchmark runs (PR 12) run with exclusive access — one timed attempt at a time, fresh processes, no other heavy work on the same machine during a timed attempt. Documentation/review may proceed elsewhere. Record CPU/RAM/OS/library versions, thread settings, Spark worker/partition counts, and every timed attempt including failures/timeouts.

## Secrets

No external model, email, cloud, or dataset API keys are required. Never put a credential, token, PAT, `.env`, Canvas credential, or private correspondence in git, in a fixture, log, example config, or PR body. Do not print tokens (`gh auth token` is forbidden). Use the verified `gh` CLI session only; check presence/identity only.

## PR lifecycle (per 02_GITHUB_AND_AGENTS.md)

1. One branch per deliverable, from current `origin/main`, e.g. `step/01-foundation`.
2. Stage explicit paths; inspect staged names and diff for credentials, PDFs, holdout labels, and local paths.
3. Commit with the verified identity; run the identity guard over the full feature history.
4. Push only via the verified CLI credential helper, then `gh pr create` with purpose, exact tests/results, affected data, evidence links, and unresolved items.
5. Merge only after checks / local verification on the exact candidate merge; preserve both git emails through integration (local `--no-ff` merge in a clean worktree, guard on `HEAD`, then push). No self-approval, admin override, fabricated review, squash/rebase that rewrites authorship, or closing a PR to simulate a merge.
6. One coordinator performs all remote writes. Workers return local changes, tests, and review findings only — no remote repo creation, push, or PR from a worker.
