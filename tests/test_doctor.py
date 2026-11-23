"""PR 01 smoke test: `doctor` finds the golden fixture and valid study config."""

import json
from pathlib import Path

from kindwindow.cli import _fixture_paths, cmd_doctor
import argparse

ROOT = Path(__file__).resolve().parents[1]


def test_golden_fixture_files_exist():
    paths = _fixture_paths()
    assert paths["golden_events"].is_file()
    assert paths["golden_windows"].is_file()
    assert sum(1 for _ in paths["golden_events"].open()) == 9


def test_study_config_is_valid_json():
    data = json.loads((ROOT / "configs" / "study.json").read_text())
    assert data["protocol"] == "KindWindow proposed freeze v1"
    assert data["proposal_fixed"]["max_homes"] == 20


def test_doctor_returns_zero(capsys):
    assert cmd_doctor(argparse.Namespace()) == 0
    assert "doctor: OK" in capsys.readouterr().out
