import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "sender"

PINNED = {
    "data/sample-tive-payloads.json": "25d4eb8ccee49b6560456cec851a47adb8307bca07524c382b7849da23c5174f",
    "schemas/tive-incoming-schema.json": "45bc72c281dd863324d455fd98252c2c34db419854d07562f5b47caae6a0694f",
    "static/htmx.min.js": "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447",
}


@pytest.mark.parametrize(("rel", "digest"), PINNED.items())
def test_vendored_file_matches_pin(rel: str, digest: str) -> None:
    assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest


def test_htmx_is_not_the_placeholder() -> None:
    assert b"htmx" in (ROOT / "static/htmx.min.js").read_bytes()[:200]
