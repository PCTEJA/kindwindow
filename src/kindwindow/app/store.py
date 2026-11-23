"""SQLite store for the KindWindow local demo app.

Every connection enables ``foreign_keys`` (per-connection pragma, as the
kit requires), uses bound parameters only, and runs writes inside explicit
transactions. Demo identities are synthetic and preseeded here.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

MIGRATION = Path(__file__).resolve().parent / "migrations" / "001_init.sql"

# Synthetic demo identities (local demo only, no real people).
DEMO_RESIDENT_ID = 1
DEMO_CONTACTS = {1: "Contact A", 2: "Contact B"}


def token_digest(token: str) -> str:
    """SHA-256 digest of an opaque token; only digests are stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys on and Row access."""
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(db_path: str | Path) -> Path:
    """Apply migration 001 and preseed the synthetic demo identities.

    Idempotent: safe to run on every app start / restart, so preferences
    and invitations persist across restarts.
    """
    path = Path(db_path)
    sql = MIGRATION.read_text(encoding="utf-8")
    conn = connect(path)
    try:
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO resident (resident_id, paused) VALUES (?, 0)",
                (DEMO_RESIDENT_ID,),
            )
            for contact_id, label in DEMO_CONTACTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO contact (contact_id, label) VALUES (?, ?)",
                    (contact_id, label),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO contact_grant (resident_id, contact_id)"
                    " VALUES (?, ?)",
                    (DEMO_RESIDENT_ID, contact_id),
                )
    finally:
        conn.close()
    return path
