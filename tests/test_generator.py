import pytest

from sender.generator import GeneratorError, generate, validate_payload


def test_100_payloads_pass_the_tive_schema() -> None:
    payloads = generate(100, 5, seed=7)
    assert len(payloads) == 100
    for p in payloads:
        validate_payload(p)


def test_same_seed_same_output() -> None:
    assert generate(30, 3, seed=42) == generate(30, 3, seed=42)
    assert generate(30, 3, seed=42) != generate(30, 3, seed=43)


def test_timestamps_increase_per_device() -> None:
    last: dict[str, int] = {}
    for p in generate(60, 4, seed=1):
        imei, ts = p["DeviceId"], p["EntryTimeEpoch"]
        assert ts > last.get(imei, 0)
        last[imei] = ts
    assert len(last) == 4


def test_imeis_have_15_digits() -> None:
    for p in generate(40, 6, seed=3):
        assert len(p["DeviceId"]) == 15
        assert p["DeviceId"].isdigit()


def test_invalid_payload_raises_generator_error() -> None:
    p = generate(1, 1, seed=1)[0]
    p["Location"]["Latitude"] = 95.0
    with pytest.raises(GeneratorError):
        validate_payload(p)


def test_temperature_range() -> None:
    for p in generate(50, 2, seed=9):
        assert 2.0 <= p["Temperature"]["Celsius"] <= 8.0
