import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

Payload = dict[str, Any]


@dataclass
class SendResult:
    http_status: int | None = None
    ingestion_id: str | None = None
    status: str | None = None
    duplicate: bool = False
    body: Any = None
    detail: str | None = None
    error: str | None = None
    elapsed_ms: float = 0.0


def _detail(body: Any, text: str) -> str:
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            if key in body:
                value = body[key]
                return value if isinstance(value, str) else json.dumps(value)
        return json.dumps(body)
    return text[:300]


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


class ApiClient:
    """Thin client for the Integration API. Never retries and never raises on HTTP or
    network errors: they are recorded in the result."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(
        self,
        payload: Payload | bytes,
        *,
        key_override: str | None = None,
        omit_key: bool = False,
        idempotency_key: str | None = None,
    ) -> SendResult:
        content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if not omit_key:
            headers["X-API-Key"] = key_override if key_override is not None else self._api_key
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        started = time.perf_counter()
        result = SendResult()
        try:
            resp = await self._http.post("/api/webhook/tive", content=content, headers=headers)
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        else:
            result.http_status = resp.status_code
            try:
                result.body = resp.json()
            except ValueError:
                result.body = None
            result.detail = _detail(result.body, resp.text)
            if isinstance(result.body, dict):
                result.ingestion_id = _str_or_none(result.body.get("ingestion_id"))
                result.status = _str_or_none(result.body.get("status"))
                result.duplicate = bool(result.body.get("duplicate", resp.status_code == 200))
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

    async def get_statuses(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Call GET /api/ingestions/batch and return {ingestion_id: IngestionView}.

        Accepts `{"items": [...]}`, a bare list, or an object keyed by id.
        Returns an empty dict on any HTTP or network error.
        """
        if not ids:
            return {}
        try:
            resp = await self._http.get(
                "/api/ingestions/batch",
                params={"ids": ",".join(ids)},
                headers={"X-API-Key": self._api_key},
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return {}
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
        elif isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        else:
            return {}
        return {str(v["id"]): v for v in items if isinstance(v, dict) and "id" in v}

    async def livez(self) -> bool:
        try:
            resp = await self._http.get("/livez")
        except httpx.HTTPError:
            return False
        return resp.status_code == 200
