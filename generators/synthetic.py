"""Train-only synthetic event generator (Step 10).

Emits immutable normalized-events JSONL bytes — one record per line with
fields ``home, member, row, time, sensor, type, message`` and ``time`` in
``YYYY-MM-DDTHH:MM:SS`` — identical for both engines. Deterministic:
same seed + same params -> byte-identical output (same SHA-256).

Model (fitted ONLY from aggregate summaries, never from raw rows):
unequal home probabilities, hourly event-rate weights, conditional
sensor/message mix, and burst / interarrival gaps. Generation uses only
``random.Random(seed)`` from the standard library, so no third-party RNG
version can change the bytes.

The default params file (``generators/params_dev.json``) is fitted ONLY
from the golden fixture (9 events, 1 home) plus study-config aggregates.
It is a development placeholder: the real train-fit params, fitted from
train-only aggregate summaries in Step 12, will replace it. Do NOT treat
dev params as representative of real homes.

Time validity: per home, event times are strictly non-decreasing in
generation order (gaps are >= 0; a zero gap is a legitimate simultaneous
event, as in the fixtures) and the merged file is globally sorted by
time, so the engines' ordering assumptions hold.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

PARAMS_SCHEMA = "kindwindow.synthetic-params/v1"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Hours with zero fitted weight still need a finite stretch factor; the
# floor below is applied only inside the gap-stretch computation and is
# recorded in the params file so a re-fit can change it deliberately.
DEFAULT_HOUR_WEIGHT_FLOOR = 0.005


def sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def load_params(path: str | Path) -> dict:
    """Load and validate a generator params JSON file."""
    params = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_params(params)
    return params


def validate_params(params: dict) -> None:
    """Validate a params dict; raises ``ValueError`` on any defect."""
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    if params.get("schema") != PARAMS_SCHEMA:
        raise ValueError(f"params schema must be {PARAMS_SCHEMA!r}")
    homes = params.get("homes")
    if not isinstance(homes, list) or not homes:
        raise ValueError("params.homes must be a non-empty list")
    total = 0.0
    seen_homes: set[str] = set()
    for entry in homes:
        if not isinstance(entry, dict) or not isinstance(entry.get("home"), str):
            raise ValueError("each params.homes entry needs a string 'home'")
        if entry["home"] in seen_homes:
            raise ValueError(f"duplicate home in params.homes: {entry['home']!r}")
        seen_homes.add(entry["home"])
        p = entry.get("probability")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or p < 0:
            raise ValueError(f"home {entry['home']!r} has an invalid probability: {p!r}")
        total += float(p)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"params.homes probabilities must sum to 1, got {total!r}")
    weights = params.get("hourly_weights")
    if not isinstance(weights, list) or len(weights) != 24:
        raise ValueError("params.hourly_weights must be a list of 24 numbers")
    if any(not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0 for w in weights):
        raise ValueError("params.hourly_weights must be non-negative numbers")
    if sum(weights) <= 0:
        raise ValueError("params.hourly_weights must sum to a positive value")
    type_probs = params.get("type_probs")
    if not isinstance(type_probs, dict) or set(type_probs) != {"motion", "door"}:
        raise ValueError("params.type_probs must map exactly {'motion', 'door'} to probabilities")
    if abs(sum(float(v) for v in type_probs.values()) - 1.0) > 1e-6:
        raise ValueError("params.type_probs must sum to 1")
    message_probs = params.get("message_probs")
    if not isinstance(message_probs, dict):
        raise ValueError("params.message_probs must be an object")
    expected_messages = {"motion": {"ON", "OFF"}, "door": {"OPEN", "CLOSE"}}
    for sensor_type, messages in expected_messages.items():
        probs = message_probs.get(sensor_type)
        if not isinstance(probs, dict) or set(probs) != messages:
            raise ValueError(f"params.message_probs[{sensor_type!r}] must map {sorted(messages)}")
        if abs(sum(float(v) for v in probs.values()) - 1.0) > 1e-6:
            raise ValueError(f"params.message_probs[{sensor_type!r}] must sum to 1")
    sensors = params.get("sensors")
    if not isinstance(sensors, dict):
        raise ValueError("params.sensors must be an object")
    for sensor_type in ("motion", "door"):
        ids = sensors.get(sensor_type)
        if not isinstance(ids, list) or not ids or not all(isinstance(s, str) for s in ids):
            raise ValueError(f"params.sensors[{sensor_type!r}] must be a non-empty string list")
    inter = params.get("interarrival_seconds")
    if not isinstance(inter, dict):
        raise ValueError("params.interarrival_seconds must be an object")
    if inter.get("distribution") != "exponential":
        raise ValueError("params.interarrival_seconds.distribution must be 'exponential'")
    mean = inter.get("mean_seconds")
    if not isinstance(mean, (int, float)) or isinstance(mean, bool) or mean <= 0:
        raise ValueError("params.interarrival_seconds.mean_seconds must be positive")
    burst = params.get("burst")
    if not isinstance(burst, dict):
        raise ValueError("params.burst must be an object")
    burst_p = burst.get("probability")
    if not isinstance(burst_p, (int, float)) or isinstance(burst_p, bool):
        raise ValueError("params.burst.probability must be a number")
    if not 0.0 <= float(burst_p) <= 1.0:
        raise ValueError("params.burst.probability must be in [0, 1]")
    gap = burst.get("gap_seconds")
    if (
        not isinstance(gap, list)
        or len(gap) != 2
        or not all(isinstance(g, (int, float)) and not isinstance(g, bool) for g in gap)
        or gap[0] < 0
        or gap[1] < gap[0]
    ):
        raise ValueError("params.burst.gap_seconds must be [min, max] with 0 <= min <= max")
    start = params.get("start")
    if not isinstance(start, str):
        raise ValueError("params.start must be a 'YYYY-MM-DDTHH:MM:SS' string")
    datetime.strptime(start, TIME_FORMAT)  # raises ValueError on a bad format
    if not isinstance(params.get("member"), str) or not params["member"]:
        raise ValueError("params.member must be a non-empty string")


def _hour_weight(hour: int, weights: list[float], floor: float) -> float:
    """Hourly weight with a floor so zero-weight hours stay reachable."""
    return max(float(weights[hour]), float(floor))


def _pick(rng: random.Random, probs: dict[str, float]) -> str:
    """Deterministic categorical draw from an insertion-ordered dict."""
    roll = rng.random()
    cumulative = 0.0
    items = list(probs.items())
    for key, prob in items[:-1]:
        cumulative += float(prob)
        if roll < cumulative:
            return key
    return items[-1][0]


def _derive_seed(seed: int, home: str) -> int:
    """Deterministic per-home seed: SHA-256 of ``seed|home`` as an int.

    (``random.Random`` seeding from a string would use ``hash()``, which
    is salted per process -- never do that here.)
    """
    digest = hashlib.sha256(f"{seed}|{home}".encode("utf-8")).hexdigest()
    return int(digest, 16)


def _home_counts(
    rng: random.Random, homes: list[str], probs: dict[str, float], n: int
) -> dict[str, int]:
    """Multinomial home assignment for ``n`` events (master RNG stream)."""
    counts: dict[str, int] = {home: 0 for home in homes}
    cumulative = []
    running = 0.0
    for home in homes:
        running += probs[home]
        cumulative.append((running, home))
    for _ in range(n):
        roll = rng.random()
        for threshold, home in cumulative:
            if roll < threshold:
                counts[home] += 1
                break
        else:  # floating-point edge: roll == 1.0 is impossible, but be safe
            counts[homes[-1]] += 1
    return counts


def _home_stream(params: dict, home: str, k: int, seed: int):
    """Yield ``k`` records for one home, in non-decreasing time order.

    Uses a per-home RNG stream derived from (seed, home), so homes can
    be generated independently and merged in global time order without
    holding all events in memory. The model is unchanged: first event
    within the first hour after ``start``; later gaps are exponential
    interarrivals, stretched when leaving a low-rate hour
    (gap *= mean_weight / weight(hour)) and replaced by a short uniform
    burst gap with probability ``burst_p``.
    """
    rng = random.Random(_derive_seed(seed, home))
    weights = [float(w) for w in params["hourly_weights"]]
    mean_weight = sum(weights) / 24.0
    floor = float(params.get("hour_weight_floor", DEFAULT_HOUR_WEIGHT_FLOOR))
    mean_gap = float(params["interarrival_seconds"]["mean_seconds"])
    burst_p = float(params["burst"]["probability"])
    burst_lo, burst_hi = (float(g) for g in params["burst"]["gap_seconds"])
    start = datetime.strptime(params["start"], TIME_FORMAT)
    member = params["member"]
    t = start + timedelta(seconds=rng.uniform(0, 3600))
    for _ in range(k):
        sensor_type = _pick(rng, params["type_probs"])
        message = _pick(rng, params["message_probs"][sensor_type])
        sensor = rng.choice(params["sensors"][sensor_type])
        yield {
            "home": home,
            "member": member,
            "row": 0,  # renumbered after the global time sort
            "time": t.strftime(TIME_FORMAT),
            "sensor": sensor,
            "type": sensor_type,
            "message": message,
        }
        if rng.random() < burst_p:
            gap = rng.uniform(burst_lo, burst_hi)
        else:
            gap = rng.expovariate(1.0 / mean_gap)
            gap *= mean_weight / _hour_weight(t.hour, weights, floor)
        t = t + timedelta(seconds=gap)


def _merged_records(params: dict, n: int, seed: int):
    """All ``n`` records in global time order, streamed via heap merge.

    Each home's stream is already time-ordered, so ``heapq.merge`` with
    ``key=time`` yields the global order without materialising the
    whole file. Ties keep per-home generation order (stable merge over
    homes in sorted order). Row numbers are assigned here so
    ``(member, row)`` is unique per file.
    """
    import heapq

    validate_params(params)
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError(f"n must be a non-negative int, got {n!r}")
    if n == 0:
        return
    homes = sorted(entry["home"] for entry in params["homes"])
    home_prob = {entry["home"]: float(entry["probability"]) for entry in params["homes"]}
    counts = _home_counts(random.Random(seed), homes, home_prob, n)
    streams = [_home_stream(params, home, counts[home], seed) for home in homes if counts[home] > 0]
    row = 0
    for record in heapq.merge(*streams, key=lambda r: r["time"]):
        row += 1
        record["row"] = row
        yield record


def generate_bytes(params: dict, n: int, seed: int) -> bytes:
    """Generate exactly ``n`` normalized events as JSONL bytes.

    Deterministic in (params, n, seed): the same inputs always produce
    the same bytes. ``n`` may be 0 (returns ``b""``). Event rows are
    numbered 1..n in global time order; ``member`` comes from params.
    Holds the output in memory: fine for tests and small sizes; use
    :func:`generate_file` for 1M/10M workloads.
    """
    lines = [json.dumps(record) for record in _merged_records(params, n, seed)]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def generate_file(params: dict, n: int, seed: int, path: str | Path) -> dict:
    """Stream generated JSONL to ``path`` (bounded memory); returns metadata.

    Writes in 100k-line chunks and hashes incrementally, so 10M-event
    workloads never hold the events or the output bytes in memory.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total_bytes = 0
    chunk: list[str] = []
    with path.open("w", encoding="utf-8") as fh:
        for record in _merged_records(params, n, seed):
            chunk.append(json.dumps(record))
            if len(chunk) >= 100_000:
                data = ("\n".join(chunk) + "\n").encode("utf-8")
                fh.write(data.decode("utf-8"))
                digest.update(data)
                total_bytes += len(data)
                chunk = []
        if chunk:
            data = ("\n".join(chunk) + "\n").encode("utf-8")
            fh.write(data.decode("utf-8"))
            digest.update(data)
            total_bytes += len(data)
    return {"path": str(path), "n_events": n, "bytes": total_bytes, "sha256": digest.hexdigest()}


