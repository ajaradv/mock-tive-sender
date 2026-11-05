import json
import random
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

SCHEMA_PATH = Path(__file__).parent / "schemas" / "tive-incoming-schema.json"

DEFAULT_START_MS = 1739215646000  # 2025-02-10T19:27:26Z, fixed so a seed is reproducible
DEFAULT_INTERVAL_MS = 300_000


class GeneratorError(Exception):
    """A generated payload failed the vendored Tive schema (a generator bug)."""


@lru_cache(maxsize=1)
def _validator() -> Draft7Validator:
    return Draft7Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate_payload(payload: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
        raise GeneratorError(detail)


def _imei(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(15))


def _make_devices(rng: random.Random, device_count: int) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in range(device_count):
        imei = _imei(rng)
        while imei in seen:
            imei = _imei(rng)
        seen.add(imei)
        devices.append(
            {
                "imei": imei,
                "name": f"G{rng.randint(0, 999999):06d}",
                "shipment": f"SHP-{rng.randint(10000, 99999)}",
                "lat": rng.uniform(25.0, 47.0),
                "lon": rng.uniform(-122.0, -71.0),
                "account": 478,
                "n": n,
            }
        )
    return devices


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reading(rng: random.Random, dev: dict[str, Any], ts_ms: int) -> dict[str, Any]:
    celsius = round(rng.uniform(2.0, 8.0), 4)
    probe = round(celsius + rng.uniform(-0.5, 0.5), 4)
    x, y, z = (round(rng.uniform(-1, 1), 4) for _ in range(3))
    dev["lat"] = max(-89.0, min(89.0, dev["lat"] + rng.uniform(-0.01, 0.01)))
    dev["lon"] = max(-179.0, min(179.0, dev["lon"] + rng.uniform(-0.01, 0.01)))
    method = rng.choice(["gps", "wifi", "cell"])
    meters_by_method = {
        "gps": rng.randint(3, 40),
        "wifi": rng.randint(10, 120),
        "cell": rng.randint(300, 2000),
    }
    meters = meters_by_method[method]
    return {
        "EntityName": dev["name"],
        "EntryTimeEpoch": ts_ms,
        "EntryTimeUtc": _utc(ts_ms),
        "Cellular": {
            "SignalStrength": rng.choice(["Poor", "Fair", "Good"]),
            "Dbm": rng.randint(-120, -60),
        },
        "Temperature": {"Celsius": celsius, "Fahrenheit": round(celsius * 9 / 5 + 32, 4)},
        "ProbeTemperature": {"Celsius": probe, "Fahrenheit": round(probe * 9 / 5 + 32, 4)},
        "Humidity": {"Percentage": round(rng.uniform(20, 80), 2)},
        "Accelerometer": {
            "G": round((x * x + y * y + z * z) ** 0.5, 6),
            "X": x,
            "Y": y,
            "Z": z,
        },
        "Light": {"Lux": round(rng.uniform(0, 5), 2)},
        "Battery": {"Percentage": rng.randint(20, 100), "Estimation": "Days", "IsCharging": False},
        "Shipment": {
            "Id": dev["shipment"],
            "Description": None,
            "DeviceId": dev["imei"],
            "Carrier": "MOCKCARRIER",
        },
        "AccountId": dev["account"],
        "DeviceId": dev["imei"],
        "DeviceName": dev["name"],
        "ShipmentId": dev["shipment"],
        "PublicShipmentId": f"{rng.randint(0, 36**10 - 1):010X}"[:10],
        "Location": {
            "Latitude": round(dev["lat"], 6),
            "Longitude": round(dev["lon"], 6),
            "FormattedAddress": None,
            "LocationMethod": method,
            "Accuracy": {"Meters": meters, "Kilometers": meters / 1000},
            "GeolocationSourceName": "mock",
            "CellTowerUsedCount": 1,
            "WifiAccessPointUsedCount": rng.randint(1, 8) if method == "wifi" else 0,
        },
    }


def generate(
    count: int,
    device_count: int,
    seed: int,
    start_ms: int = DEFAULT_START_MS,
    interval_ms: int = DEFAULT_INTERVAL_MS,
) -> list[dict[str, Any]]:
    """Generate `count` valid Tive payloads spread round-robin over `device_count` devices.

    The same arguments always yield the same payloads. Each device's timestamps
    increase by `interval_ms` per reading.
    """
    if count < 0 or device_count < 1:
        raise ValueError("count must be >= 0 and device_count >= 1")
    rng = random.Random(seed)
    devices = _make_devices(rng, device_count)
    sent = [0] * device_count
    out: list[dict[str, Any]] = []
    for i in range(count):
        d = i % device_count
        ts = start_ms + sent[d] * interval_ms
        sent[d] += 1
        payload = _reading(rng, devices[d], ts)
        validate_payload(payload)
        out.append(payload)
    return out
