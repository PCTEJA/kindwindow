"""Step 07 resident-interface tests (local demo only, synthetic identities).

Separate TestClient instances = separate cookie jars for the resident,
Contact A and unrelated Contact B. The clock is parameterized (a fake
clock object on ``app.state.clock``); expiry is tested by advancing it,
never by sleeping.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from kindwindow.app import create_app
from kindwindow.cli import build_parser
from kindwindow.cli import main as cli_main

# Monday 2026-11-23 08:00 UTC (verified: 2026-11-23 is a Monday).
BASE = int(datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc).timestamp())


def ts(day_offset: int, hour: int, minute: int = 0) -> int:
    return BASE + day_offset * 86400 + (hour - 8) * 3600 + minute * 60


MON_10 = ts(0, 10)  # Mon 10:00 — valid preferred slot
MON_1230 = ts(0, 12, 30)  # Mon 12:30 — inside quiet hours
MON_18 = ts(0, 18)  # Mon 18:00 — outside preferred hours
TUE_10 = ts(1, 10)
TUE_01 = ts(1, 1)  # Tue 01:00 — overnight tail of Mon 20:00-02:00
WED_10 = ts(2, 10)

PREFS = {
    "preferred_days": [1, 2, 3, 4, 5],
    "preferred_range": {"start": "09:00", "end": "17:00"},
    "quiet_range": {"start": "12:00", "end": "13:00"},
    "contact_ids": [1, 2],
}


class FakeClock:
    def __init__(self, now: int = BASE):
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app_and_db(tmp_path, clock):
    db_path = tmp_path / "demo.db"
    app = create_app(db_path, clock=clock)
    return app, db_path


@pytest.fixture
def clients(app_and_db):
    app, _ = app_and_db
    out = {}
    for name, identity in (("resident", "resident"), ("a", "contact-a"), ("b", "contact-b")):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/demo/login", json={"identity": identity})
        assert r.status_code == 200, (identity, r.text)
        out[name] = c
    return out


def set_prefs(resident, prefs=None):
    r = resident.post("/preferences", json=prefs or PREFS)
    assert r.status_code == 200, r.text
    return r


def propose(resident, slot=MON_10, contact_id=1, key="k1"):
    return resident.post(
        "/invitations",
        json={"contact_id": contact_id, "slot_start_utc": slot, "request_key": key},
    )


def approve(resident, inv_id):
    return resident.post(f"/invitations/{inv_id}/approve")


def seed_pending(db_path, days=(6, 7), start="10:00", end="12:00"):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO pending_preferences"
                " (resident_id, preferred_days, preferred_start, preferred_end,"
                "  quiet_start, quiet_end, source_note, created_at_utc)"
                " VALUES (1, ?, ?, ?, NULL, NULL, 'synthetic note', ?)",
                (json.dumps(list(days)), start, end, BASE),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------- auth
def test_unauthenticated_and_wrong_role(app_and_db, clients):
    app, _ = app_and_db
    anon = TestClient(app, raise_server_exceptions=False)
    r = anon.get("/preferences")
    assert r.status_code == 401
    assert r.json() == {"detail": "authentication required"}
    # Contact may not use resident routes; resident may not use the inbox.
    assert clients["a"].get("/preferences").status_code == 403
    assert clients["a"].get("/preferences").json() == {"detail": "forbidden"}
    assert clients["resident"].get("/contact/invitations").status_code == 403
    # Unknown identity login fails predictably, without disclosure.
    assert anon.post("/demo/login", json={"identity": "someone-else"}).status_code == 401


def test_unknown_invitation_id_is_generic_404(clients):
    # Same 404 for the resident (unknown id) and for a contact probing
    # another household's / another contact's invitation.
    assert clients["resident"].get("/invitations/9999").status_code == 404
    assert clients["resident"].get("/invitations/9999").json() == {
        "detail": "invitation not found"
    }
    assert clients["b"].post("/invitations/9999/approve").status_code in (403, 404)


# ---------------------------------------------------------------- preferences
def test_preferences_set_get_and_overnight_span(clients):
    set_prefs(clients["resident"])
    got = clients["resident"].get("/preferences").json()
    assert got["preferences"]["preferred_days"] == [1, 2, 3, 4, 5]
    assert got["preferences"]["preferred_start"] == "09:00"
    assert got["contacts"] == [
        {"contact_id": 1, "label": "Contact A"},
        {"contact_id": 2, "label": "Contact B"},
    ]
    assert got["paused"] is False
    # Overnight preferred span (20:00-02:00) is accepted when it does not
    # overlap quiet hours, and its overnight tail is a valid slot.
    r = clients["resident"].post(
        "/preferences",
        json={
            "preferred_days": [1, 2],
            "preferred_range": {"start": "20:00", "end": "02:00"},
            "quiet_range": {"start": "03:00", "end": "04:00"},
        },
    )
    assert r.status_code == 200, r.text
    assert propose(clients["resident"], slot=TUE_01, key="night").status_code == 201


def test_preferences_conflict_and_bad_input(clients):
    # Quiet hours covering the whole preferred window leave no usable
    # slot -> explicit 409. Partial overlap (quiet inside preferred) is
    # normal and is covered by the default PREFS in other tests.
    r = clients["resident"].post(
        "/preferences",
        json={
            "preferred_days": [1],
            "preferred_range": {"start": "09:00", "end": "17:00"},
            "quiet_range": {"start": "08:00", "end": "18:00"},
        },
    )
    assert r.status_code == 409
    assert "no usable slot" in r.json()["detail"]
    # Same for an overnight span fully covered by quiet hours.
    r = clients["resident"].post(
        "/preferences",
        json={
            "preferred_days": [1],
            "preferred_range": {"start": "20:00", "end": "02:00"},
            "quiet_range": {"start": "19:00", "end": "03:00"},
        },
    )
    assert r.status_code == 409
    assert clients["resident"].post(
        "/preferences", json={"preferred_days": [8], "preferred_range": ["09:00", "17:00"]}
    ).status_code == 422
    assert clients["resident"].post(
        "/preferences",
        json={"preferred_days": [1], "preferred_range": {"start": "09:00", "end": "09:00"}},
    ).status_code == 422


def test_notes_parse_is_stub_and_changes_nothing(clients):
    set_prefs(clients["resident"])
    r = clients["resident"].post("/notes/parse", json={"text": "weekdays 9 to 5, quiet at noon"})
    assert r.status_code == 501
    assert "not implemented" in r.json()["detail"]
    assert clients["resident"].get("/preferences").json()["preferences"]["preferred_days"] == [
        1, 2, 3, 4, 5,
    ]
    # Contact may not parse notes either.
    assert clients["a"].post("/notes/parse", json={"text": "x"}).status_code == 403


def test_confirm_and_cancel_pending_changes(app_and_db, clients):
    _, db_path = app_and_db
    set_prefs(clients["resident"])
    seed_pending(db_path)  # simulates Step 06 parser output awaiting review
    assert clients["resident"].get("/preferences").json()["pending_preferences"] is not None
    # Cancel: pending discarded, live preferences unchanged.
    r = clients["resident"].post("/preferences/cancel")
    assert r.status_code == 200
    assert r.json()["confirmed"] is False
    got = clients["resident"].get("/preferences").json()
    assert got["pending_preferences"] is None
    assert got["preferences"]["preferred_days"] == [1, 2, 3, 4, 5]
    # Confirm via {"confirm": false} also cancels; confirm applies.
    seed_pending(db_path)
    r = clients["resident"].post("/preferences/confirm", json={"confirm": False})
    assert r.status_code == 200 and r.json()["confirmed"] is False
    seed_pending(db_path)
    r = clients["resident"].post("/preferences/confirm", json={"confirm": True})
    assert r.status_code == 200 and r.json()["confirmed"] is True
    got = clients["resident"].get("/preferences").json()
    assert got["preferences"]["preferred_days"] == [6, 7]
    assert got["preferences"]["preferred_start"] == "10:00"
    assert got["pending_preferences"] is None
    # Nothing pending -> explicit 409, not a silent no-op.
    r = clients["resident"].post("/preferences/confirm", json={"confirm": True})
    assert r.status_code == 409
    assert clients["resident"].post("/preferences/cancel").status_code == 409


# ---------------------------------------------------------------- propose
def test_propose_validates_slot_quiet_and_pause(clients):
    set_prefs(clients["resident"])
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    r = propose(clients["resident"])
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "pending"
    # Contact sees nothing before approval.
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    # Outside preferred hours, inside quiet hours, non-preferred day, past.
    assert propose(clients["resident"], slot=MON_18, key="k-out").status_code == 409
    assert propose(clients["resident"], slot=MON_1230, key="k-quiet").status_code == 409
    assert propose(clients["resident"], slot=ts(5, 10), key="k-sat").status_code == 409  # Saturday
    assert propose(clients["resident"], slot=BASE - 60, key="k-past").status_code == 422
    # No preferences at all -> explicit error (fresh app, no prefs set).
    # Pause blocks new proposals.
    assert clients["resident"].post("/pause").status_code == 200
    assert propose(clients["resident"], slot=TUE_10, key="k-paused").status_code == 409
    assert clients["resident"].post("/resume").status_code == 200
    assert propose(clients["resident"], slot=TUE_10, key="k-paused").status_code == 201


def test_propose_without_preferences(app_and_db):
    app, _ = app_and_db
    resident = TestClient(app, raise_server_exceptions=False)
    resident.post("/demo/login", json={"identity": "resident"})
    assert propose(resident).status_code == 409
    assert "preferred hours" in propose(resident).json()["detail"]


def test_propose_requires_grant(clients):
    # Narrow grants to Contact A only; proposing to B is forbidden.
    set_prefs(clients["resident"], {**PREFS, "contact_ids": [1]})
    assert propose(clients["resident"], contact_id=2, key="k-b").status_code == 403


# ---------------------------------------------------------------- idempotence
def test_request_key_idempotence_and_conflicts(clients):
    set_prefs(clients["resident"])
    first = propose(clients["resident"])
    assert first.status_code == 201
    again = propose(clients["resident"])
    assert again.status_code == 200
    assert again.json()["invitation_id"] == first.json()["invitation_id"]
    assert again.json()["slot_start_utc"] == MON_10
    # Same key, different payload -> one explicit conflict.
    assert propose(clients["resident"], slot=TUE_10, key="k1").status_code == 409
    # Different key, same live slot -> one explicit conflict.
    assert propose(clients["resident"], slot=MON_10, key="k2").status_code == 409
    assert len(clients["resident"].get("/invitations").json()["invitations"]) == 1


def test_concurrent_duplicate_proposals(clients):
    set_prefs(clients["resident"])
    results = []

    def _go():
        results.append(propose(clients["resident"], key="k-race").status_code)

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) in ([200, 201], [201, 201])
    assert len(clients["resident"].get("/invitations").json()["invitations"]) == 1


# ---------------------------------------------------------------- approve / inbox
def test_approve_creates_opaque_invite_and_contact_whitelist(clients, clock):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    r = approve(clients["resident"], inv["invitation_id"])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved"
    assert r.json()["expires_utc"] == BASE + 3600
    assert r.json()["invite_token"]  # opaque token, resident only, once
    assert r.json()["experimental_probability"] == 0.42
    inbox_a = clients["a"].get("/contact/invitations").json()["invitations"]
    assert inbox_a == [
        {
            "invitation_id": inv["invitation_id"],
            "slot_start_utc": MON_10,
            "expires_utc": BASE + 3600,
        }
    ]
    # Whitelist: no probability / token / internal fields leak to contacts.
    assert set(inbox_a[0]) == {"invitation_id", "slot_start_utc", "expires_utc"}
    # Unrelated Contact B sees nothing.
    assert clients["b"].get("/contact/invitations").json()["invitations"] == []
    # Approving twice is a conflict, not a second invite.
    assert approve(clients["resident"], inv["invitation_id"]).status_code == 409


def test_expiry_at_exact_expires_at(clients, clock):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    clock.advance(3599)  # one second before expiry: still visible
    assert len(clients["a"].get("/contact/invitations").json()["invitations"]) == 1
    clock.advance(1)  # exactly expires_at: access ends
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    assert clients["resident"].get("/invitations").json()["invitations"][0]["state"] == "expired"


# ---------------------------------------------------------------- postpone / reject
def test_postpone_requires_fresh_approval(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    assert len(clients["a"].get("/contact/invitations").json()["invitations"]) == 1
    r = clients["resident"].post(
        f"/invitations/{inv['invitation_id']}/postpone",
        json={"new_slot_start_utc": TUE_10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "postponed"
    assert r.json()["slot_start_utc"] == TUE_10
    assert r.json()["expires_utc"] is None
    # Contact sees nothing until fresh approval.
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    assert approve(clients["resident"], inv["invitation_id"]).status_code == 200
    inbox = clients["a"].get("/contact/invitations").json()["invitations"]
    assert [i["slot_start_utc"] for i in inbox] == [TUE_10]
    # Postpone to a quiet slot is rejected and leaves the invite unchanged.
    assert clients["resident"].post(
        f"/invitations/{inv['invitation_id']}/postpone",
        json={"new_slot_start_utc": ts(1, 12, 30)},
    ).status_code == 409
    got = clients["resident"].get(f"/invitations/{inv['invitation_id']}").json()
    assert got["slot_start_utc"] == TUE_10


def test_reject_ends_proposal(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    r = clients["resident"].post(f"/invitations/{inv['invitation_id']}/reject")
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    # Stale approval cannot revive a rejected proposal.
    assert approve(clients["resident"], inv["invitation_id"]).status_code == 409
    r = clients["resident"].post(f"/invitations/{inv['invitation_id']}/reject")
    assert r.status_code == 409


# ---------------------------------------------------------------- pause / revoke
def test_pause_invalidates_approved_and_requires_fresh_approval(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    r = clients["resident"].post("/pause")
    assert r.status_code == 200
    assert r.json()["invalidated_approved"] == 1
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    # The invite is back to pending, not auto-revived.
    got = clients["resident"].get(f"/invitations/{inv['invitation_id']}").json()
    assert got["state"] == "pending"
    assert clients["resident"].post("/resume").status_code == 200
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    assert approve(clients["resident"], inv["invitation_id"]).status_code == 200
    assert len(clients["a"].get("/contact/invitations").json()["invitations"]) == 1


def test_revoke_immediate_and_idempotent(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    r = clients["resident"].post(f"/invitations/{inv['invitation_id']}/revoke")
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    # Second revoke: same result, still 200 (idempotent).
    r2 = clients["resident"].post(f"/invitations/{inv['invitation_id']}/revoke")
    assert r2.status_code == 200 and r2.json()["state"] == "revoked"
    assert approve(clients["resident"], inv["invitation_id"]).status_code == 409


def test_cross_contact_isolation(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    # B cannot act on A's invitation: resident-only routes reject the
    # contact role with the same generic 403 as any wrong-role call, and
    # B's own inbox stays empty. (A resident probing an unknown id gets
    # the generic 404; see test_unknown_invitation_id_is_generic_404.)
    assert clients["b"].post(f"/invitations/{inv['invitation_id']}/revoke").status_code == 403
    assert clients["b"].post(f"/invitations/{inv['invitation_id']}/approve").status_code == 403
    assert clients["b"].get(f"/invitations/{inv['invitation_id']}").status_code == 403
    assert clients["b"].get("/contact/invitations").json()["invitations"] == []
    assert clients["a"].get("/contact/invitations").json()["invitations"] != []


# ---------------------------------------------------------------- delete / restart
def test_demo_delete_cascades_and_invalidates_sessions(app_and_db, clients):
    app, db_path = app_and_db
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve(clients["resident"], inv["invitation_id"])
    r = clients["resident"].post("/demo/delete")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["remaining_invitations"] == 0
    # Sessions invalidated: resident and contacts are logged out.
    assert clients["resident"].get("/preferences").status_code == 401
    assert clients["a"].get("/contact/invitations").status_code == 401
    anon = TestClient(app, raise_server_exceptions=False)
    assert anon.post("/demo/login", json={"identity": "resident"}).status_code == 401
    assert anon.post("/demo/login", json={"identity": "contact-a"}).status_code == 401
    conn = sqlite3.connect(db_path)
    try:
        tables = ("invitation", "preferences", "contact_grant", "session", "resident", "contact")
        for table in tables:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        conn.close()


def test_persistence_across_restart(app_and_db, clients, clock):
    app, db_path = app_and_db
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    # New app instance on the same database file: state survives restart.
    app2 = create_app(db_path, clock=clock)
    resident2 = TestClient(app2, raise_server_exceptions=False)
    assert resident2.post("/demo/login", json={"identity": "resident"}).status_code == 200
    got = resident2.get("/preferences").json()
    assert got["preferences"]["preferred_days"] == [1, 2, 3, 4, 5]
    assert [i["invitation_id"] for i in resident2.get("/invitations").json()["invitations"]] == [
        inv["invitation_id"]
    ]


# ---------------------------------------------------------------- CLI
def test_cli_demo_requires_synthetic_and_loopback():
    assert cli_main(["demo"]) == 2  # --synthetic missing
    assert cli_main(["demo", "--synthetic", "--host", "0.0.0.0"]) == 2
    parser = build_parser()
    args = parser.parse_args(["demo", "--synthetic", "--host", "127.0.0.1"])
    assert args.synthetic is True and args.host == "127.0.0.1" and args.port == 8000
    assert cli_main(["doctor"]) == 0
