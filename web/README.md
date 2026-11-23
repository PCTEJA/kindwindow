# KindWindow — hosted demo site

A self-contained, static copy of the KindWindow demo interface
(`src/kindwindow/app/static/`), built to deploy to Vercel with no
backend and no build step. It runs entirely in the visitor's browser:
an in-browser store in `app.js` mirrors the rules of the in-repo
FastAPI app (`src/kindwindow/app/app.py`), and the note parser mirrors
the Step 06 parser v1.0.0 (`src/kindwindow/preferences/parser.py`).

- Resident view: visiting hours, write-it-in-words notes (pending
  until confirmed), propose/approve/postpone/reject/revoke invitations,
  pause/resume, delete demo data.
- Contact view: whitelisted inbox only (invitation number, visit time,
  expiry). No probability, token, or internal fields.
- Research view: verified aggregates only (test macro Brier 0.141 vs
  recency 0.185, abstention, gap/degradation, per-home Brier, systems
  medians, demo latency). No real sensor rows; no number is invented.

State lives in memory only — reloading the page resets the demo to its
starting state (one synthetic resident, Contact A and Contact B
granted, no preferences, no invitations, not paused). Synthetic
identities only; no real people are contacted, no data leaves the
device.

## Run locally

```bash
python3 -m http.server 8000 --directory web
# open http://127.0.0.1:8000/
```

## Deploy

```bash
# from the repository root, with the Vercel CLI or a linked project
vercel deploy web --prod
```

The authoritative live app is the in-repo demo — `kindwindow demo`
from the repository root — which runs the same interface against the
real FastAPI + SQLite backend on loopback.
