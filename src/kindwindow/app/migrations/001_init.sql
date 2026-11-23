-- KindWindow demo migration 001_init.sql
-- Local synthetic demo only. Starting schema is starter/demo_constraints.sql
-- (account ownership, approved reads, uniqueness), extended here with
-- resident preferences, pending (text-derived) preferences, server-side
-- sessions, contact labels, and the opaque invite-token digest on
-- invitation. Every connection must also enable foreign_keys before
-- starting transactions (see store.connect).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS resident (
    resident_id INTEGER PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
);

CREATE TABLE IF NOT EXISTS contact (
    contact_id INTEGER PRIMARY KEY,
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contact_grant (
    resident_id INTEGER NOT NULL REFERENCES resident ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES contact ON DELETE CASCADE,
    PRIMARY KEY (resident_id, contact_id)
);

CREATE TABLE IF NOT EXISTS invitation (
    invitation_id INTEGER PRIMARY KEY,
    resident_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    slot_start_utc INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'postponed', 'rejected', 'revoked', 'expired')),
    expires_utc INTEGER,
    request_key TEXT NOT NULL,
    invite_token_digest TEXT,
    UNIQUE (resident_id, request_key),
    FOREIGN KEY (resident_id, contact_id) REFERENCES contact_grant ON DELETE CASCADE,
    CHECK (state <> 'approved' OR expires_utc IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS invitation_live_slot
ON invitation(resident_id, contact_id, slot_start_utc)
WHERE state IN ('pending', 'approved');

CREATE INDEX IF NOT EXISTS invitation_inbox
ON invitation(contact_id, state, expires_utc);

-- Resident preferences. Times are local HH:MM; the demo treats local time
-- as UTC. A range end earlier than its start means overnight into the
-- next day. preferred_days is a JSON array of ISO weekdays (1=Mon..7=Sun)
-- and applies to every interval.
CREATE TABLE IF NOT EXISTS preferences (
    resident_id INTEGER PRIMARY KEY REFERENCES resident ON DELETE CASCADE,
    preferred_days TEXT NOT NULL DEFAULT '[]',
    preferred_start TEXT,
    preferred_end TEXT,
    quiet_start TEXT,
    quiet_end TEXT
);

-- Text-derived (parser) changes awaiting explicit resident confirmation.
-- Nothing here is applied to preferences until POST /preferences/confirm.
CREATE TABLE IF NOT EXISTS pending_preferences (
    resident_id INTEGER PRIMARY KEY REFERENCES resident ON DELETE CASCADE,
    preferred_days TEXT,
    preferred_start TEXT,
    preferred_end TEXT,
    quiet_start TEXT,
    quiet_end TEXT,
    source_note TEXT,
    created_at_utc INTEGER NOT NULL
);

-- Server-side sessions for synthetic demo identities. Only the SHA-256
-- digest of the opaque token is stored; tokens are compared with
-- hmac.compare_digest and never written to logs.
CREATE TABLE IF NOT EXISTS session (
    token_digest TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('resident', 'contact')),
    resident_id INTEGER REFERENCES resident ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contact ON DELETE CASCADE,
    created_at_utc INTEGER NOT NULL,
    expires_at_utc INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS session_expiry
ON session(expires_at_utc);
