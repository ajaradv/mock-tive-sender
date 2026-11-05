import copy
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Payload = dict[str, Any]

FAULT_VALUE = 327.67


def out_of_range_latitude(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    q["Location"]["Latitude"] = 95.0
    return q


def out_of_range_longitude(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    q["Location"]["Longitude"] = -200.0
    return q


def drop_device_id(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    q.pop("DeviceId", None)
    return q


def seconds_timestamp(p: Payload) -> Payload:
    """EntryTimeEpoch in seconds instead of milliseconds (EntryTimeUtc left alone)."""
    q = copy.deepcopy(p)
    q["EntryTimeEpoch"] = q["EntryTimeEpoch"] // 1000
    return q


def stringify_numbers(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    temp = q["Temperature"]
    for key in ("Celsius", "Fahrenheit"):
        if isinstance(temp.get(key), int | float):
            temp[key] = str(temp[key])
    cell = q.get("Cellular")
    if isinstance(cell, dict) and isinstance(cell.get("Dbm"), int | float):
        cell["Dbm"] = str(cell["Dbm"])
    return q


def probe_fault(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    q["ProbeTemperature"] = {"Celsius": FAULT_VALUE, "Fahrenheit": None}
    return q


def primary_temp_fault(p: Payload) -> Payload:
    q = copy.deepcopy(p)
    q["Temperature"] = {"Celsius": FAULT_VALUE, "Fahrenheit": None}
    return q


def exact_resend(p: Payload) -> list[Payload]:
    """The original followed by a byte-identical copy."""
    return [copy.deepcopy(p), copy.deepcopy(p)]


def conflicting_duplicate(p: Payload) -> list[Payload]:
    """The original followed by a payload with the same device and timestamp but a
    different position."""
    other = copy.deepcopy(p)
    lat = other["Location"]["Latitude"]
    other["Location"]["Latitude"] = round(lat + 0.01 if lat <= 89.0 else lat - 0.01, 6)
    return [copy.deepcopy(p), other]


@dataclass(frozen=True)
class Mutation:
    name: str
    label: str
    fn: Callable[[Payload], Any]
    pair: bool = False


MUTATIONS: dict[str, Mutation] = {
    m.name: m
    for m in (
        Mutation("out_of_range_latitude", "Out-of-range latitude", out_of_range_latitude),
        Mutation("out_of_range_longitude", "Out-of-range longitude", out_of_range_longitude),
        Mutation("drop_device_id", "Missing DeviceId", drop_device_id),
        Mutation("seconds_timestamp", "Timestamp in seconds", seconds_timestamp),
        Mutation("stringify_numbers", "Numbers as strings", stringify_numbers),
        Mutation("probe_fault", "Probe fault value", probe_fault),
        Mutation("primary_temp_fault", "Primary temperature fault value", primary_temp_fault),
        Mutation("exact_resend", "Exact resend", exact_resend, pair=True),
        Mutation(
            "conflicting_duplicate",
            "Same device and timestamp, different content",
            conflicting_duplicate,
            pair=True,
        ),
    )
}


@dataclass(frozen=True)
class MixItem:
    label: str
    payload: Payload
    mutation: str | None = None
    repeat_of_previous: bool = False  # byte-identical resend of the previous item


def apply_mix(
    payloads: list[Payload], selected: list[str], rate: float, rng: random.Random
) -> list[MixItem]:
    """Apply the selected mutations to roughly `rate` of the payloads.

    Single-payload mutations replace the payload. Pair mutations append a second
    payload right after the original. Deterministic for a seeded `rng`.
    """
    names = [n for n in selected if n in MUTATIONS]
    items: list[MixItem] = []
    for i, p in enumerate(payloads, start=1):
        base = f"gen #{i}"
        if not names or rng.random() >= rate:
            items.append(MixItem(base, copy.deepcopy(p)))
            continue
        m = MUTATIONS[rng.choice(names)]
        result = m.fn(p)
        if m.pair:
            first, second = result
            items.append(MixItem(base, first))
            items.append(
                MixItem(
                    f"{base} +{m.name}",
                    second,
                    m.name,
                    repeat_of_previous=m.name == "exact_resend",
                )
            )
        else:
            items.append(MixItem(f"{base} +{m.name}", result, m.name))
    return items
