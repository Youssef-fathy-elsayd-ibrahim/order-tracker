import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as responder

TEST_APPROVAL_TOKEN = "test-approval-token-012345678901234"


def test_health_endpoint():
    with TestClient(responder.app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_alert_is_saved_with_external_evidence_and_agent_status(tmp_path, monkeypatch):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.delenv("AGENT_COMMAND", raising=False)
    monkeypatch.setattr(
        responder,
        "_loki_logs",
        lambda route, start, end: {
            "source": "Loki",
            "status": "ok",
            "data": {
                "data": {
                    "result": [
                        {"stream": {"trace_id": "0123456789abcdef"}, "values": []}
                    ]
                }
            },
        },
    )
    monkeypatch.setattr(
        responder,
        "_tempo_traces",
        lambda route, start, end, logs: {
            "source": "Tempo",
            "status": "ok",
            "traces": [{"trace_id": "0123456789abcdef"}],
        },
    )
    with TestClient(responder.app) as client:
        response = client.post(
            "/alerts",
            json={
                "receiver": "smoke-test",
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "Order Tracker - 5xx Errors",
                            "http_response_status_code": "500",
                        },
                        "annotations": {
                            "endpoint": "/api/orders/{order_id}",
                            "description": "A local smoke-test alert",
                        },
                        "startsAt": "2026-10-05T19:00:00Z",
                        "generatorURL": "http://localhost:3000/alerting/example",
                    }
                ],
            },
        )

    assert response.status_code == 202
    result = response.json()
    assert result["agent_status"] == "unavailable"
    assert result["logs_status"] == "ok"
    assert result["traces_status"] == "ok"

    incident_dir = Path(result["incident_path"])
    incident = json.loads((incident_dir / "incident.json").read_text(encoding="utf-8"))
    logs = json.loads((incident_dir / "logs.json").read_text(encoding="utf-8"))
    traces = json.loads((incident_dir / "traces.json").read_text(encoding="utf-8"))
    agent = json.loads(
        (incident_dir / "agent-output.json").read_text(encoding="utf-8")
    )
    assert incident["endpoint"] == "/api/orders/{order_id}"
    assert incident["http_status_code"] == "500"
    assert logs["status"] == "ok"
    assert traces["traces"][0]["trace_id"] == "0123456789abcdef"
    assert agent["status"] == "unavailable"
    assert "Inspect the available application source" in (
        incident_dir / "agent-prompt.txt"
    ).read_text(encoding="utf-8")
    assert "do not edit files, run commands, or claim the incident is" in (
        incident_dir / "agent-prompt.txt"
    ).read_text(encoding="utf-8")
    assert incident["resolution_status"] == "investigating"


def test_missing_external_services_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.delenv("AGENT_COMMAND", raising=False)
    monkeypatch.setattr(
        responder,
        "_loki_logs",
        lambda route, start, end: {
            "source": "Loki",
            "status": "error",
            "error": {"type": "URLError", "message": "Backend connection failed"},
        },
    )
    monkeypatch.setattr(
        responder,
        "_tempo_traces",
        lambda route, start, end, logs: {
            "source": "Tempo",
            "status": "error",
            "errors": [{"operation": "search", "type": "URLError"}],
            "traces": [],
        },
    )
    with TestClient(responder.app) as client:
        response = client.post(
            "/alerts",
            json={"alertname": "local smoke test", "endpoint": "/api/orders/{order_id}"},
        )

    assert response.status_code == 202
    result = response.json()
    assert result["logs_status"] == "error"
    assert result["traces_status"] == "error"
    saved_logs = json.loads(
        (Path(result["incident_path"]) / "logs.json").read_text(encoding="utf-8")
    )
    assert saved_logs["error"]["message"] == "Backend connection failed"


def test_loki_query_retries_until_recent_log_is_ingested(monkeypatch):
    responses = iter(
        [
            {"data": {"result": []}},
            {"data": {"result": [{"stream": {"http_route": "/api/orders/{order_id}"}}]}},
        ]
    )
    requested = []
    monkeypatch.setattr(responder, "_request_json", lambda url: (requested.append(url), next(responses))[1])
    monkeypatch.setattr(responder.time, "sleep", lambda seconds: None)

    result = responder._loki_logs("/api/orders/{order_id}", 100, 200)

    assert result["status"] == "ok"
    assert len(requested) == 2
    assert "service_name%3D%22order-tracker%22" in requested[0]
    assert "http_route%3D%22%2Fapi%2Forders%2F%7Border_id%7D%22" in requested[0]


def test_agent_reports_unavailable_without_configured_executable(monkeypatch):
    monkeypatch.setenv("AGENT_COMMAND", "python")
    monkeypatch.setattr(responder.shutil, "which", lambda command: None)

    result = responder._run_agent("Investigate this alert.")

    assert result["status"] == "unavailable"
    assert result["exit_code"] is None
    assert "not found" in result["stderr"]


def test_test_runner_uses_fixed_project_pytest_command(monkeypatch):
    calls = []
    monkeypatch.setenv("IR_APPROVAL_TOKEN", "test-secret")
    monkeypatch.setattr(
        responder.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or SimpleNamespace(
                returncode=0, stdout="8 passed", stderr=""
            )
        ),
    )

    result = responder._run_tests()

    command, options = calls[0]
    assert result["status"] == "passed"
    assert command[:5] == [
        "uv",
        "run",
        "--project",
        str(responder.REPOSITORY_ROOT),
        "--frozen",
    ]
    assert command[5:] == [
        "pytest",
        "-q",
        str(responder.REPOSITORY_ROOT / "tests"),
    ]
    assert options["shell"] is False
    assert options["cwd"] == responder.REPOSITORY_ROOT
    assert "IR_APPROVAL_TOKEN" not in options["env"]


