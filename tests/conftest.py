import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sender.config import Settings
from sender.main import create_app

KEY = "test-sender-key"
MAX_BODY = 256 * 1024
FINAL_STATUSES = {"accepted", "quarantined"}


class FakeApi:
    """httpx.MockTransport fake of the Integration API contract (design: Request path)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.rows: dict[str, dict[str, Any]] = {}
        self.by_hash: dict[str, str] = {}
        self.by_idem: dict[str, tuple[str, str]] = {}  # key -> (ingestion_id, body_hash)
        self.batch_calls = 0
        self.settle_after_batches = 1  # batch calls before a row turns final
        self.livez_ok = True
        self.final_status = "accepted"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/livez":
            if self.livez_ok:
                return httpx.Response(200, json={"status": "ok", "worker": "ok"})
            return httpx.Response(503, json={"status": "unavailable", "worker": "stale"})
        if path == "/api/webhook/tive" and request.method == "POST":
            return self.webhook(request)
        if path == "/api/ingestions/batch":
            return self.batch(request)
        return httpx.Response(404, json={"detail": "not found"})

    def webhook(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        if len(body) > MAX_BODY:
            return httpx.Response(413, json={"detail": "payload too large"})
        if request.headers.get("x-api-key") != KEY:
            return httpx.Response(401, json={"detail": "invalid api key"})
        idem = request.headers.get("idempotency-key")
        if idem is not None and not (1 <= len(idem) <= 255 and idem.isprintable()):
            return httpx.Response(400, json={"detail": "malformed Idempotency-Key"})
        try:
            parsed = json.loads(body, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
            body_hash = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            valid = isinstance(parsed, dict)
        except ValueError:
            body_hash, valid = body.decode("latin-1"), False
        if idem is not None:
            if idem in self.by_idem:
                iid, stored_hash = self.by_idem[idem]
                if stored_hash != body_hash:
                    return httpx.Response(409, json={"detail": "idempotency_key_reused"})
                return self.existing(iid)
        elif body_hash in self.by_hash:
            return self.existing(self.by_hash[body_hash])
        iid = str(uuid.uuid4())
        self.rows[iid] = {"status": "pending" if valid else "rejected", "polls": 0}
        if idem is not None:
            self.by_idem[idem] = (iid, body_hash)
        else:
            self.by_hash[body_hash] = iid
        if not valid:
            return httpx.Response(
                400,
                json={
                    "ingestion_id": iid,
                    "status": "rejected",
                    "detail": "body is not valid JSON",
                },
            )
        return httpx.Response(
            202,
            json={
                "ingestion_id": iid,
                "status": "pending",
                "status_url": f"/api/ingestions/{iid}",
                "duplicate": False,
            },
        )

    def existing(self, iid: str) -> httpx.Response:
        status = self.rows[iid]["status"]
        code = 400 if status == "rejected" else 200
        return httpx.Response(
            code,
            json={
                "ingestion_id": iid,
                "status": status,
                "status_url": f"/api/ingestions/{iid}",
                "duplicate": True,
            },
        )

    def batch(self, request: httpx.Request) -> httpx.Response:
        self.batch_calls += 1
        ids = [i for i in request.url.params.get("ids", "").split(",") if i]
        assert len(ids) <= 100
        items = []
        for iid in ids:
            row = self.rows.get(iid)
            if row is None:
                continue
            row["polls"] += 1
            if row["status"] == "pending" and row["polls"] >= self.settle_after_batches:
                row["status"] = self.final_status
            items.append(self.view(iid, row))
        return httpx.Response(200, json={"items": items})

    @staticmethod
    def view(iid: str, row: dict[str, Any]) -> dict[str, Any]:
        quarantined = row["status"] == "quarantined"
        return {
            "id": iid,
            "status": row["status"],
            "received_at": "2026-11-01T00:00:00Z",
            "processed_at": None,
            "attempts": 1,
            "issues": [
                {
                    "code": "latitude_out_of_range",
                    "field": "Location.Latitude",
                    "severity": "blocking",
                    "message": "latitude out of range",
                    "original": 95.0,
                    "corrected": None,
                    "source": "pipeline",
                }
            ]
            if quarantined
            else [],
            "issues_history": [],
            "readings": []
            if quarantined
            else [
                {
                    "kind": "sensor",
                    "px_payload": {"temperature": 4.2},
                    "current": True,
                    "superseded_by": None,
                },
                {
                    "kind": "location",
                    "px_payload": {"latitude": 1.0},
                    "current": True,
                    "superseded_by": None,
                },
            ],
            "candidate_sensor": {"temperature": 4.2} if quarantined else None,
            "candidate_location": {"latitude": 95.0} if quarantined else None,
            "write_result": None,
            "probe_temperature": None,
            "probe_fault": False,
            "promoted_at": None,
            "reprocess_count": 0,
        }


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(api: FakeApi, clock: Clock) -> TestClient:
    settings = Settings(
        integration_api_url="http://api.test",
        integration_api_key=KEY,
        poll_timeout_s=60,
        _env_file=None,
    )
    app = create_app(settings, transport=api.transport(), clock=clock)
    return TestClient(app)
