import copy
import random
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sender.client import ApiClient, SendResult
from sender.config import Settings
from sender.generator import GeneratorError, generate
from sender.mutations import MUTATIONS, MixItem, apply_mix
from sender.samples import load_samples
from sender.store import HistoryStore, SendRecord

BASE = Path(__file__).parent
MAX_GENERATED = 200
BATCH_LIMIT = 100
OVERSIZE_BYTES = 300 * 1024
FAST_POLL_S = 1
SLOW_POLL_S = 5
BACKOFF_AFTER_S = 10

DEMO_CASES: dict[str, tuple[str, str]] = {
    "oversized": ("Oversized body", "expects 413, not stored"),
    "no_key": ("No key", "expects 401, not stored"),
    "bad_key": ("Unknown key", "expects 401, not stored"),
    "bad_header": ("Malformed Idempotency-Key", "expects 400, not stored"),
    "malformed_json": ("Malformed JSON", "expects 400 with an ingestion_id, stored as rejected"),
    "resend": ("Resend", "same body twice: 202, then 200 with the same ingestion_id"),
    "idempotency_conflict": (
        "Key reuse",
        "same Idempotency-Key, two bodies: 202, then 409",
    ),
}


def poll_interval(records: list[SendRecord], now: float, poll_timeout_s: float) -> int | None:
    """Seconds until the next poll, or None when polling should stop."""
    ages = [now - r.sent_at for r in records if not r.is_final and now - r.sent_at < poll_timeout_s]
    if not ages:
        return None
    return SLOW_POLL_S if max(ages) >= BACKOFF_AFTER_S else FAST_POLL_S


