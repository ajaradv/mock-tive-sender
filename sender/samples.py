import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLES_PATH = Path(__file__).parent / "data" / "sample-tive-payloads.json"


@dataclass(frozen=True)
class Sample:
    index: int  # 1-based, matches the design's "Expected outcome per sample" table
    name: str
    description: str
    payload: dict[str, Any]


def load_samples(path: Path = SAMPLES_PATH) -> list[Sample]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Sample(i, item["name"], item.get("description", ""), item["payload"])
        for i, item in enumerate(data["payloads"], start=1)
    ]
