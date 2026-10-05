import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "orders.db")
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_and_seeded_orders(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    orders = client.get("/api/orders").json()
    assert len(orders) == 3
    assert {order["priority"] for order in orders} == {"standard", "express"}


def test_create_and_update_order(client):
    response = client.post(
        "/api/orders",
        json={"customer": "Taylor", "item": "Mug", "priority": "standard"},
    )
    assert response.status_code == 201
    order_id = response.json()["id"]
    assert client.get(f"/api/orders/{order_id}").json()["status"] == "received"
    updated = client.patch(f"/api/orders/{order_id}", json={"status": "shipped"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "shipped"


def test_missing_order(client):
    assert client.get("/api/orders/missing").status_code == 404


def test_order_lookup_records_http_route_and_status(client, monkeypatch):
    metric_calls = []
    log_calls = []

    class RecordingSpan:
        def __init__(self):
            self.attributes = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def set_attribute(self, name, value):
            self.attributes[name] = value

        def set_status(self, status):
            pass

    class RecordingTracer:
        def __init__(self):
            self.spans = []

        def start_as_current_span(self, name):
            span = RecordingSpan()
            self.spans.append((name, span))
            return span

    recording_tracer = RecordingTracer()
    monkeypatch.setattr(main, "tracer", recording_tracer)
    monkeypatch.setattr(main, "record_order_lookup", metric_calls.append)
    monkeypatch.setattr(
        main,
        "emit_order_lookup_log",
        lambda status_code, found, error_type=None: log_calls.append(
            (status_code, found, error_type)
        ),
    )

    found = client.get("/api/orders/standard-1001")
    missing = client.get("/api/orders/missing")

    assert found.status_code == 200
    assert missing.status_code == 404
    assert metric_calls == [200, 404]
    assert log_calls == [(200, True, None), (404, False, None)]
    assert [name for name, _span in recording_tracer.spans] == [
        "order lookup",
        "order lookup",
    ]
    assert [
        span.attributes["http.response.status_code"]
        for _name, span in recording_tracer.spans
    ] == [200, 404]


def test_order_lookup_metric_uses_http_semantic_attributes(monkeypatch):
    metric_calls = []
    monkeypatch.setattr(main.order_lookup_requests, "add", lambda *args: metric_calls.append(args))
    monkeypatch.setattr(main.metric_reader, "get_metrics_data", lambda: object())
    monkeypatch.setattr(main.metric_exporter, "export", lambda metrics_data: None)

    main.record_order_lookup(200)

    assert metric_calls == [
        (
            1,
            {
                "http.route": "/api/orders/{order_id}",
                "http.response.status_code": 200,
            },
        )
    ]


def test_order_lookup_log_is_structured_without_order_data(monkeypatch):
    log_calls = []

    class RecordingLogger:
        def emit(self, **kwargs):
            log_calls.append(kwargs)

    monkeypatch.setattr(main, "telemetry_logger", RecordingLogger())

    main.emit_order_lookup_log(404, False)

    assert log_calls == [
        {
            "severity_number": main.SeverityNumber.WARN,
            "body": "Order lookup not found",
            "attributes": {
                "http.route": "/api/orders/{order_id}",
                "http.response.status_code": 404,
                "order.found": False,
            },
        }
    ]
