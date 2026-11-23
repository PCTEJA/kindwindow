"""Smoke tests for the hosted static demo site in web/.

The site is browser-only (no backend); these tests check the files
exist, carry the required views/banner/copy, and that the in-browser
store reproduces the API's validation messages. The interactive flows
themselves are exercised by tests/test_ui.py against the real app.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"

EXPECTED_MESSAGES = [
    "quiet hours cover all preferred hours; no usable slot",
    "no preferred hours set; set preferred hours first",
    "slot is not on a preferred day",
    "slot is outside preferred hours",
    "slot is within quiet hours",
    "slot must be in the future",
    "resident is paused",
    "no pending text-derived changes to confirm",
    "invitation cannot be approved in its current state",
]


def test_web_files_exist():
    for name in ("index.html", "app.js", "styles.css", "vercel.json", "README.md"):
        assert (WEB / name).is_file(), name


def test_web_index_has_three_views_and_hosted_banner():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<title>KindWindow — hosted demo</title>" in html
    assert 'id="view-resident"' in html
    assert 'id="view-contact"' in html
    assert 'id="view-research"' in html
    assert "Hosted demo — runs entirely in your browser." in html
    assert "No real people are contacted, no data leaves your device." in html
    assert "State lives in memory only" in html
    assert "Synthetic demo value" in html
    # Relative asset paths so the site works under any static host.
    assert 'href="styles.css"' in html
    assert 'src="app.js"' in html
    assert "fetch(" not in html
    # Verified research aggregates, copied from the in-repo UI.
    assert "0.141" in html
    assert "100,812" in html
    assert "176,506" in html
    assert "228.1 s" in html


def test_web_app_js_mirrors_api_rules():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    for message in EXPECTED_MESSAGES:
        assert message in js, message
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js
    assert "SYNTHETIC_PROBABILITY = 0.42" in js
    # Per-home Brier values from reports/tables/test_scores.csv.
    assert 'home: "hh119", n: 18472, forest: 0.0932, recency: 0.0928' in js
    assert 'home: "aruba", n: 2062, forest: 0.1301, recency: 0.196' in js


def test_web_styles_match_in_repo_ui():
    assert (WEB / "styles.css").read_text(encoding="utf-8") == (
        REPO / "src/kindwindow/app/static/styles.css"
    ).read_text(encoding="utf-8")


def test_vercel_json_parses_and_is_static():
    config = json.loads((WEB / "vercel.json").read_text(encoding="utf-8"))
    assert config["framework"] is None
    assert "buildCommand" not in config
