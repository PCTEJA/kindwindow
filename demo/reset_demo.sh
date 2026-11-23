#!/bin/sh
# Reset the KindWindow local demo: remove ONLY the default on-disk demo
# database (local/demo.db and its SQLite WAL/SHM sidecars). Nothing else
# is touched — no study data, no fixtures, no other local/ files.
# The app reseeds fresh synthetic identities on the next start, so repeat
# presentations begin from identical state.
# Usage (from the repo root):  sh demo/reset_demo.sh
set -eu
cd "$(dirname "$0")/.."
rm -f local/demo.db local/demo.db-wal local/demo.db-shm
echo "demo database reset: local/demo.db* removed (synthetic demo data only)"
