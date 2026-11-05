# Implementation notes (Mock Sender)

Choices where the design was silent or ambiguous, with the section each relates to.

1. **Batch response shape** (file 19 `IngestionView`, API surface). The design says only "the
   same view for many ingestions". `ApiClient.get_statuses` accepts `{"items": [IngestionView, ...]}`
   (matches `IngestionPage(items, ...)`), a bare list, or an object keyed by id, and indexes by
   `IngestionView.id`. The test fake returns `{"items": [...]}`. Ids are sent as a comma-separated
   `ids` query value. The API does not tell the sender about ids it hides or lacks; missing ids
   are simply left unchanged and polled again until the timeout.
2. **`WebhookAccepted` on 200/400/409.** The sender reads `ingestion_id`, `status` and `duplicate`
   when present and falls back to `duplicate = (HTTP 200)`. Error text comes from `detail`,
   `error` or `message` in the JSON body, else the first 300 characters of the body.
3. **Auth header on batch/livez.** Batch uses the same `X-API-Key`; `/livez` sends none (public).
4. **Form handling** (file 70). Forms are urlencoded by HTMX. They are parsed with
   `urllib.parse.parse_qs` instead of FastAPI `Form`, so `python-multipart` is not needed
   (not in the design's dependency list).
5. **Idempotency toggle is page-wide.** One checkbox inside a single `<form>` is included in every
   HTMX send (samples, all, generated). Demo cases ignore it because each sets its own header
   behaviour. Within generated sends, an `exact_resend` item reuses the previous key (a true
   retry); every other item gets a new UUID.
6. **"Same key, different content" generator mutation** (Mock Sender section). Read as the natural
   reading key `(DeviceId, EntryTimeEpoch)`, not the HTTP header: the second payload has the same
   device and timestamp with latitude moved by 0.01. The header-based case is the "Key reuse"
   demo.
7. **Mutation definitions.** Fault value `327.67` for both probe and primary; seconds timestamp
   leaves `EntryTimeUtc` unchanged; strings apply to `Temperature.Celsius/Fahrenheit` and
   `Cellular.Dbm`. `apply_mix` picks one selected mutation per mutated payload; pair mutations
   append the second payload right after the first.
8. **Demo `resend`/`idempotency_conflict` use a fresh generated payload** (random seed) so the
   first send is always `202` even if the demo is clicked repeatedly. Demo `oversized`, `no_key`,
   `bad_key`, `bad_header` use sample 1. `bad_header` is a 300-character key (over the 255 limit);
   `bad_key` is the literal `mock-unknown-key`.
9. **Polling clock/timeout.** The 60 s timeout and 10 s backoff are measured from each send's
   `sent_at` (not `first_polled_at`, which is recorded but informational). A row that times out
   is shown with its last known status and "no longer polling".
10. **Generator content.** `FormattedAddress` is null in generated payloads; start time is fixed
    (`1739215646000`) and the interval is 5 min so a seed is fully reproducible (no wall clock).
11. **Generated send cap and order.** Count capped at 200; sends are sequential (not concurrent) so
    resend/conflict pairs reach the API in order.
12. **`uv.lock` / Docker.** `[tool.uv] package = false`; the Dockerfile copies uv from
    `ghcr.io/astral-sh/uv:0.13` and runs `uv sync --frozen --no-dev`.
13. **Banner/page.** The page never contains the API URL or key; the banner polls `/api-status`
    every 3 s until `/livez` returns 200.
14. **HTMX** was downloaded successfully (2.0.4); no placeholder was needed.
15. **Test dependency note.** `starlette.testclient` emits a deprecation warning about httpx; it is
    harmless and left as is.
