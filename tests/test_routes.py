import json
import re

from fastapi.testclient import TestClient

from tests.conftest import KEY, FakeApi

WEBHOOK = "/api/webhook/tive"


def posts(api: FakeApi):
    return [r for r in api.requests if r.url.path == WEBHOOK]


def test_index_renders_without_secrets(client: TestClient) -> None:
    html = client.get("/").text
    assert "Send all 13" in html
    assert KEY not in html
    assert "api.test" not in html
    assert "X-API-Key" not in html


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").text == "ok"


def test_send_one_sample(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/sample/1").text
    assert len(posts(api)) == 1
    req = posts(api)[0]
    assert req.headers["x-api-key"] == KEY
    assert "idempotency-key" not in req.headers
    assert json.loads(req.content)["DeviceId"] == "863257063350583"
    assert "sample 1: Standard Temperature Shipment" in html
    assert "202" in html
    assert 'hx-get="/results"' in html  # pending row, so polling starts
    assert KEY not in html


def test_send_unknown_sample_404(client: TestClient) -> None:
    assert client.post("/send/sample/99").status_code == 404


def test_send_all_samples(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/samples").text
    assert len(posts(api)) == 13
    assert "13 sends" in html


def test_idempotency_toggle_adds_unique_header(client: TestClient, api: FakeApi) -> None:
    client.post("/send/samples", content="idempotency=1")
    keys = [r.headers.get("idempotency-key") for r in posts(api)]
    assert all(keys)
    assert len(set(keys)) == 13


def test_generated_send_respects_count_and_cap(client: TestClient, api: FakeApi) -> None:
    client.post("/send/generated", content="count=5&devices=2&seed=3&rate=0")
    assert len(posts(api)) == 5
    api.requests.clear()
    client.post("/send/generated", content="count=100000&devices=1&seed=3&rate=0")
    assert len(posts(api)) == 200


def test_generated_with_mutations_is_deterministic(client: TestClient, api: FakeApi) -> None:
    body = "count=20&devices=2&seed=9&rate=1&mutations=drop_device_id&mutations=probe_fault"
    client.post("/send/generated", content=body)
    first = [r.content for r in posts(api)]
    assert any("DeviceId" not in json.loads(c) for c in first)
    api.requests.clear()
    client.post("/send/generated", content=body)
    assert [r.content for r in posts(api)] == first


def test_bad_form_number_is_422(client: TestClient) -> None:
    assert client.post("/send/generated", content="count=abc").status_code == 422


def test_results_makes_one_batch_call_and_settles(client: TestClient, api: FakeApi) -> None:
    client.post("/send/samples")
    html = client.get("/results").text
    assert api.batch_calls == 1
    batch = [r for r in api.requests if r.url.path.endswith("/batch")][0]
    assert len(batch.url.params["ids"].split(",")) == 13
    assert "hx-get" not in html  # all final: no polling trigger
    assert html.count("st-accepted") >= 13
    assert "sensor" in html  # readings rendered


def test_results_shows_issues_and_candidates_for_quarantine(
    client: TestClient, api: FakeApi
) -> None:
    api.final_status = "quarantined"
    client.post("/send/sample/2")
    html = client.get("/results").text
    assert "latitude_out_of_range" in html
    assert "candidate_sensor" in html
    assert "st-quarantined" in html


def test_results_keeps_polling_until_final(client: TestClient, api: FakeApi) -> None:
    api.settle_after_batches = 3
    client.post("/send/sample/1")
    assert 'hx-trigger="every 1s"' in client.get("/results").text
    assert 'hx-trigger="every 1s"' in client.get("/results").text
    assert "hx-get" not in client.get("/results").text
    calls = api.batch_calls
    client.get("/results")
    assert api.batch_calls == calls  # nothing left to poll: no batch call


def test_polling_backs_off_after_10s_then_stops_at_timeout(
    client: TestClient, api: FakeApi, clock
) -> None:
    api.settle_after_batches = 10_000
    client.post("/send/sample/1")
    clock.t += 5
    assert "every 1s" in client.get("/results").text
    clock.t += 6  # 11 s old
    assert "every 5s" in client.get("/results").text
    calls = api.batch_calls
    clock.t += 50  # 61 s old
    html = client.get("/results").text
    assert "hx-get" not in html
    assert "no longer polling" in html
    assert api.batch_calls == calls  # timed out rows are not polled


def test_demo_oversized(client: TestClient, api: FakeApi) -> None:
    client.post("/send/demo/oversized")
    (req,) = posts(api)
    assert len(req.content) > 256 * 1024
    assert req.headers["x-api-key"] == KEY


def test_demo_no_key_and_bad_key(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/demo/no_key").text
    assert "x-api-key" not in posts(api)[0].headers
    assert "401" in html
    client.post("/send/demo/bad_key")
    bad = posts(api)[1].headers["x-api-key"]
    assert bad and bad != KEY


def test_demo_bad_header(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/demo/bad_header").text
    assert len(posts(api)[0].headers["idempotency-key"]) > 255
    assert "400" in html


def test_demo_malformed_json(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/demo/malformed_json").text
    body = posts(api)[0].content
    assert b"NaN" in body
    assert "400" in html
    assert "rejected" in html


def test_demo_resend(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/demo/resend").text
    a, b = posts(api)
    assert a.content == b.content
    assert "idempotency-key" not in a.headers
    assert "202" in html
    assert "200" in html
    ids = set(re.findall(r'class="mono">([0-9a-f-]{36})<', html))
    assert len(ids) == 1


def test_demo_idempotency_conflict(client: TestClient, api: FakeApi) -> None:
    html = client.post("/send/demo/idempotency_conflict").text
    a, b = posts(api)
    assert a.headers["idempotency-key"] == b.headers["idempotency-key"]
    assert a.content != b.content
    assert "202" in html
    assert "409" in html
    assert len(api.rows) == 1


def test_unknown_demo_404(client: TestClient) -> None:
    assert client.post("/send/demo/nope").status_code == 404


def test_api_status_banner(client: TestClient, api: FakeApi) -> None:
    api.livez_ok = False
    assert "waking up" in client.get("/api-status").text
    assert "hx-get" in client.get("/api-status").text
    api.livez_ok = True
    html = client.get("/api-status").text
    assert "API ready" in html
    assert "hx-get" not in html


def test_connection_error_is_recorded_not_raised(client: TestClient, api: FakeApi) -> None:
    def boom(request):
        import httpx

        raise httpx.ConnectError("refused")

    api.webhook = boom  # type: ignore[method-assign]
    html = client.post("/send/sample/1").text
    assert "ConnectError" in html
    assert "hx-get" not in html


def test_api_key_never_in_any_page(client: TestClient) -> None:
    client.post("/send/samples")
    for path in ("/", "/results", "/api-status"):
        assert KEY not in client.get(path).text
