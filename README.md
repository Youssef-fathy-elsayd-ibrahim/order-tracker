# Order Tracker

Order Tracker is a local order-management and observability exercise for the AI
Dev Tools Zoomcamp. It provides a FastAPI app with a lightweight static UI,
SQLite persistence, and a Docker Compose stack for metrics, logs, traces, and
incident response. It has no confirmed public deployment.

See the [product requirements](docs/product/requirements.md) for verified scope
and acceptance criteria, the [agent workflow](docs/agent-workflow.md) for
collaboration and incident-safety rules, and the
[local operation guide](docs/operations/operation-guide.md) for detailed
PowerShell examples and isolated demonstrations.

## Run locally

Prerequisites: Docker Desktop with Docker Compose v2. The app is served only
on the local machine by default.

```powershell
docker compose up --build -d --wait
docker compose ps
```

Open the UI at <http://127.0.0.1:8000>. The API and interactive docs are at
<http://127.0.0.1:8000/api/orders> and <http://127.0.0.1:8000/docs>. Three
sample orders are seeded only when the SQLite database is empty. The page can
create orders and check their status; status updates are API-only.

The order database and observability data use Docker named volumes and survive
container restarts. Stop or restart the stack without deleting data:

```powershell
docker compose stop
docker compose start
```

Do not use `docker compose down -v` or volume-pruning commands for routine
operations.

## Observability and incident response

The OpenTelemetry Collector routes application metrics to Prometheus, logs to
Loki, and traces to Tempo. Grafana is provisioned with the
**Order Tracker / Order Tracker - Requests** dashboard and a 5xx alert routed
to the incident responder. Local endpoints:

- Grafana: <http://127.0.0.1:3000>
- Prometheus: <http://127.0.0.1:9090>
- Loki: <http://127.0.0.1:3100>
- Tempo: <http://127.0.0.1:3200>
- Incident responder: <http://127.0.0.1:8001>

AI diagnosis is optional and read-only. The incident-response image contains
the official Copilot CLI pinned to v1.0.112; configure a user-owned
fine-grained GitHub PAT with the **Copilot Requests** permission in the ignored
local `.env` as `COPILOT_GITHUB_TOKEN`. Classic PATs are not supported. Never
put credentials in source control or chat. Alerts are still accepted when the
provider is unavailable. Diagnosis proposes remediation only; a human must
review and apply any patch. Existing operator approval, passing tests, and app
health-check gates remain mandatory before an incident can be resolved.

The optional Windows smoke-test launcher uses a distinct Compose project,
isolated volumes, and requires explicit confirmation before sending a real
Copilot request:

```powershell
.\scripts\run-copilot-smoke-test.bat
```

No authenticated Copilot request is claimed by this documentation. The
[operation guide](docs/operations/operation-guide.md) distinguishes this
optional real-provider smoke test from the direct webhook smoke test and the
isolated Grafana-to-responder demonstration. It also records the historical
October 22 and October 26, 2026 validations; those records are not a current
CI result or evidence of public hosting.

## Tests and API contract

Install the locked development dependencies and browser once, then run the
main, responder, and isolated Playwright test suites:

```powershell
uv sync --frozen
uv run --frozen pytest -q
uv run --directory incident-response --frozen pytest -q
uv run --frozen playwright install chromium
uv run --frozen pytest -q browser-tests
```

The browser suite uses a temporary SQLite database, separate from the
development database and Docker volumes. CI runs both Python suites, the
browser suite with headless Chromium, and the OpenAPI drift test. Tests mock
Copilot subprocesses and do not consume Copilot requests.

The checked-in API contract is [docs/api/openapi.json](docs/api/openapi.json).
After an intentional API change, regenerate and verify it:

```powershell
uv run --frozen python scripts/export_openapi.py
uv run --frozen pytest -q tests/test_openapi_contract.py
```

## Project context

- [Formal product requirements](docs/product/requirements.md)
- [Local operation and usage guide](docs/operations/operation-guide.md)
- [Agent workflow and roles](docs/agent-workflow.md)
- [Agent entry point](AGENTS.md)