def fit_params_from_summary_csv(
    path: str | Path,
    *,
    start: str = "2030-02-01T08:00:00",
    member: str = "synthetic.jsonl",
    provenance: str = "train-only aggregate summary CSV (not raw data)",
) -> dict:
    """Fit generator params from a train-only aggregate summary CSV.

    This helper NEVER reads raw events. The CSV must have columns
    ``statistic,key,value`` with one row per aggregate:

    * ``home_events`` — key: home id, value: event count for that home.
    * ``hour_events`` — key: hour ``0``..``23``, value: event count.
    * ``type_events`` — key: ``motion`` / ``door``, value: event count.
    * ``message_events`` — key: ``motion:ON`` etc., value: event count.
    * ``mean_interarrival_seconds`` — key: ``-`` (ignored), value: mean gap.
    * ``burst_fraction`` — key: ``-``, value: fraction of gaps <= burst max.
    * ``burst_gap_max_seconds`` — key: ``-``, value: burst-gap upper bound.
    * ``sensors`` — key: ``motion:M1`` etc., value: ignored (id listed).

    Missing optional statistics fall back to dev defaults recorded in the
    returned params (``fit_defaults_used`` lists them). The result is a
    params dict in the same schema as ``params_dev.json``; callers should
    record the summary file's SHA-256 alongside it. Do NOT run this on
    real data now: Step 12 will run it on the train-only summary.
    """
    home_counts: dict[str, float] = {}
    hour_counts = [0.0] * 24
    type_counts: dict[str, float] = {}
    message_counts: dict[str, dict[str, float]] = {"motion": {}, "door": {}}
    sensors: dict[str, list[str]] = {"motion": [], "door": []}
    mean_gap: float | None = None
    burst_fraction: float | None = None
    burst_max: float | None = None
    defaults_used: list[str] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not {"statistic", "key", "value"} <= set(reader.fieldnames):
            raise ValueError("summary CSV must have columns statistic,key,value")
        for row in reader:
            statistic = (row.get("statistic") or "").strip()
            key = (row.get("key") or "").strip()
            raw_value = (row.get("value") or "").strip()
            if statistic == "sensors":
                sensor_type, _, sensor_id = key.partition(":")
                if sensor_type in sensors and sensor_id and sensor_id not in sensors[sensor_type]:
                    sensors[sensor_type].append(sensor_id)
                continue
            try:
                value = float(raw_value)
            except ValueError:
                raise ValueError(f"summary row has a non-numeric value: {row!r}") from None
            if statistic == "home_events":
                home_counts[key] = home_counts.get(key, 0.0) + value
            elif statistic == "hour_events":
                hour = int(key)
                if not 0 <= hour <= 23:
                    raise ValueError(f"hour_events key must be 0..23, got {key!r}")
                hour_counts[hour] += value
            elif statistic == "type_events":
                type_counts[key] = type_counts.get(key, 0.0) + value
            elif statistic == "message_events":
                sensor_type, _, message = key.partition(":")
                if sensor_type not in message_counts:
                    raise ValueError(f"message_events key has unknown type: {key!r}")
                message_counts[sensor_type][message] = (
                    message_counts[sensor_type].get(message, 0.0) + value
                )
            elif statistic == "mean_interarrival_seconds":
                mean_gap = value
            elif statistic == "burst_fraction":
                burst_fraction = value
            elif statistic == "burst_gap_max_seconds":
                burst_max = value
            else:
                raise ValueError(f"unknown summary statistic: {statistic!r}")
    if not home_counts:
        raise ValueError("summary CSV has no home_events rows")
    if sum(hour_counts) <= 0:
        raise ValueError("summary CSV has no hour_events rows")
    if set(type_counts) != {"motion", "door"}:
        raise ValueError("summary CSV type_events must cover exactly motion and door")
    for sensor_type, expected in (("motion", {"ON", "OFF"}), ("door", {"OPEN", "CLOSE"})):
        if set(message_counts[sensor_type]) != expected:
            raise ValueError(
                f"summary CSV message_events for {sensor_type} must cover {sorted(expected)}"
            )
    total_homes = sum(home_counts.values())
    total_types = sum(type_counts.values())
    params: dict = {
        "schema": PARAMS_SCHEMA,
        "provenance": provenance,
        "source_summary": str(path),
        "homes": [
            {"home": home, "probability": count / total_homes}
            for home, count in sorted(home_counts.items())
        ],
        "hourly_weights": [c / sum(hour_counts) for c in hour_counts],
        "type_probs": {t: c / total_types for t, c in sorted(type_counts.items())},
        "message_probs": {
            sensor_type: {
                message: count / sum(counts.values()) for message, count in sorted(counts.items())
            }
            for sensor_type, counts in sorted(message_counts.items())
        },
        "sensors": {
            sensor_type: sorted(ids) if ids else (["M1"] if sensor_type == "motion" else ["D1"])
            for sensor_type, ids in sorted(sensors.items())
        },
        "interarrival_seconds": {
            "distribution": "exponential",
            "mean_seconds": mean_gap if mean_gap is not None else 1575.0,
        },
        "burst": {
            "probability": burst_fraction if burst_fraction is not None else 0.375,
            "gap_seconds": [0, burst_max if burst_max is not None else 600],
        },
        "hour_weight_floor": DEFAULT_HOUR_WEIGHT_FLOOR,
        "start": start,
        "member": member,
    }
    if mean_gap is None:
        defaults_used.append("interarrival_seconds.mean_seconds=1575.0 (dev default)")
    if burst_fraction is None:
        defaults_used.append("burst.probability=0.375 (dev default)")
    if burst_max is None:
        defaults_used.append("burst.gap_seconds max=600 (dev default)")
    params["fit_defaults_used"] = defaults_used
    validate_params(params)
    return params