def _apply_view(rec: SendRecord, view: dict[str, Any]) -> None:
    rec.final_status = view.get("status") or rec.final_status
    rec.issues = list(view.get("issues") or [])
    readings = [r for r in (view.get("readings") or []) if isinstance(r, dict)]
    for kind in ("candidate_sensor", "candidate_location"):
        if view.get(kind):
            readings.append({"kind": kind, "px_payload": view[kind], "current": False})
    rec.readings = readings


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = settings or Settings()
    client = ApiClient(
        settings.integration_api_url,
        settings.integration_api_key,
        settings.request_timeout_s,
        transport=transport,
    )
    store = HistoryStore(settings.history_limit)
    samples = load_samples()
    templates = Jinja2Templates(directory=str(BASE / "templates"))
    templates.env.filters["timestamp"] = lambda t: time.strftime("%H:%M:%S", time.gmtime(t))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    app = FastAPI(title="PAXAFE Mock Tive Sender", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    app.state.client = client
    app.state.store = store

    async def table(request: Request) -> HTMLResponse:
        records = await store.snapshot()
        now = clock()
        return templates.TemplateResponse(
            request,
            "partials/results_table.html",
            {
                "records": records,
                "interval": poll_interval(records, now, settings.poll_timeout_s),
                "now": now,
                "poll_timeout_s": settings.poll_timeout_s,
            },
        )

    async def form_of(request: Request) -> dict[str, list[str]]:
        return parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)

    def first(form: dict[str, list[str]], key: str, default: str) -> str:
        values = form.get(key)
        return values[0] if values and values[0] != "" else default

    def number(form: dict[str, list[str]], key: str, default: float, cast: type) -> Any:
        try:
            return cast(first(form, key, str(default)))
        except ValueError:
            raise HTTPException(422, f"{key} must be a number") from None

    async def do_send(label: str, payload: dict[str, Any] | bytes, **kw: Any) -> SendResult:
        rec = await store.add(label, clock())
        result = await client.send(payload, **kw)
        rec.http_status = result.http_status
        rec.ingestion_id = result.ingestion_id
        rec.final_status = result.status if result.ingestion_id else None
        rec.error = result.error
        rec.detail = result.detail if result.http_status and result.http_status >= 400 else None
        return result

    def idem(form: dict[str, list[str]]) -> str | None:
        return str(uuid.uuid4()) if "idempotency" in form else None

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "samples": samples,
                "mutations": list(MUTATIONS.values()),
                "demo_cases": DEMO_CASES,
                "max_generated": MAX_GENERATED,
                "records": [],
                "interval": None,
                "now": clock(),
                "poll_timeout_s": settings.poll_timeout_s,
            },
        )

    @app.post("/send/sample/{index}", response_class=HTMLResponse)
    async def send_sample(request: Request, index: int) -> HTMLResponse:
        if not 1 <= index <= len(samples):
            raise HTTPException(404, "unknown sample")
        form = await form_of(request)
        s = samples[index - 1]
        await do_send(
            f"sample {s.index}: {s.name}", copy.deepcopy(s.payload), idempotency_key=idem(form)
        )
        return await table(request)

    @app.post("/send/samples", response_class=HTMLResponse)
    async def send_all_samples(request: Request) -> HTMLResponse:
        form = await form_of(request)
        for s in samples:
            await do_send(
                f"sample {s.index}: {s.name}", copy.deepcopy(s.payload), idempotency_key=idem(form)
            )
        return await table(request)

    @app.post("/send/generated", response_class=HTMLResponse)
    async def send_generated(request: Request) -> HTMLResponse:
        form = await form_of(request)
        count = min(max(number(form, "count", 10, int), 0), MAX_GENERATED)
        devices = max(number(form, "devices", 2, int), 1)
        seed = number(form, "seed", 1, int)
        rate = min(max(number(form, "rate", 0.3, float), 0.0), 1.0)
        try:
            payloads = generate(count, devices, seed)
        except GeneratorError as exc:
            raise HTTPException(500, f"generator error: {exc}") from exc
        items: list[MixItem] = apply_mix(
            payloads, form.get("mutations", []), rate, random.Random(seed)
        )
        use_keys = "idempotency" in form
        key: str | None = None
        for item in items:
            # A byte-identical resend is a retry, so it reuses the previous Idempotency-Key.
            if not (use_keys and item.repeat_of_previous):
                key = str(uuid.uuid4()) if use_keys else None
            await do_send(item.label, item.payload, idempotency_key=key)
        return await table(request)

    @app.post("/send/demo/{case}", response_class=HTMLResponse)
    async def send_demo(request: Request, case: str) -> HTMLResponse:
        if case not in DEMO_CASES:
            raise HTTPException(404, "unknown demo case")
        title = DEMO_CASES[case][0]
        sample = copy.deepcopy(samples[0].payload)
        fresh = generate(1, 1, secrets.randbelow(2**31))[0]
        if case == "oversized":
            sample["Padding"] = "x" * OVERSIZE_BYTES
            await do_send(f"demo: {title}", sample)
        elif case == "no_key":
            await do_send(f"demo: {title}", sample, omit_key=True)
        elif case == "bad_key":
            await do_send(f"demo: {title}", sample, key_override="mock-unknown-key")
        elif case == "bad_header":
            await do_send(f"demo: {title}", sample, idempotency_key="k" * 300)
        elif case == "malformed_json":
            await do_send(
                f"demo: {title}",
                b'{"DeviceId": "863257063350583", "Temperature": {"Celsius": NaN}}',
            )
        elif case == "resend":
            await do_send(f"demo: {title} (1st)", fresh)
            await do_send(f"demo: {title} (2nd)", fresh)
        else:  # idempotency_conflict
            key = str(uuid.uuid4())
            other = copy.deepcopy(fresh)
            other["Location"]["Latitude"] = round(other["Location"]["Latitude"] + 0.01, 6)
            await do_send(f"demo: {title} (body A)", fresh, idempotency_key=key)
            await do_send(f"demo: {title} (body B)", other, idempotency_key=key)
        return await table(request)

    @app.get("/results", response_class=HTMLResponse)
    async def results(request: Request) -> HTMLResponse:
        now = clock()
        records = await store.snapshot()
        active = [
            r for r in records if not r.is_final and now - r.sent_at < settings.poll_timeout_s
        ]
        for r in active:
            if r.first_polled_at is None:
                r.first_polled_at = now
        ids = sorted({r.ingestion_id for r in active if r.ingestion_id})
        views: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), BATCH_LIMIT):
            views.update(await client.get_statuses(ids[i : i + BATCH_LIMIT]))
        for r in active:
            view = views.get(r.ingestion_id or "")
            if view:
                _apply_view(r, view)
        return await table(request)

    @app.get("/api-status", response_class=HTMLResponse)
    async def api_status(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "partials/api_banner.html", {"up": await client.livez()}
        )

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    return app


app = create_app()
