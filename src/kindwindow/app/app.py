"""KindWindow resident invitation demo app (local demo only).

FastAPI + SQLite, bound to loopback only by the CLI. All identities are
synthetic demo identities (one resident, Contact A, Contact B); the app is
labelled as a local demo on every surface. No real people are contacted,
no sensor data is used, and no health / consent / willingness is inferred.

Auth: a simple local login for the preseeded demo identities
(``POST /demo/login``) issues an opaque server-side session token as an
HttpOnly cookie. Only the token digest is stored; every object access
re-validates the session, the caller's role, ownership and (for contacts)
the grant. Auth errors are predictable and generic so they never disclose
another household's invitation: 401 ``authentication required``,
403 ``forbidden``, 404 ``invitation not found``.

Clock: ``app.state.clock`` is a callable returning epoch seconds
(default ``time.time``). Tests replace it with a fake clock, so expiry is
tested without sleeping. Expiry is strict: access ends at exactly
``expires_at`` (``now < expires_at`` is required).

Pause (privacy-first default, documented in the UI text at ``GET /``):
pausing invalidates already-approved invitations by returning them to
``pending`` with their token/expiry cleared, so after resume they need a
fresh explicit approval before any contact can see them again.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import store

DEMO_LABEL = "Local demo — synthetic identities only. No real people are contacted."
INVITE_TTL_SECONDS = 3600  # short-lived invite: 1 hour
SESSION_TTL_SECONDS = 24 * 3600
# Synthetic experimental probability shown ONLY on the resident view.
# The approval path works without it; it is never sent to contacts.
EXPERIMENTAL_PROBABILITY = 0.42

AUTH_REQUIRED = {"detail": "authentication required"}
FORBIDDEN = {"detail": "forbidden"}
INVITATION_NOT_FOUND = {"detail": "invitation not found"}

# identity -> (role, resident_id, contact_id)
IDENTITIES: dict[str, tuple[str, int | None, int | None]] = {
    "resident": ("resident", store.DEMO_RESIDENT_ID, None),
    "contact-a": ("contact", None, 1),
    "contact-b": ("contact", None, 2),
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>KindWindow — local demo</title></head>
<body>
<h1>KindWindow — local demo</h1>
<p><strong>Local demo — synthetic identities only. No real people are contacted.</strong></p>
<p>Resident, Contact A and Contact B are synthetic demo identities. Log in
via <code>POST /demo/login</code> with <code>{{"identity": "resident"}}</code>,
<code>"contact-a"</code> or <code>"contact-b"</code>.</p>
<h2>Pause behaviour (privacy-first)</h2>
<p>Pausing stops new proposals and invalidates already-approved invitations:
they return to pending with their invite token and expiry cleared. After
resume they need a fresh explicit approval before any contact can see them.</p>
<h2>Routes</h2>
<ul>
<li>Resident: <code>GET/POST /preferences</code>, <code>POST /notes/parse</code>
(501 in this build — parser is Step 06, in progress),
<code>POST /preferences/confirm</code>, <code>POST /preferences/cancel</code>,
<code>POST /invitations</code> (propose),
<code>POST /invitations/{{id}}/approve|postpone|reject|revoke</code>,
<code>POST /pause</code>, <code>POST /resume</code>, <code>POST /demo/delete</code></li>
<li>Contact: <code>GET /contact/invitations</code> (whitelisted fields only)</li>
</ul>
<p>Times are local HH:MM; this demo treats local time as UTC. A range end
earlier than its start means overnight into the next day.</p>
</body></html>
"""


