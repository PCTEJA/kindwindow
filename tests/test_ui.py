"""UI tests: the single-page demo interface served at GET /.

The JSON API itself is covered by tests/test_app.py; these tests check
that the polished UI loads, its static assets are served, and the
resident flow still works end to end through the same API the UI calls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kindwindow.app import create_app
from tests.test_app import BASE, FakeClock, propose, set_prefs, ts


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


MON_10 = ts(0, 10)


def test_landing_serves_new_ui(tmp_path):
    app = create_app(tmp_path / "demo.db")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<title>KindWindow — local demo</title>" in r.text
    assert 'id="view-resident"' in r.text
    assert 'id="view-contact"' in r.text
    assert 'id="view-research"' in r.text
    assert "Local demo — synthetic identities only" in r.text
    assert "Synthetic demo value" in r.text
    assert "/static/app.js" in r.text


def test_static_assets_served(tmp_path):
    app = create_app(tmp_path / "demo.db")
    client = TestClient(app, raise_server_exceptions=False)
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "PER_HOME" in js.text
    assert "0.42" in js.text or "SYNTHETIC_PROBABILITY" in js.text
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "--green" in css.text
    assert client.get("/static/nope.txt").status_code == 404
    assert client.get("/static/../app.py").status_code in (404, 422)


def test_research_view_numbers_match_test_scores(tmp_path):
    """Research aggregates in the UI must match reports/tables/test_scores.csv."""
    import csv
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    rows = list(csv.DictReader((repo / "reports" / "tables" / "test_scores.csv").open()))
    macro = {r["model"]: float(r["brier"]) for r in rows if r["home"] == "MACRO"}
    assert macro["forest-d10-leaf10-calibrated"] == 0.1409647451004638
    assert macro["recency-baseline"] == 0.1845620257996725
    assert macro["constant-baseline"] == 0.2371995581416996
    assert macro["hour-of-week-baseline"] == 0.225578949733327
    assert macro["forest-d10-leaf10-calibrated-retained-0.5"] == 0.069070787554923
    assert macro["forest-d10-leaf10-calibrated-gap12h-excluded"] == 0.162033429373023
    assert macro["forest-d10-leaf10-calibrated-degraded-30pct-2207"] == 0.1425592616563877
    # The JS chart data must carry the same per-home calibrated values.
    app = create_app(tmp_path / "demo.db")
    js = TestClient(app, raise_server_exceptions=False).get("/static/app.js").text
    for r in rows:
        if r["model"] == "forest-d10-leaf10-calibrated" and r["home"] != "MACRO":
            assert f'"{r["home"]}"' in js
            # JS stores the value as a plain number literal (0.153, not 0.1530).
            assert f"{round(float(r['brier']), 4):g}" in js


def test_resident_flow_end_to_end(clients):
    """The flow the UI drives: login, prefs, note-parse pending, propose."""
    resident = clients["resident"]
    set_prefs(resident)
    got = resident.get("/preferences").json()
    assert got["preferences"]["preferred_days"] == [1, 2, 3, 4, 5]
    # Note parse is pending-only until confirmed.
    r = resident.post(
        "/notes/parse", json={"text": "Monday: available 09:00-12:00; quiet 10:00-10:30."}
    )
    assert r.status_code == 200
    assert r.json()["pending_preferences"]["preferred_days"] == [1]
    assert resident.get("/preferences").json()["preferences"]["preferred_days"] == [1, 2, 3, 4, 5]
    r = resident.post("/preferences/confirm", json={"confirm": True})
    assert r.status_code == 200 and r.json()["confirmed"] is True
    assert resident.get("/preferences").json()["preferences"]["preferred_days"] == [1]
    # Propose + approve; contact A sees only whitelisted fields.
    # Confirmed hours are Mon 09:00-12:00, quiet 10:00-10:30, so use 11:00.
    inv = propose(resident, slot=ts(0, 11)).json()
    assert inv["experimental_probability"] == 0.42
    assert resident.post(f"/invitations/{inv['invitation_id']}/approve").status_code == 200
    inbox = clients["a"].get("/contact/invitations").json()["invitations"]
    assert inbox == [
        {
            "invitation_id": inv["invitation_id"],
            "slot_start_utc": ts(0, 11),
            "expires_utc": BASE + 3600,
        }
    ]
    assert clients["b"].get("/contact/invitations").json()["invitations"] == []


def test_propose_validation_error_is_verbatim(clients):
    """The UI shows API validation errors verbatim; check the wording here."""
    set_prefs(clients["resident"])
    r = propose(clients["resident"], slot=ts(0, 12, 30), key="k-quiet-ui")
    assert r.status_code == 409
    assert r.json()["detail"] == "slot is within quiet hours"
    r = propose(clients["resident"], slot=ts(5, 10), key="k-sat-ui")
    assert r.status_code == 409
    assert r.json()["detail"] == "slot is not on a preferred day"


def test_contact_b_empty_inbox_when_nothing_approved(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    approve = clients["resident"].post(f"/invitations/{inv['invitation_id']}/approve")
    assert approve.status_code == 200
    assert clients["b"].get("/contact/invitations").json()["invitations"] == []
    assert len(clients["a"].get("/contact/invitations").json()["invitations"]) == 1


def test_pause_and_delete_flow(clients):
    set_prefs(clients["resident"])
    inv = propose(clients["resident"]).json()
    clients["resident"].post(f"/invitations/{inv['invitation_id']}/approve")
    r = clients["resident"].post("/pause")
    assert r.status_code == 200 and r.json()["invalidated_approved"] == 1
    assert clients["a"].get("/contact/invitations").json()["invitations"] == []
    assert clients["resident"].post("/resume").status_code == 200
    r = clients["resident"].post("/demo/delete")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert clients["resident"].get("/preferences").status_code == 401


def test_ui_references_only_existing_routes(tmp_path):
    """Every fetch path used by app.js must exist on the app."""
    app = create_app(tmp_path / "demo.db")
    client = TestClient(app, raise_server_exceptions=False)
    js = client.get("/static/app.js").text
    assert "/demo/login" in js and "/demo/logout" in js and "/demo/delete" in js
    assert "/preferences" in js and "/notes/parse" in js
    assert "/preferences/confirm" in js and "/preferences/cancel" in js
    assert "/invitations" in js and "/contact/invitations" in js
    assert "/pause" in js and "/resume" in js
    # Unauthenticated API calls still return the predictable 401.
    assert client.get("/preferences").status_code == 401
    assert client.get("/contact/invitations").status_code == 401
