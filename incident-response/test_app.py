import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as responder

TEST_APPROVAL_TOKEN = "test-approval-token-012345678901234"
TEST_COPILOT_TOKEN = "test-copilot-token-never-persist"
SAMPLE_DIAGNOSIS = {
    "likely_cause": "Express-order date parsing failed.",
    "uncertainty": "The captured evidence does not establish how the timestamp became invalid.",
    "proposed_remediation": "Validate the timestamp and return a controlled API error.",
    "evidence": ["The lookup returned HTTP 500.", "The trace marks the order lookup as failed."],
    "suggested_patch": "Handle invalid timestamps at the order-detail boundary.",
}


def stub_incident_evidence(monkeypatch):
    monkeypatch.setattr(
        responder,
        "_loki_logs",
        lambda route, start, end: {"status": "ok", "data": {"data": {"result": []}}},
    )
    monkeypatch.setattr(
        responder,
        "_tempo_traces",
        lambda route, start, end, logs: {"status": "ok", "traces": []},
    )


def configure_copilot(monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", TEST_COPILOT_TOKEN)
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(
        responder.shutil,
        "which",
        lambda command, path=None: "/usr/local/bin/copilot",
    )


def test_health_endpoint():
    with TestClient(responder.app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_alert_is_saved_with_external_evidence_and_agent_status(tmp_path, monkeypatch):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
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
    assert agent["error"] == "COPILOT_GITHUB_TOKEN is not configured"
    assert "Inspect only the available application source" in (
        incident_dir / "agent-prompt.txt"
    ).read_text(encoding="utf-8")
    assert "Never edit files, run commands, fetch URLs" in (
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


def test_structured_diagnosis_is_persisted_with_restricted_cli_and_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    stub_incident_evidence(monkeypatch)
    configure_copilot(monkeypatch)
    monkeypatch.setenv("IR_APPROVAL_TOKEN", TEST_APPROVAL_TOKEN)
    monkeypatch.setenv("DATABASE_PASSWORD", "unrelated-secret")
    monkeypatch.setenv("GH_TOKEN", "another-secret")
    monkeypatch.setenv("PATH", "host-path-must-not-pass")
    calls = []
    copilot_home = []

    def run_cli(command, **options):
        calls.append((command, options))
        copilot_home.append(options["env"]["COPILOT_HOME"])
        assert os.path.isdir(options["env"]["COPILOT_HOME"])
        assert list(Path(options["env"]["COPILOT_HOME"]).iterdir()) == []
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(SAMPLE_DIAGNOSIS),
            stderr="",
        )

    monkeypatch.setattr(responder.subprocess, "run", run_cli)
    with TestClient(responder.app) as client:
        response = client.post(
            "/alerts",
            json={"alertname": "local smoke test", "endpoint": "/api/orders/{order_id}"},
        )

    assert response.status_code == 202
    result = response.json()
    incident_dir = Path(result["incident_path"])
    agent = json.loads((incident_dir / "agent-output.json").read_text(encoding="utf-8"))
    incident = json.loads((incident_dir / "incident.json").read_text(encoding="utf-8"))
    command, options = calls[0]
    child_env = options["env"]

    assert result["agent_status"] == "completed"
    assert agent["diagnosis"] == SAMPLE_DIAGNOSIS
    assert agent["status"] == "completed"
    assert (incident_dir / "agent-response.txt").read_text(encoding="utf-8") == json.dumps(
        SAMPLE_DIAGNOSIS
    )
    assert incident["resolution_status"] == "investigating"
    assert options["shell"] is False
    assert options["cwd"] == responder.REPOSITORY_ROOT / "app"
    assert command[0] == "/usr/local/bin/copilot"
    assert "--prompt" in command
    assert "--available-tools=view,grep,glob" in command
    assert "--allow-tool=read" in command
    assert "--deny-tool=shell,write,url,github" in command
    assert f"--add-dir={responder.REPOSITORY_ROOT / 'tests'}" in command
    assert "--disallow-temp-dir" in command
    assert "--disable-builtin-mcps" in command
    assert "--no-custom-instructions" in command
    assert "--dynamic-retrieval=skills=off" in command
    assert sum(argument.startswith("--add-dir=") for argument in command) == 1
    assert not any(
        flag in command
        for flag in (
            "--allow-all",
            "--allow-all-tools",
            "--allow-all-paths",
            "--allow-all-urls",
            "--fleet",
            "--autopilot",
        )
    )
    assert set(child_env) == {
        "PATH",
        "HOME",
        "COPILOT_HOME",
        "COPILOT_GITHUB_TOKEN",
        "COPILOT_AUTO_UPDATE",
        "GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS",
        "LANG",
    }
    assert child_env["COPILOT_GITHUB_TOKEN"] == TEST_COPILOT_TOKEN
    assert child_env["PATH"] == responder.COPILOT_PATH
    assert child_env["COPILOT_HOME"] == child_env["HOME"]
    assert child_env["COPILOT_AUTO_UPDATE"] == "false"
    assert child_env["GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS"] == "false"
    assert not os.path.exists(copilot_home[0])
    assert "IR_APPROVAL_TOKEN" not in child_env
    assert "DATABASE_PASSWORD" not in child_env
    assert "GH_TOKEN" not in child_env
    assert TEST_COPILOT_TOKEN not in (incident_dir / "agent-output.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("malformed_json", "malformed_output"),
        ("deeply_nested_json", "malformed_output"),
        ("invalid_encoding", "malformed_output"),
        ("oversized_output", "malformed_output"),
        ("missing_fields", "malformed_output"),
        ("missing_token", "unavailable"),
        ("missing_executable", "unavailable"),
        ("nonzero_exit", "failed"),
        ("timeout", "timeout"),
        ("invalid_configuration", "configuration_error"),
    ],
)
def test_agent_failures_are_accepted_and_leave_incident_investigating(
    tmp_path, monkeypatch, failure, expected_status
):
    monkeypatch.setattr(responder, "INCIDENT_ROOT", tmp_path)
    stub_incident_evidence(monkeypatch)
    configure_copilot(monkeypatch)
    monkeypatch.setattr(
        responder.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Copilot subprocess must not run"),
    )
    if failure == "missing_token":
        monkeypatch.delenv("COPILOT_GITHUB_TOKEN")
    elif failure == "missing_executable":
        monkeypatch.setattr(responder.shutil, "which", lambda command, path=None: None)
    elif failure == "invalid_configuration":
        monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "not-an-integer")
    else:
        def run_cli(command, **options):
            if failure == "malformed_json":
                return SimpleNamespace(returncode=0, stdout="not JSON", stderr="")
            if failure == "deeply_nested_json":
                nested_json = "[" * 5_000 + "0" + "]" * 5_000
                return SimpleNamespace(returncode=0, stdout=nested_json, stderr="")
            if failure == "invalid_encoding":
                return SimpleNamespace(returncode=0, stdout=b"\xff", stderr=b"")
            if failure == "oversized_output":
                return SimpleNamespace(
                    returncode=0,
                    stdout="x" * (responder.MAX_AGENT_RESPONSE_BYTES * 2),
                    stderr="",
                )
            if failure == "missing_fields":
                return SimpleNamespace(returncode=0, stdout='{"likely_cause":"x"}', stderr="")
            if failure == "nonzero_exit":
                return SimpleNamespace(
                    returncode=2,
                    stdout=f"partial {TEST_COPILOT_TOKEN}",
                    stderr=f"error {TEST_COPILOT_TOKEN}",
                )
            if failure == "timeout":
                raise subprocess.TimeoutExpired(
                    command,
                    5,
                    output=f"partial {TEST_COPILOT_TOKEN}",
                    stderr="timed out",
                )
            raise AssertionError(f"Unexpected failure case: {failure}")

        monkeypatch.setattr(responder.subprocess, "run", run_cli)

    with TestClient(responder.app) as client:
        response = client.post(
            "/alerts",
            json={"alertname": "local smoke test", "endpoint": "/api/orders/{order_id}"},
        )

    assert response.status_code == 202
    result = response.json()
    incident_dir = Path(result["incident_path"])
    agent = json.loads((incident_dir / "agent-output.json").read_text(encoding="utf-8"))
    incident = json.loads((incident_dir / "incident.json").read_text(encoding="utf-8"))

    assert result["agent_status"] == expected_status
    assert agent["status"] == expected_status
    assert agent["diagnosis"] is None
    assert incident["resolution_status"] == "investigating"
    assert (incident_dir / "agent-response.txt").stat().st_size <= (
        responder.MAX_AGENT_RESPONSE_BYTES
    )
    persisted = (incident_dir / "agent-output.json").read_text(encoding="utf-8")
    raw_response = (incident_dir / "agent-response.txt").read_text(encoding="utf-8")
    assert TEST_COPILOT_TOKEN not in persisted
    assert TEST_COPILOT_TOKEN not in raw_response


def test_test_runner_uses_fixed_project_pytest_command(monkeypatch):
    calls = []
    runtime_environment = {
        "PATH": "test-path",
        "HOME": "test-home",
        "TMPDIR": "test-temp",
        "LANG": "C.UTF-8",
    }
    for name, value in runtime_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("IR_APPROVAL_TOKEN", "approval-secret")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", TEST_COPILOT_TOKEN)
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-pass")
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
    assert options["env"] == runtime_environment
    assert not {
        "IR_APPROVAL_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    }.intersection(options["env"])


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