def create_incident(client):
    response = client.post(
        "/alerts",
        json={"alertname": "local smoke test", "endpoint": "/api/orders/{order_id}"},
    )
    assert response.status_code == 202
    return response.json()["incident_id"]


def test_verification_resolves_only_after_tests_and_health_pass(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.setattr(responder, "_loki_logs", lambda *args: {"status": "no_data"})
    monkeypatch.setattr(
        responder, "_tempo_traces", lambda *args: {"status": "no_data", "traces": []}
    )
    monkeypatch.setattr(
        responder, "_run_tests", lambda: {"status": "passed", "exit_code": 0}
    )
    monkeypatch.setattr(
        responder, "_check_application_health", lambda: {"status": "ok"}
    )
    monkeypatch.setattr(responder, "APPROVAL_TOKEN", TEST_APPROVAL_TOKEN)
    with TestClient(responder.app) as client:
        incident_id = create_incident(client)
        response = client.post(
            f"/incidents/{incident_id}/verify",
            json={"approved": True, "remediation_summary": "Reviewed fix applied"},
            headers={"Authorization": f"Bearer {TEST_APPROVAL_TOKEN}"},
        )
        incident = client.get(f"/incidents/{incident_id}").json()

    assert response.status_code == 200
    assert response.json()["verified"] is True
    assert response.json()["resolution_status"] == "resolved"
    assert incident["resolution_status"] == "resolved"
    assert incident["resolved_at"]
    verification = json.loads(
        (tmp_path / incident_id / "verification.json").read_text(encoding="utf-8")
    )
    assert verification["tests"]["status"] == "passed"
    assert verification["health_check"]["status"] == "ok"


def test_verification_remains_unresolved_when_any_check_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.setattr(responder, "_loki_logs", lambda *args: {"status": "no_data"})
    monkeypatch.setattr(
        responder, "_tempo_traces", lambda *args: {"status": "no_data", "traces": []}
    )
    monkeypatch.setattr(
        responder, "_run_tests", lambda: {"status": "failed", "exit_code": 1}
    )
    monkeypatch.setattr(
        responder, "_check_application_health", lambda: {"status": "ok"}
    )
    monkeypatch.setattr(responder, "APPROVAL_TOKEN", TEST_APPROVAL_TOKEN)
    with TestClient(responder.app) as client:
        incident_id = create_incident(client)
        response = client.post(
            f"/incidents/{incident_id}/verify",
            json={"approved": True, "remediation_summary": "Reviewed fix applied"},
            headers={"Authorization": f"Bearer {TEST_APPROVAL_TOKEN}"},
        )
        incident = client.get(f"/incidents/{incident_id}").json()

    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert response.json()["resolution_status"] == "unresolved"
    assert incident["resolution_status"] == "unresolved"
    assert "resolved_at" not in incident


def test_verification_remains_unresolved_when_health_check_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.setattr(responder, "_loki_logs", lambda *args: {"status": "no_data"})
    monkeypatch.setattr(
        responder, "_tempo_traces", lambda *args: {"status": "no_data", "traces": []}
    )
    monkeypatch.setattr(
        responder, "_run_tests", lambda: {"status": "passed", "exit_code": 0}
    )
    monkeypatch.setattr(
        responder,
        "_check_application_health",
        lambda: {"status": "error", "error": {"message": "service unavailable"}},
    )
    monkeypatch.setattr(responder, "APPROVAL_TOKEN", TEST_APPROVAL_TOKEN)
    with TestClient(responder.app) as client:
        incident_id = create_incident(client)
        response = client.post(
            f"/incidents/{incident_id}/verify",
            json={"approved": True, "remediation_summary": "Reviewed fix applied"},
            headers={"Authorization": f"Bearer {TEST_APPROVAL_TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert response.json()["resolution_status"] == "unresolved"
    assert response.json()["health_status"] == "error"


def test_verification_requires_a_valid_approval_token(tmp_path, monkeypatch):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.setattr(responder, "APPROVAL_TOKEN", TEST_APPROVAL_TOKEN)
    monkeypatch.setattr(responder, "_loki_logs", lambda *args: {"status": "no_data"})
    monkeypatch.setattr(
        responder, "_tempo_traces", lambda *args: {"status": "no_data", "traces": []}
    )
    with TestClient(responder.app) as client:
        incident_id = create_incident(client)
        response = client.post(
            f"/incidents/{incident_id}/verify",
            json={"approved": True, "remediation_summary": "Reviewed fix applied"},
            headers={"Authorization": "Bearer incorrect-token"},
        )

    assert response.status_code == 403
    assert not (tmp_path / incident_id / "verification.json").exists()


def test_verification_requires_explicit_human_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.setattr(responder, "_loki_logs", lambda *args: {"status": "no_data"})
    monkeypatch.setattr(
        responder, "_tempo_traces", lambda *args: {"status": "no_data", "traces": []}
    )
    with TestClient(responder.app) as client:
        incident_id = create_incident(client)
        response = client.post(
            f"/incidents/{incident_id}/verify",
            json={"approved": False, "remediation_summary": "Not approved"},
        )

    assert response.status_code == 409
    assert not (tmp_path / incident_id / "verification.json").exists()
