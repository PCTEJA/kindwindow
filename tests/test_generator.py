"""Step 10: synthetic generator tests (fixture/synthetic only).

Determinism, size handling, field/time validity, params validation,
and the train-only summary-CSV fitting helper. No real data is read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from generators.synthetic import (
    fit_params_from_summary_csv,
    generate_bytes,
    generate_file,
    load_params,
    sha256_hex,
    summarize_bytes,
    validate_params,
)

ROOT = Path(__file__).resolve().parents[1]
PARAMS_DEV = ROOT / "generators" / "params_dev.json"


@pytest.fixture(scope="module")
def params():
    return load_params(PARAMS_DEV)


def _records(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def test_dev_params_load_and_validate(params):
    assert params["schema"] == "kindwindow.synthetic-params/v1"
    assert params["homes"] == [{"home": "fixture-A", "probability": 1.0}]
    assert abs(sum(params["hourly_weights"]) - 1.0) < 1e-9
    validate_params(params)


def test_same_seed_same_sha256(params):
    data_a = generate_bytes(params, 100, 941)
    data_b = generate_bytes(params, 100, 941)
    assert data_a == data_b
    assert sha256_hex(data_a) == sha256_hex(data_b)
    assert sha256_hex(data_a) == "365acb6501172dd7b2456e8fc50bc69b3481646d7db27d35631e31404e7ac38b"


def test_different_seed_different_bytes(params):
    assert generate_bytes(params, 100, 941) != generate_bytes(params, 100, 942)
    assert sha256_hex(generate_bytes(params, 100, 941)) != sha256_hex(
        generate_bytes(params, 100, 942)
    )


def test_tiny_sizes_and_zero(params):
    assert generate_bytes(params, 0, 941) == b""
    assert len(_records(generate_bytes(params, 1, 941))) == 1
    assert len(_records(generate_bytes(params, 10, 941))) == 10
    assert len(_records(generate_bytes(params, 1000, 941))) == 1000


def test_record_fields_and_time_format(params):
    records = _records(generate_bytes(params, 50, 941))
    for i, record in enumerate(records, start=1):
        assert set(record) == {"home", "member", "row", "time", "sensor", "type", "message"}
        assert record["row"] == i
        assert record["member"] == "synthetic.jsonl"
        assert len(record["time"]) == 19 and record["time"][10] == "T"
        assert record["type"] in ("motion", "door")
        if record["type"] == "motion":
            assert record["message"] in ("ON", "OFF")
        else:
            assert record["message"] in ("OPEN", "CLOSE")
    times = [r["time"] for r in records]
    assert times == sorted(times)  # globally time-ordered file


def test_generate_file_reports_sha256(tmp_path, params):
    out = tmp_path / "synth.jsonl"
    info = generate_file(params, 25, 941, out)
    assert info["n_events"] == 25
    assert info["bytes"] == out.stat().st_size
    assert info["sha256"] == sha256_hex(out.read_bytes())
    assert summarize_bytes(out.read_bytes())["n_events"] == 25


def test_generate_file_matches_generate_bytes(tmp_path, params):
    """Streaming file writer produces the same bytes as the in-memory path."""
    out = tmp_path / "synth.jsonl"
    info = generate_file(params, 500, 941, out)
    assert out.read_bytes() == generate_bytes(params, 500, 941)
    assert info["sha256"] == sha256_hex(generate_bytes(params, 500, 941))


def test_invalid_params_rejected(params):
    bad = dict(params, homes=[{"home": "a", "probability": 0.5}])
    with pytest.raises(ValueError, match="sum to 1"):
        validate_params(bad)
    bad = dict(params, hourly_weights=[0.0] * 24)
    with pytest.raises(ValueError):
        validate_params(bad)
    bad = dict(params, start="not-a-time")
    with pytest.raises(ValueError):
        validate_params(bad)


def test_fit_params_from_summary_csv(tmp_path):
    summary = tmp_path / "summary.csv"
    summary.write_text(
        "statistic,key,value\n"
        "home_events,home-A,60\n"
        "home_events,home-B,40\n"
        "hour_events,8,10\n"
        "hour_events,9,30\n"
        "hour_events,10,60\n"
        "type_events,motion,70\n"
        "type_events,door,30\n"
        "message_events,motion:ON,35\n"
        "message_events,motion:OFF,35\n"
        "message_events,door:OPEN,20\n"
        "message_events,door:CLOSE,10\n"
        "mean_interarrival_seconds,-,900\n"
        "burst_fraction,-,0.2\n"
        "burst_gap_max_seconds,-,300\n"
        "sensors,motion:M1,\n"
        "sensors,motion:M2,\n"
        "sensors,door:D1,\n",
        encoding="utf-8",
    )
    fitted = fit_params_from_summary_csv(summary, provenance="test summary")
    validate_params(fitted)
    assert fitted["homes"] == [
        {"home": "home-A", "probability": 0.6},
        {"home": "home-B", "probability": 0.4},
    ]
    assert fitted["hourly_weights"][10] == pytest.approx(0.6)
    assert fitted["type_probs"] == {"door": 0.3, "motion": 0.7}
    assert fitted["message_probs"]["door"] == {
        "CLOSE": pytest.approx(1 / 3),
        "OPEN": pytest.approx(2 / 3),
    }
    assert fitted["interarrival_seconds"]["mean_seconds"] == 900
    assert fitted["burst"] == {"probability": 0.2, "gap_seconds": [0, 300]}
    assert fitted["sensors"] == {"door": ["D1"], "motion": ["M1", "M2"]}
    assert fitted["fit_defaults_used"] == []
    # Fitted params generate deterministically too.
    data = generate_bytes(fitted, 200, 941)
    assert len(_records(data)) == 200
    assert sha256_hex(data) == sha256_hex(generate_bytes(fitted, 200, 941))


def test_fit_params_rejects_raw_style_and_bad_csv(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("home,time,message\nfixture-A,2030-01-01T00:00:00,ON\n", encoding="utf-8")
    with pytest.raises(ValueError, match="statistic,key,value"):
        fit_params_from_summary_csv(bad)
    empty = tmp_path / "empty.csv"
    empty.write_text("statistic,key,value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no home_events"):
        fit_params_from_summary_csv(empty)
