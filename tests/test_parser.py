"""Tests for the Step 06 preference parser (``kindwindow.preferences``).

Covers the 40 dev notes (``tests/fixtures/notes_dev.jsonl``, copied from
the handoff kit's dev file -- the sealed held-out file is never read
here) plus hand-written cases: overnight intervals, AM/PM and 24h
times, quiet markers/negation, conflicting phrases -> empty, and
out-of-grammar phrases -> empty/refusal.

Per-field scoring follows the kit rule: a complete non-null normalised
value is one extraction; correct value = TP, missing required = FN,
spurious = FP, wrong non-empty value = FP + FN, both empty = neither.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kindwindow.preferences import PARSER_VERSION, parse_note

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIELDS = ("days", "preferred_range", "quiet_range")


def load_dev_notes() -> list[dict]:
    path = FIXTURES / "notes_dev.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def evaluate_dev_notes() -> dict:
    """Score parse_note on the 40 dev notes only. Returns per-field
    TP/FP/FN, precision, recall, exact field agreement, and note-level
    exact match. Never touches the held-out file."""
    rows = load_dev_notes()
    assert len(rows) == 40, f"expected 40 dev notes, got {len(rows)}"
    metrics: dict[str, dict] = {}
    exact_notes = 0
    for field in FIELDS:
        tp = fp = fn = support = 0
        exact = 0
        for row in rows:
            gold = row["gold"][field]
            got = parse_note(row["text"])[field]
            if gold is not None:
                support += 1
            if gold is None and got is None:
                exact += 1
            elif gold is not None and got == gold:
                tp += 1
                exact += 1
            elif gold is not None and got is None:
                fn += 1
            elif gold is None and got is not None:
                fp += 1
            else:  # both non-null but different: FP + FN
                fp += 1
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        metrics[field] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": support,
            "precision": precision,
            "recall": recall,
            "exact_field_agreement": f"{exact}/{len(rows)}",
        }
    for row in rows:
        if parse_note(row["text"]) == row["gold"]:
            exact_notes += 1
    metrics["note_exact_match"] = f"{exact_notes}/{len(rows)}"
    return metrics


# ---------------------------------------------------------------------------
# Dev notes
# ---------------------------------------------------------------------------


def test_dev_notes_exact_match():
    metrics = evaluate_dev_notes()
    assert metrics["note_exact_match"] == "40/40", metrics


def test_dev_notes_per_field_metrics():
    metrics = evaluate_dev_notes()
    for field in FIELDS:
        m = metrics[field]
        assert (m["tp"], m["fp"], m["fn"]) == (m["support"], 0, 0), (field, m)
        assert m["precision"] == 1.0 and m["recall"] == 1.0, (field, m)
        assert m["exact_field_agreement"] == "40/40", (field, m)


def test_dev_notes_by_family():
    rows = load_dev_notes()
    by_family: dict[str, list[str]] = {}
    for row in rows:
        got = parse_note(row["text"])
        assert got == row["gold"], (row["id"], row["text"], row["gold"], got)
        by_family.setdefault(row["family"], []).append(row["id"])
    # The 8 dev families, 5 notes each.
    assert sorted(by_family) == sorted(
        [
            "direct_preference",
            "direct_quiet",
            "group_calendar",
            "two_part_day",
            "named_list",
            "no_ring",
            "day_only",
            "time_only",
        ]
    )
    assert all(len(v) == 5 for v in by_family.values())


def test_parser_version():
    assert PARSER_VERSION == "1.0.0"


# ---------------------------------------------------------------------------
# Hand cases
# ---------------------------------------------------------------------------

HAND_CASES = [
    # (text, expected)
    # Overnight / cross-midnight.
    (
        "Thursday quiet time runs from 10 pm to 2 am.",
        {"days": [4], "preferred_range": None, "quiet_range": ["22:00", "02:00"]},
    ),
    (
        "Do not ring on weekdays between 11 pm and 6 am.",
        {"days": [1, 2, 3, 4, 5], "preferred_range": None, "quiet_range": ["23:00", "06:00"]},
    ),
    (
        "I'm available from 9 pm to midnight.",
        {"days": None, "preferred_range": ["21:00", "00:00"], "quiet_range": None},
    ),
    (
        "Don't ring Saturday from midnight to 7 am.",
        {"days": [6], "preferred_range": None, "quiet_range": ["00:00", "07:00"]},
    ),
    # AM/PM and 24h, incl. 12 am/pm and shared marker.
    (
        "Call me Monday from 12 am to 2 am.",
        {"days": [1], "preferred_range": ["00:00", "02:00"], "quiet_range": None},
    ),
    (
        "Call me Monday from 12 pm to 2 pm.",
        {"days": [1], "preferred_range": ["12:00", "14:00"], "quiet_range": None},
    ),
    (
        "I am available from 9 to 11 am.",
        {"days": None, "preferred_range": ["09:00", "11:00"], "quiet_range": None},
    ),
    (
        "Call me Monday from 9am to 11am.",
        {"days": [1], "preferred_range": ["09:00", "11:00"], "quiet_range": None},
    ),
    (
        "CALL ME ON MONDAY BETWEEN 9 AM AND 11 AM.",
        {"days": [1], "preferred_range": ["09:00", "11:00"], "quiet_range": None},
    ),
    # Quiet markers / negation.
    (
        "Please don't ring Monday between 10:00 and 11:00.",
        {"days": [1], "preferred_range": None, "quiet_range": ["10:00", "11:00"]},
    ),
    (
        "Do not ring Friday from 9:30 pm to midnight.",
        {"days": [5], "preferred_range": None, "quiet_range": ["21:30", "00:00"]},
    ),
    (
        "Keep quiet Tuesdays from 8 am to 8:45 am.",
        {"days": [2], "preferred_range": None, "quiet_range": ["08:00", "08:45"]},
    ),
    (
        "Monday 9 am to 11 am is quiet time.",
        {"days": [1], "preferred_range": None, "quiet_range": ["09:00", "11:00"]},
    ),
    # Two intervals, one of each kind, incl. overlap and comma clauses.
    (
        "Monday: available 09:00-12:00; quiet 10:00-10:30.",
        {"days": [1], "preferred_range": ["09:00", "12:00"], "quiet_range": ["10:00", "10:30"]},
    ),
    (
        "Available Monday 9 am to 11 am, quiet 10 am to 10:30 am.",
        {"days": [1], "preferred_range": ["09:00", "11:00"], "quiet_range": ["10:00", "10:30"]},
    ),
    (
        "Quiet time Monday 9 am to 11 am, available 2 pm to 4 pm.",
        {"days": [1], "preferred_range": ["14:00", "16:00"], "quiet_range": ["09:00", "11:00"]},
    ),
    # Day range / groups.
    (
        "Call me Mon-Fri from 9 am to 5 pm.",
        {"days": [1, 2, 3, 4, 5], "preferred_range": ["09:00", "17:00"], "quiet_range": None},
    ),
    (
        "Call me Monday to Friday from 9 am to 5 pm.",
        {"days": [1, 2, 3, 4, 5], "preferred_range": ["09:00", "17:00"], "quiet_range": None},
    ),
    # Unicode: curly apostrophe, en dash.
    (
        "Please don\u2019t ring Tuesday between 1 pm and 3 pm.",
        {"days": [2], "preferred_range": None, "quiet_range": ["13:00", "15:00"]},
    ),
    (
        "Call me Monday 09:00\u201312:00.",
        {"days": [1], "preferred_range": ["09:00", "12:00"], "quiet_range": None},
    ),
    # Empty / no information.
    ("", {"days": None, "preferred_range": None, "quiet_range": None}),
    ("Hello.", {"days": None, "preferred_range": None, "quiet_range": None}),
]


@pytest.mark.parametrize("text,expected", HAND_CASES)
def test_hand_cases(text, expected):
    assert parse_note(text) == expected


CONFLICT_AND_OUT_OF_GRAMMAR = [
    # Conflicting: two day selectors / two preferred intervals / identical
    # preferred and quiet intervals -> the affected field(s) are empty.
    (
        "Call me Friday from 9 am to 11 am and also Saturday from 2 pm to 4 pm.",
        {"days": None, "preferred_range": None, "quiet_range": None},
    ),
    (
        "Call me Monday from 9 am to 11 am and from 2 pm to 4 pm.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
    (
        "Call me Monday or Tuesday from 9 am to 11 am.",
        {"days": None, "preferred_range": ["09:00", "11:00"], "quiet_range": None},
    ),
    (
        "Monday: available 09:00-12:00; quiet 09:00-12:00.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
    # Out of grammar: bare hours, vague times, single times, hedges,
    # except / every-other qualifiers, invalid times, stray times.
    ("Call me Monday from 9 to 11.", {"days": [1], "preferred_range": None, "quiet_range": None}),
    ("Call me in the morning.", {"days": None, "preferred_range": None, "quiet_range": None}),
    ("Call me at 9 am.", {"days": None, "preferred_range": None, "quiet_range": None}),
    (
        "Call me Monday from around 9 am to 11 am.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
    (
        "Weekdays except Friday work from 9 am to noon.",
        {"days": None, "preferred_range": ["09:00", "12:00"], "quiet_range": None},
    ),
    (
        "Call me every other Monday from 9 am to 11 am.",
        {"days": None, "preferred_range": ["09:00", "11:00"], "quiet_range": None},
    ),
    (
        "Call me Monday from 25:00 to 26:00.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
    (
        "Call me Monday from 9 am to 9 am.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
    (
        "Call me on Monday between 9 am and 11 am, and also at 2 pm.",
        {"days": [1], "preferred_range": None, "quiet_range": None},
    ),
]


@pytest.mark.parametrize("text,expected", CONFLICT_AND_OUT_OF_GRAMMAR)
def test_conflicting_and_out_of_grammar_refuse(text, expected):
    assert parse_note(text) == expected