def summarize_bytes(data: bytes) -> dict:
    """Aggregate summary of generated JSONL bytes (for run records).

    Returns counts only (homes, hours, types, messages, n, bytes, sha256)
    — no raw rows — so it is safe to store in attempt records.
    """
    import collections

    homes: collections.Counter = collections.Counter()
    hours: collections.Counter = collections.Counter()
    types: collections.Counter = collections.Counter()
    messages: collections.Counter = collections.Counter()
    n = 0
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        n += 1
        homes[record["home"]] += 1
        hours[record["time"][11:13]] += 1
        types[record["type"]] += 1
        messages[f"{record['type']}:{record['message']}"] += 1
    return {
        "n_events": n,
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "homes": dict(sorted(homes.items())),
        "hours": dict(sorted(hours.items())),
        "types": dict(sorted(types.items())),
        "messages": dict(sorted(messages.items())),
    }


def main(argv: list[str] | None = None) -> int:
    """Tiny CLI for generation: ``python -m generators.synthetic ...``."""
    import argparse

    parser = argparse.ArgumentParser(prog="generators.synthetic")
    parser.add_argument("--params", required=True, help="Params JSON file")
    parser.add_argument("--n", type=int, required=True, help="Number of events")
    parser.add_argument("--seed", type=int, default=941, help="Seed (default 941)")
    parser.add_argument("--out", required=True, help="Output JSONL path")
    args = parser.parse_args(argv)
    params = load_params(args.params)
    info = generate_file(params, args.n, args.seed, args.out)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
