import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

FINAL_STATUSES = frozenset(
    {"accepted", "accepted_with_corrections", "quarantined", "rejected", "promoted", "failed"}
)


@dataclass
class SendRecord:
    local_id: int
    label: str
    sent_at: float
    http_status: int | None = None
    ingestion_id: str | None = None
    final_status: str | None = None  # last status seen; may be pending/processing
    issues: list[dict[str, Any]] = field(default_factory=list)
    readings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    detail: str | None = None
    first_polled_at: float | None = None

    @property
    def is_final(self) -> bool:
        """No more polling is useful: nothing to look up, or the status is final."""
        if self.ingestion_id is None:
            return True
        return self.final_status in FINAL_STATUSES


class HistoryStore:
    """Bounded in-memory send history, newest first. Lost on restart by design."""

    def __init__(self, limit: int = 500) -> None:
        self._items: deque[SendRecord] = deque(maxlen=limit)
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def add(self, label: str, sent_at: float) -> SendRecord:
        async with self._lock:
            rec = SendRecord(local_id=self._next_id, label=label, sent_at=sent_at)
            self._next_id += 1
            self._items.appendleft(rec)
            return rec

    async def snapshot(self) -> list[SendRecord]:
        async with self._lock:
            return list(self._items)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()
