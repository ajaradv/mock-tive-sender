# PAXAFE Mock Tive Sender

A small FastAPI + Jinja + HTMX app that produces Tive-shaped webhook payloads, sends them to
the PAXAFE Integration API, and shows how each one was handled: HTTP status, ingestion ID,
final status, issues and corrections, and the stored or candidate PX records.

Live URL: _fill in after deploying to Render_ (the first load wakes the free instance, and the
page banner shows "API waking up..." until the Integration API's `/livez` returns 200).

The API key and URL live only in the server's environment. They are never rendered into a page.

## Local setup

```bash
cp .env.example .env        # set INTEGRATION_API_URL and INTEGRATION_API_KEY
uv sync
uv run uvicorn sender.main:app --reload --port 8001
```

Open http://localhost:8001. The Integration API can be local (`http://localhost:8000`) or the
deployed one. Create a sender key in the Integration API with `python -m app.cli create-key`.

With Docker: `docker build -t mock-tive-sender . && docker run --env-file .env -p 8001:8000 mock-tive-sender`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `INTEGRATION_API_URL` | `http://localhost:8000` | Base URL of the Integration API |
| `INTEGRATION_API_KEY` | (empty) | Sender-role API key |
| `REQUEST_TIMEOUT_S` | 60 | Per-request timeout, long enough for a cold start |
| `POLL_TIMEOUT_S` | 60 | Stop polling a send after this many seconds |
| `HISTORY_LIMIT` | 500 | Rows kept in the results table |

## Tests and checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Route tests run against an `httpx.MockTransport` fake of the Integration API, so no network or
database is needed.

## Panels

**Samples.** The 13 provided payloads (`sender/data/sample-tive-payloads.json`), sent one at a
time or all at once. Expected final statuses are in the design doc's "Expected outcome per
sample" table: 8 accepted or accepted with corrections, 5 quarantined.

**Generator.** `count` (max 200), `devices`, `seed` and a mutation `rate`. The same seed gives
the same payloads and the same mutations. Every valid payload is checked against the vendored
`tive-incoming-schema.json` before mutations are applied; a failure is a generator error.

| Mutation | Effect | Expected outcome |
| --- | --- | --- |
| Out-of-range latitude / longitude | latitude `95.0` / longitude `-200.0` | quarantined |
| Missing DeviceId | top-level `DeviceId` removed | quarantined (`device_imei_missing`) |
| Timestamp in seconds | `EntryTimeEpoch` divided by 1000 | quarantined (`timestamp_implausible`) |
| Numbers as strings | `Temperature.*` and `Cellular.Dbm` become strings | accepted with corrections |
| Probe fault value | `ProbeTemperature.Celsius = 327.67` | accepted, warning `probe_fault` |
| Primary temperature fault value | `Temperature.Celsius = 327.67` | quarantined (`temperature_fault`) |
| Exact resend | the payload is sent twice, byte for byte | `202`, then `200` with the same ID |
| Same device and timestamp, different content | second payload moves the latitude | both accepted; last write wins |

The "Idempotency-Key" checkbox adds a fresh UUID header to each send. An exact resend reuses the
previous key (it is a retry), so it still returns `200`.

**Demo cases.**

| Button | What is sent | Expected response |
| --- | --- | --- |
| Oversized body | sample 1 plus 300 KB of padding | `413`, not stored |
| No key | no `X-API-Key` header | `401`, not stored |
| Unknown key | an invalid key | `401`, not stored |
| Malformed Idempotency-Key | a 300-character header value | `400`, not stored |
| Malformed JSON | a body containing `NaN` | `400` with an `ingestion_id`; stored as `rejected` |
| Resend | a fresh generated payload, twice, no header | `202`, then `200` with the same `ingestion_id` |
| Key reuse | the same `Idempotency-Key`, two different bodies | `202`, then `409` |

## Results and polling

`GET /results` calls the API's `GET /api/ingestions/batch?ids=...` once for every send that is
not yet final (in chunks of 100). The table polls every 1 s, every 5 s once the oldest pending
row is 10 s old, and stops when every row is final or `POLL_TIMEOUT_S` has passed.

History is kept **in memory** in the Mock Sender process, capped at `HISTORY_LIMIT` rows, and is
lost on restart. The Integration API is the system of record.

## Vendored files

| File | Source / version |
| --- | --- |
| `sender/data/sample-tive-payloads.json` | copy of `sample-tive-payloads.json` in the workspace |
| `sender/schemas/tive-incoming-schema.json` | copy of `tive-incoming-schema.json` in the workspace |
| `sender/static/htmx.min.js` | HTMX **2.0.4**, from `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` |

`tests/test_vendored_files.py` pins the SHA-256 of each, so an edit without updating the pin
fails the test.

## Deployment (Render)

`render.yaml` defines one free Docker web service with `healthCheckPath: /healthz`. Set
`INTEGRATION_API_URL` and `INTEGRATION_API_KEY` in the Render dashboard (both are `sync: false`).