# ---------------------------------------------------------------- helpers
def _parse_hhmm(value: object) -> int | None:
    """HH:MM -> minutes since midnight, or None if malformed."""
    if not isinstance(value, str):
        return None
    m = _HHMM.match(value)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _fmt_minutes(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _in_range(minute: int, start: int, end: int) -> bool:
    """Half-open [start, end); end < start means overnight."""
    if end > start:
        return start <= minute < end
    return minute >= start or minute < end


def _range_minutes(start: int, end: int) -> set[int]:
    if end > start:
        return set(range(start, end))
    return set(range(start, 1440)) | set(range(0, end))


def _quiet_covers_preferred(p_start: int, p_end: int, q_start: int, q_end: int) -> bool:
    """True when every preferred minute is also quiet: no usable slot left.

    Partial overlap is normal (quiet is usually a sub-period inside the
    preferred window); only full coverage is a conflict.
    """
    return _range_minutes(p_start, p_end) <= _range_minutes(q_start, q_end)


def _parse_range(value: object) -> tuple[str, str] | None:
    """Accept {"start","end"} or [start, end]; return validated HH:MM pair."""
    if isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
    else:
        return None
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        return None
    if start == end:
        return None
    return (str(start), str(end))


def _slot_minutes_and_weekday(slot_start_utc: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(slot_start_utc, tz=timezone.utc)
    return dt.hour * 60 + dt.minute, dt.isoweekday()


def _check_slot(slot_start_utc: int, prefs: dict | None) -> str | None:
    """Return an error detail if the slot is not a valid resident slot."""
    if prefs is None or not prefs.get("preferred_days") or not prefs.get("preferred_start"):
        return "no preferred hours set; set preferred hours first"
    minute, weekday = _slot_minutes_and_weekday(slot_start_utc)
    if weekday not in prefs["preferred_days"]:
        return "slot is not on a preferred day"
    if not _in_range(
        minute, _parse_hhmm(prefs["preferred_start"]), _parse_hhmm(prefs["preferred_end"])
    ):
        return "slot is outside preferred hours"
    if prefs.get("quiet_start"):
        if _in_range(minute, _parse_hhmm(prefs["quiet_start"]), _parse_hhmm(prefs["quiet_end"])):
            return "slot is within quiet hours"
    return None


def _prefs_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "preferred_days": json.loads(row["preferred_days"]) if row["preferred_days"] else [],
        "preferred_start": row["preferred_start"],
        "preferred_end": row["preferred_end"],
        "quiet_start": row["quiet_start"],
        "quiet_end": row["quiet_end"],
    }


def _resident_view(row: sqlite3.Row) -> dict:
    """Resident-facing invitation view (may carry experimental context)."""
    return {
        "invitation_id": row["invitation_id"],
        "resident_id": row["resident_id"],
        "contact_id": row["contact_id"],
        "slot_start_utc": row["slot_start_utc"],
        "state": row["state"],
        "expires_utc": row["expires_utc"],
        "request_key": row["request_key"],
        "experimental_probability": EXPERIMENTAL_PROBABILITY,
    }


def _contact_view(row: sqlite3.Row) -> dict:
    """Whitelisted contact-facing fields only (see starter/contact_inbox.sql).

    No probability, no raw sensors, no token, no internal fields.
    """
    return {
        "invitation_id": row["invitation_id"],
        "slot_start_utc": row["slot_start_utc"],
        "expires_utc": row["expires_utc"],
    }


def _expire_stale(conn: sqlite3.Connection, now: int) -> None:
    """Mark approved invitations whose expiry has been reached as expired.

    Strict: at exactly expires_at access ends (``expires_utc <= now``).
    """
    conn.execute(
        "UPDATE invitation SET state='expired', invite_token_digest=NULL"
        " WHERE state='approved' AND expires_utc IS NOT NULL AND expires_utc <= ?",
        (now,),
    )


# ---------------------------------------------------------------- app factory
def create_app(db_path: str | Path, clock: Callable[[], float] | None = None) -> FastAPI:
    store.init_db(db_path)
    app = FastAPI(title="KindWindow demo (local, synthetic only)")
    app.state.db_path = Path(db_path)
    app.state.clock = clock or time.time
    app.state.demo_label = DEMO_LABEL

    def now() -> int:
        return int(app.state.clock())

    def db() -> sqlite3.Connection:
        return store.connect(app.state.db_path)

    # -- auth ------------------------------------------------------
    def current_session(request: Request) -> dict | None:
        token = request.cookies.get("kw_session")
        if not token:
            return None
        digest = store.token_digest(token)
        conn = db()
        try:
            row = conn.execute(
                "SELECT * FROM session WHERE token_digest = ?", (digest,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        # Constant-time comparison of the stored digest.
        if not hmac.compare_digest(row["token_digest"], digest):
            return None
        if row["expires_at_utc"] <= now():
            return None
        return dict(row)

    def require(request: Request, role: str) -> tuple[dict | None, JSONResponse | None]:
        sess = current_session(request)
        if sess is None:
            return None, JSONResponse(AUTH_REQUIRED, status_code=401)
        if sess["role"] != role:
            return None, JSONResponse(FORBIDDEN, status_code=403)
        return sess, None

    # -- landing / login -------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return LANDING_HTML

    @app.post("/demo/login")
    def login(payload: dict, request: Request):
        identity = payload.get("identity") if isinstance(payload, dict) else None
        if identity not in IDENTITIES:
            return JSONResponse({"detail": "authentication failed"}, status_code=401)
        role, resident_id, contact_id = IDENTITIES[identity]
        conn = db()
        try:
            if resident_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM resident WHERE resident_id = ?", (resident_id,)
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT 1 FROM contact WHERE contact_id = ?", (contact_id,)
                ).fetchone()
            if exists is None:
                # Demo records were deleted; login fails the same way for
                # every identity (no disclosure about what remains).
                return JSONResponse({"detail": "authentication failed"}, status_code=401)
            token = secrets.token_urlsafe(32)
            created = now()
            with conn:
                conn.execute(
                    "INSERT INTO session (token_digest, role, resident_id, contact_id,"
                    " created_at_utc, expires_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        store.token_digest(token),
                        role,
                        resident_id,
                        contact_id,
                        created,
                        created + SESSION_TTL_SECONDS,
                    ),
                )
        finally:
            conn.close()
        resp = JSONResponse(
            {
                "identity": identity,
                "role": role,
                "resident_id": resident_id,
                "contact_id": contact_id,
                "demo": DEMO_LABEL,
            }
        )
        resp.set_cookie("kw_session", token, httponly=True, samesite="lax", path="/")
        return resp

    @app.post("/demo/logout")
    def logout(request: Request):
        sess = current_session(request)
        if sess is not None:
            conn = db()
            try:
                with conn:
                    conn.execute(
                        "DELETE FROM session WHERE token_digest = ?", (sess["token_digest"],)
                    )
            finally:
                conn.close()
        resp = JSONResponse({"logged_out": True})
        resp.delete_cookie("kw_session", path="/")
        return resp

    # -- preferences -----------------------------------------------
    @app.get("/preferences")
    def get_preferences(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            resident = conn.execute(
                "SELECT paused FROM resident WHERE resident_id = ?", (sess["resident_id"],)
            ).fetchone()
            if resident is None:
                return JSONResponse(AUTH_REQUIRED, status_code=401)
            prefs = _prefs_row(
                conn.execute(
                    "SELECT * FROM preferences WHERE resident_id = ?", (sess["resident_id"],)
                ).fetchone()
            )
            pending = _prefs_row(
                conn.execute(
                    "SELECT * FROM pending_preferences WHERE resident_id = ?",
                    (sess["resident_id"],),
                ).fetchone()
            )
            grants = [
                {"contact_id": r["contact_id"], "label": r["label"]}
                for r in conn.execute(
                    "SELECT g.contact_id, c.label FROM contact_grant g"
                    " JOIN contact c ON c.contact_id = g.contact_id"
                    " WHERE g.resident_id = ? ORDER BY g.contact_id",
                    (sess["resident_id"],),
                ).fetchall()
            ]
        finally:
            conn.close()
        return {
            "demo": DEMO_LABEL,
            "resident_id": sess["resident_id"],
            "paused": bool(resident["paused"]),
            "preferences": prefs,
            "pending_preferences": pending,
            "contacts": grants,
        }

    @app.post("/preferences")
    def set_preferences(payload: dict, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "invalid preferences"}, status_code=422)
        conn = db()
        try:
            stored = _prefs_row(
                conn.execute(
                    "SELECT * FROM preferences WHERE resident_id = ?", (sess["resident_id"],)
                ).fetchone()
            ) or {
                "preferred_days": [],
                "preferred_start": None,
                "preferred_end": None,
                "quiet_start": None,
                "quiet_end": None,
            }
            merged = dict(stored)
            if "preferred_days" in payload:
                days = payload["preferred_days"]
                if (
                    not isinstance(days, list)
                    or not days
                    or any(not isinstance(d, int) or d < 1 or d > 7 for d in days)
                ):
                    return JSONResponse(
                        {"detail": "preferred_days must be a non-empty list of ISO weekdays 1-7"},
                        status_code=422,
                    )
                merged["preferred_days"] = sorted(set(days))
            if "preferred_range" in payload:
                rng = _parse_range(payload["preferred_range"])
                if rng is None:
                    return JSONResponse(
                        {"detail": "preferred_range must be HH:MM start/end with start != end"},
                        status_code=422,
                    )
                merged["preferred_start"], merged["preferred_end"] = rng
            if "quiet_range" in payload:
                if payload["quiet_range"] is None:
                    merged["quiet_start"] = merged["quiet_end"] = None
                else:
                    rng = _parse_range(payload["quiet_range"])
                    if rng is None:
                        return JSONResponse(
                            {"detail": "quiet_range must be HH:MM start/end with start != end"},
                            status_code=422,
                        )
                    merged["quiet_start"], merged["quiet_end"] = rng
            if not merged["preferred_days"] or not merged["preferred_start"]:
                return JSONResponse(
                    {"detail": "preferred_days and preferred_range are required"},
                    status_code=422,
                )
            # Conflict: quiet hours covering the whole preferred window
            # leave no usable slot (overnight spans included via
            # minute-set expansion). Partial overlap is normal.
            if merged["quiet_start"]:
                if _quiet_covers_preferred(
                    _parse_hhmm(merged["preferred_start"]),
                    _parse_hhmm(merged["preferred_end"]),
                    _parse_hhmm(merged["quiet_start"]),
                    _parse_hhmm(merged["quiet_end"]),
                ):
                    return JSONResponse(
                        {"detail": "quiet hours cover all preferred hours; no usable slot"},
                        status_code=409,
                    )
            contact_ids = payload.get("contact_ids")
            if contact_ids is not None:
                if not isinstance(contact_ids, list) or any(
                    not isinstance(c, int) for c in contact_ids
                ):
                    return JSONResponse(
                        {"detail": "contact_ids must be a list of integers"}, status_code=422
                    )
                known = {
                    r["contact_id"]
                    for r in conn.execute("SELECT contact_id FROM contact").fetchall()
                }
                if any(c not in known for c in contact_ids):
                    return JSONResponse({"detail": "unknown contact"}, status_code=422)
            with conn:
                conn.execute(
                    "INSERT INTO preferences"
                    " (resident_id, preferred_days, preferred_start, preferred_end,"
                    "  quiet_start, quiet_end) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(resident_id) DO UPDATE SET"
                    "  preferred_days=excluded.preferred_days,"
                    "  preferred_start=excluded.preferred_start,"
                    "  preferred_end=excluded.preferred_end,"
                    "  quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end",
                    (
                        sess["resident_id"],
                        json.dumps(merged["preferred_days"]),
                        merged["preferred_start"],
                        merged["preferred_end"],
                        merged["quiet_start"],
                        merged["quiet_end"],
                    ),
                )
                if contact_ids is not None:
                    conn.execute(
                        "DELETE FROM contact_grant WHERE resident_id = ?", (sess["resident_id"],)
                    )
                    for cid in sorted(set(contact_ids)):
                        conn.execute(
                            "INSERT INTO contact_grant (resident_id, contact_id) VALUES (?, ?)",
                            (sess["resident_id"], cid),
                        )
        finally:
            conn.close()
        return {"preferences": merged, "demo": DEMO_LABEL}

    # -- notes / pending preferences -------------------------------
    @app.post("/notes/parse")
    def parse_note(payload: dict, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        # Step 06 parser (v1.0.0) integrated: parsed fields are stored as
        # pending only — nothing is applied until POST /preferences/confirm.
        from kindwindow.preferences import parse_note as _parse

        text = payload.get("text", "") if isinstance(payload, dict) else ""
        parsed = _parse(text)
        if (
            parsed["days"] is None
            and parsed["preferred_range"] is None
            and parsed["quiet_range"] is None
        ):
            return {
                "parsed": parsed,
                "pending_preferences": None,
                "detail": "nothing could be parsed from the note; preferences unchanged",
            }
        pref = parsed["preferred_range"]
        quiet = parsed["quiet_range"]
        conn = db()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO pending_preferences
                       (resident_id, preferred_days, preferred_start, preferred_end,
                        quiet_start, quiet_end, source_note, created_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(resident_id) DO UPDATE SET
                         preferred_days=excluded.preferred_days,
                         preferred_start=excluded.preferred_start,
                         preferred_end=excluded.preferred_end,
                         quiet_start=excluded.quiet_start,
                         quiet_end=excluded.quiet_end,
                         source_note=excluded.source_note,
                         created_at_utc=excluded.created_at_utc""",
                    (
                        sess["resident_id"],
                        json.dumps(parsed["days"]) if parsed["days"] is not None else None,
                        pref[0] if pref else None,
                        pref[1] if pref else None,
                        quiet[0] if quiet else None,
                        quiet[1] if quiet else None,
                        text,
                        now(),
                    ),
                )
            pending = _prefs_row(
                conn.execute(
                    "SELECT * FROM pending_preferences WHERE resident_id = ?",
                    (sess["resident_id"],),
                ).fetchone()
            )
        finally:
            conn.close()
        return {
            "parsed": parsed,
            "pending_preferences": pending,
            "detail": "parsed fields are pending confirmation",
        }

    def _apply_or_cancel_pending(sess: dict, confirm: bool):
        conn = db()
        try:
            pending = _prefs_row(
                conn.execute(
                    "SELECT * FROM pending_preferences WHERE resident_id = ?",
                    (sess["resident_id"],),
                ).fetchone()
            )
            if pending is None:
                return JSONResponse(
                    {"detail": "no pending text-derived changes to confirm"}, status_code=409
                )
            if not confirm:
                with conn:
                    conn.execute(
                        "DELETE FROM pending_preferences WHERE resident_id = ?",
                        (sess["resident_id"],),
                    )
                return {"confirmed": False, "pending_preferences": None,
                        "detail": "pending text-derived changes cancelled; preferences unchanged"}
            # Re-validate conflicts before applying (never silently overwrite).
            if pending["quiet_start"] and pending["preferred_start"]:
                if _quiet_covers_preferred(
                    _parse_hhmm(pending["preferred_start"]),
                    _parse_hhmm(pending["preferred_end"]),
                    _parse_hhmm(pending["quiet_start"]),
                    _parse_hhmm(pending["quiet_end"]),
                ):
                    return JSONResponse(
                        {"detail": "pending changes: quiet hours cover all preferred hours"},
                        status_code=409,
                    )
            with conn:
                conn.execute(
                    "INSERT INTO preferences"
                    " (resident_id, preferred_days, preferred_start, preferred_end,"
                    "  quiet_start, quiet_end) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(resident_id) DO UPDATE SET"
                    "  preferred_days=excluded.preferred_days,"
                    "  preferred_start=excluded.preferred_start,"
                    "  preferred_end=excluded.preferred_end,"
                    "  quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end",
                    (
                        sess["resident_id"],
                        json.dumps(pending["preferred_days"]),
                        pending["preferred_start"],
                        pending["preferred_end"],
                        pending["quiet_start"],
                        pending["quiet_end"],
                    ),
                )
                conn.execute(
                    "DELETE FROM pending_preferences WHERE resident_id = ?",
                    (sess["resident_id"],),
                )
            return {"confirmed": True, "preferences": pending, "pending_preferences": None}
        finally:
            conn.close()

    @app.post("/preferences/confirm")
    def confirm_preferences(payload: dict | None = None, request: Request = None):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        confirm = True
        if isinstance(payload, dict) and "confirm" in payload:
            confirm = bool(payload["confirm"])
        return _apply_or_cancel_pending(sess, confirm)

    @app.post("/preferences/cancel")
    def cancel_preferences(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        return _apply_or_cancel_pending(sess, False)

    # -- invitations ------------------------------------------------
    @app.post("/invitations")
    def propose(payload: dict, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "invalid invitation"}, status_code=422)
        contact_id = payload.get("contact_id")
        slot = payload.get("slot_start_utc")
        request_key = payload.get("request_key")
        if not isinstance(contact_id, int) or not isinstance(slot, int) or not request_key:
            return JSONResponse(
                {"detail": "contact_id, slot_start_utc and request_key are required"},
                status_code=422,
            )
        if slot <= now():
            return JSONResponse({"detail": "slot must be in the future"}, status_code=422)
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                paused = conn.execute(
                    "SELECT paused FROM resident WHERE resident_id = ?", (sess["resident_id"],)
                ).fetchone()
                if paused is None:
                    return JSONResponse(AUTH_REQUIRED, status_code=401)
                if paused["paused"]:
                    return JSONResponse({"detail": "resident is paused"}, status_code=409)
                grant = conn.execute(
                    "SELECT 1 FROM contact_grant WHERE resident_id = ? AND contact_id = ?",
                    (sess["resident_id"], contact_id),
                ).fetchone()
                if grant is None:
                    return JSONResponse(FORBIDDEN, status_code=403)
                # Idempotence: same request_key + same payload -> same slot.
                existing = conn.execute(
                    "SELECT * FROM invitation WHERE resident_id = ? AND request_key = ?",
                    (sess["resident_id"], request_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["contact_id"] != contact_id
                        or existing["slot_start_utc"] != slot
                    ):
                        return JSONResponse(
                            {"detail": "request_key already used with a different invitation"},
                            status_code=409,
                        )
                    return JSONResponse(_resident_view(existing), status_code=200)
                prefs = _prefs_row(
                    conn.execute(
                        "SELECT * FROM preferences WHERE resident_id = ?",
                        (sess["resident_id"],),
                    ).fetchone()
                )
                slot_err = _check_slot(slot, prefs)
                if slot_err:
                    return JSONResponse({"detail": slot_err}, status_code=409)
                try:
                    cur = conn.execute(
                        "INSERT INTO invitation"
                        " (resident_id, contact_id, slot_start_utc, state, request_key)"
                        " VALUES (?, ?, ?, 'pending', ?)",
                        (sess["resident_id"], contact_id, slot, request_key),
                    )
                    inv_id = cur.lastrowid
                except sqlite3.IntegrityError:
                    # Concurrent duplicate: re-read inside the same txn.
                    dup = conn.execute(
                        "SELECT * FROM invitation WHERE resident_id = ? AND request_key = ?",
                        (sess["resident_id"], request_key),
                    ).fetchone()
                    if (
                        dup is not None
                        and dup["contact_id"] == contact_id
                        and dup["slot_start_utc"] == slot
                    ):
                        return JSONResponse(_resident_view(dup), status_code=200)
                    return JSONResponse(
                        {"detail": "slot already has a live invitation for this contact"},
                        status_code=409,
                    )
                row = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ?", (inv_id,)
                ).fetchone()
            return JSONResponse(_resident_view(row), status_code=201)
        finally:
            conn.close()

    @app.get("/invitations")
    def list_invitations(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                rows = conn.execute(
                    "SELECT * FROM invitation WHERE resident_id = ?"
                    " ORDER BY slot_start_utc, invitation_id",
                    (sess["resident_id"],),
                ).fetchall()
            return {"demo": DEMO_LABEL, "invitations": [_resident_view(r) for r in rows]}
        finally:
            conn.close()

    @app.get("/invitations/{invitation_id}")
    def get_invitation(invitation_id: int, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                row = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ? AND resident_id = ?",
                    (invitation_id, sess["resident_id"]),
                ).fetchone()
            if row is None:
                return JSONResponse(INVITATION_NOT_FOUND, status_code=404)
            return _resident_view(row)
        finally:
            conn.close()

    def _owned_invitation(conn: sqlite3.Connection, sess: dict, invitation_id: int):
        return conn.execute(
            "SELECT * FROM invitation WHERE invitation_id = ? AND resident_id = ?",
            (invitation_id, sess["resident_id"]),
        ).fetchone()

    @app.post("/invitations/{invitation_id}/approve")
    def approve(invitation_id: int, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                row = _owned_invitation(conn, sess, invitation_id)
                if row is None:
                    return JSONResponse(INVITATION_NOT_FOUND, status_code=404)
                if row["state"] not in ("pending", "postponed"):
                    return JSONResponse(
                        {"detail": "invitation cannot be approved in its current state"},
                        status_code=409,
                    )
                paused = conn.execute(
                    "SELECT paused FROM resident WHERE resident_id = ?", (sess["resident_id"],)
                ).fetchone()
                if paused["paused"]:
                    return JSONResponse(
                        {"detail": "resident is paused; approval requires resume"}, status_code=409
                    )
                grant = conn.execute(
                    "SELECT 1 FROM contact_grant WHERE resident_id = ? AND contact_id = ?",
                    (sess["resident_id"], row["contact_id"]),
                ).fetchone()
                if grant is None:
                    return JSONResponse(FORBIDDEN, status_code=403)
                prefs = _prefs_row(
                    conn.execute(
                        "SELECT * FROM preferences WHERE resident_id = ?",
                        (sess["resident_id"],),
                    ).fetchone()
                )
                slot_err = _check_slot(row["slot_start_utc"], prefs)
                if slot_err:
                    return JSONResponse({"detail": slot_err}, status_code=409)
                token = secrets.token_urlsafe(24)
                expires = now() + INVITE_TTL_SECONDS
                conn.execute(
                    "UPDATE invitation SET state='approved', expires_utc=?,"
                    " invite_token_digest=? WHERE invitation_id=?",
                    (expires, store.token_digest(token), invitation_id),
                )
                updated = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ?", (invitation_id,)
                ).fetchone()
            view = _resident_view(updated)
            view["invite_token"] = token  # returned once, to the resident only
            return view
        finally:
            conn.close()

    @app.post("/invitations/{invitation_id}/postpone")
    def postpone(invitation_id: int, payload: dict, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        new_slot = payload.get("new_slot_start_utc") if isinstance(payload, dict) else None
        if not isinstance(new_slot, int):
            return JSONResponse({"detail": "new_slot_start_utc is required"}, status_code=422)
        if new_slot <= now():
            return JSONResponse({"detail": "slot must be in the future"}, status_code=422)
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                row = _owned_invitation(conn, sess, invitation_id)
                if row is None:
                    return JSONResponse(INVITATION_NOT_FOUND, status_code=404)
                if row["state"] not in ("pending", "approved", "postponed"):
                    return JSONResponse(
                        {"detail": "invitation cannot be postponed in its current state"},
                        status_code=409,
                    )
                prefs = _prefs_row(
                    conn.execute(
                        "SELECT * FROM preferences WHERE resident_id = ?",
                        (sess["resident_id"],),
                    ).fetchone()
                )
                slot_err = _check_slot(new_slot, prefs)
                if slot_err:
                    return JSONResponse({"detail": slot_err}, status_code=409)
                clash = conn.execute(
                    "SELECT 1 FROM invitation WHERE resident_id = ? AND contact_id = ?"
                    " AND slot_start_utc = ? AND invitation_id != ?"
                    " AND state IN ('pending', 'approved')",
                    (sess["resident_id"], row["contact_id"], new_slot, invitation_id),
                ).fetchone()
                if clash is not None:
                    return JSONResponse(
                        {"detail": "slot already has a live invitation for this contact"},
                        status_code=409,
                    )
                # Postpone requires fresh approval: back to a non-approved
                # state with token/expiry cleared.
                conn.execute(
                    "UPDATE invitation SET state='postponed', slot_start_utc=?,"
                    " expires_utc=NULL, invite_token_digest=NULL WHERE invitation_id=?",
                    (new_slot, invitation_id),
                )
                updated = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ?", (invitation_id,)
                ).fetchone()
            return _resident_view(updated)
        finally:
            conn.close()

    @app.post("/invitations/{invitation_id}/reject")
    def reject(invitation_id: int, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                row = _owned_invitation(conn, sess, invitation_id)
                if row is None:
                    return JSONResponse(INVITATION_NOT_FOUND, status_code=404)
                if row["state"] not in ("pending", "approved", "postponed"):
                    return JSONResponse(
                        {"detail": "invitation cannot be rejected in its current state"},
                        status_code=409,
                    )
                conn.execute(
                    "UPDATE invitation SET state='rejected', expires_utc=NULL,"
                    " invite_token_digest=NULL WHERE invitation_id=?",
                    (invitation_id,),
                )
                updated = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ?", (invitation_id,)
                ).fetchone()
            return _resident_view(updated)
        finally:
            conn.close()

    @app.post("/invitations/{invitation_id}/revoke")
    def revoke(invitation_id: int, request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                row = _owned_invitation(conn, sess, invitation_id)
                if row is None:
                    return JSONResponse(INVITATION_NOT_FOUND, status_code=404)
                if row["state"] == "revoked":
                    # Idempotent: repeated revocation returns the same state.
                    return _resident_view(row)
                if row["state"] not in ("pending", "approved", "postponed"):
                    return JSONResponse(
                        {"detail": "invitation cannot be revoked in its current state"},
                        status_code=409,
                    )
                conn.execute(
                    "UPDATE invitation SET state='revoked', expires_utc=NULL,"
                    " invite_token_digest=NULL WHERE invitation_id=?",
                    (invitation_id,),
                )
                updated = conn.execute(
                    "SELECT * FROM invitation WHERE invitation_id = ?", (invitation_id,)
                ).fetchone()
            return _resident_view(updated)
        finally:
            conn.close()

    # -- pause / resume ---------------------------------------------
    @app.post("/pause")
    def pause(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                conn.execute(
                    "UPDATE resident SET paused=1 WHERE resident_id = ?", (sess["resident_id"],)
                )
                # Privacy-first: approved invitations are invalidated
                # (returned to pending, token/expiry cleared) and need a
                # fresh approval after resume; they are never auto-revived.
                cur = conn.execute(
                    "UPDATE invitation SET state='pending', expires_utc=NULL,"
                    " invite_token_digest=NULL WHERE resident_id = ? AND state='approved'",
                    (sess["resident_id"],),
                )
                invalidated = cur.rowcount
            return {
                "paused": True,
                "invalidated_approved": invalidated,
                "detail": (
                    "Paused. Approved invitations were invalidated and need"
                    " fresh approval after resume."
                ),
            }
        finally:
            conn.close()

    @app.post("/resume")
    def resume(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                conn.execute(
                    "UPDATE resident SET paused=0 WHERE resident_id = ?", (sess["resident_id"],)
                )
            return {
                "paused": False,
                "detail": (
                    "Resumed. Previously approved invitations remain pending"
                    " until re-approved."
                ),
            }
        finally:
            conn.close()

    # -- contact inbox ----------------------------------------------
    @app.get("/contact/invitations")
    def contact_invitations(request: Request):
        sess, err = require(request, "contact")
        if err is not None:
            return err
        conn = db()
        try:
            with conn:
                _expire_stale(conn, now())
                # starter/contact_inbox.sql, with :contact_id from the
                # validated server session and server-generated :now_utc.
                rows = conn.execute(
                    "SELECT i.invitation_id, i.slot_start_utc, i.expires_utc"
                    " FROM invitation AS i"
                    " JOIN contact_grant AS g USING (resident_id, contact_id)"
                    " JOIN resident AS r ON r.resident_id = i.resident_id"
                    " WHERE i.contact_id = :contact_id"
                    "   AND r.paused = 0"
                    "   AND i.state = 'approved'"
                    "   AND i.expires_utc > :now_utc"
                    " ORDER BY i.slot_start_utc, i.invitation_id",
                    {"contact_id": sess["contact_id"], "now_utc": now()},
                ).fetchall()
            return {"demo": DEMO_LABEL, "invitations": [_contact_view(r) for r in rows]}
        finally:
            conn.close()

    # -- demo delete --------------------------------------------------
    @app.post("/demo/delete")
    def demo_delete(request: Request):
        sess, err = require(request, "resident")
        if err is not None:
            return err
        resident_id = sess["resident_id"]
        conn = db()
        try:
            with conn:
                contact_ids = [
                    r["contact_id"]
                    for r in conn.execute(
                        "SELECT contact_id FROM contact_grant WHERE resident_id = ?",
                        (resident_id,),
                    ).fetchall()
                ]
                # Invalidate sessions/grants first, then delete the
                # resident row; invitations, preferences and grants
                # cascade via foreign keys.
                conn.execute("DELETE FROM session WHERE resident_id = ?", (resident_id,))
                for cid in contact_ids:
                    conn.execute("DELETE FROM session WHERE contact_id = ?", (cid,))
                conn.execute("DELETE FROM resident WHERE resident_id = ?", (resident_id,))
                for cid in contact_ids:
                    conn.execute("DELETE FROM contact WHERE contact_id = ?", (cid,))
                remaining = conn.execute("SELECT COUNT(*) AS n FROM invitation").fetchone()["n"]
            return {
                "deleted": True,
                "detail": (
                    "Synthetic demo records for this resident were deleted;"
                    " dependent invitations, preferences, grants and sessions"
                    " were removed. No aggregate test records are retained."
                ),
                "remaining_invitations": remaining,
            }
        finally:
            conn.close()

    return app


def create_app_for_cli(db_path: str | Path) -> FastAPI:
    """CLI entry point: real clock, file-backed demo database."""
    return create_app(db_path)
