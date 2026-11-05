import copy
import random

import pytest

from sender.generator import generate
from sender.mutations import (
    MUTATIONS,
    apply_mix,
    conflicting_duplicate,
    drop_device_id,
    exact_resend,
    out_of_range_latitude,
    out_of_range_longitude,
    primary_temp_fault,
    probe_fault,
    seconds_timestamp,
    stringify_numbers,
)


@pytest.fixture
def base() -> dict:
    return generate(1, 1, seed=5)[0]


def diff(a, b, path=""):
    """Paths whose values differ between two JSON values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
        return out
    return [] if a == b else [path]


@pytest.mark.parametrize(
    ("fn", "paths"),
    [
        (out_of_range_latitude, ["/Location/Latitude"]),
        (out_of_range_longitude, ["/Location/Longitude"]),
        (drop_device_id, ["/DeviceId"]),
        (seconds_timestamp, ["/EntryTimeEpoch"]),
        (stringify_numbers, ["/Cellular/Dbm", "/Temperature/Celsius", "/Temperature/Fahrenheit"]),
        (probe_fault, ["/ProbeTemperature/Celsius", "/ProbeTemperature/Fahrenheit"]),
        (primary_temp_fault, ["/Temperature/Celsius", "/Temperature/Fahrenheit"]),
    ],
)
def test_single_mutation_changes_only_its_fields(fn, paths, base) -> None:
    original = copy.deepcopy(base)
    result = fn(base)
    assert base == original  # input untouched
    assert diff(original, result) == paths


def test_mutation_values(base) -> None:
    assert out_of_range_latitude(base)["Location"]["Latitude"] == 95.0
    assert out_of_range_longitude(base)["Location"]["Longitude"] == -200.0
    assert "DeviceId" not in drop_device_id(base)
    assert seconds_timestamp(base)["EntryTimeEpoch"] == base["EntryTimeEpoch"] // 1000
    s = stringify_numbers(base)
    assert isinstance(s["Temperature"]["Celsius"], str)
    assert isinstance(s["Cellular"]["Dbm"], str)
    assert probe_fault(base)["ProbeTemperature"]["Celsius"] == 327.67
    assert primary_temp_fault(base)["Temperature"]["Celsius"] == 327.67


def test_exact_resend_is_identical(base) -> None:
    a, b = exact_resend(base)
    assert a == b == base
    assert a is not b


def test_conflicting_duplicate_same_key_different_content(base) -> None:
    original = copy.deepcopy(base)
    a, b = conflicting_duplicate(base)
    assert base == original
    assert a == base
    assert (a["DeviceId"], a["EntryTimeEpoch"]) == (b["DeviceId"], b["EntryTimeEpoch"])
    assert diff(a, b) == ["/Location/Latitude"]


def test_registry_covers_all_mutations() -> None:
    assert set(MUTATIONS) == {
        "out_of_range_latitude", "out_of_range_longitude", "drop_device_id",
        "seconds_timestamp", "stringify_numbers", "probe_fault", "primary_temp_fault",
        "exact_resend", "conflicting_duplicate",
    }  # fmt: skip


def test_apply_mix_is_deterministic() -> None:
    payloads = generate(40, 3, seed=2)
    sel = list(MUTATIONS)
    a = apply_mix(payloads, sel, 0.5, random.Random(11))
    b = apply_mix(payloads, sel, 0.5, random.Random(11))
    assert a == b
    assert len(a) > 40  # pair mutations add items
    assert any(i.mutation for i in a)


def test_apply_mix_rate_zero_and_no_selection(base) -> None:
    payloads = generate(10, 2, seed=2)
    assert [
        i.payload for i in apply_mix(payloads, list(MUTATIONS), 0.0, random.Random(1))
    ] == payloads
    assert [i.payload for i in apply_mix(payloads, [], 1.0, random.Random(1))] == payloads


def test_apply_mix_does_not_mutate_inputs() -> None:
    payloads = generate(20, 2, seed=2)
    snapshot = copy.deepcopy(payloads)
    apply_mix(payloads, list(MUTATIONS), 1.0, random.Random(3))
    assert payloads == snapshot
