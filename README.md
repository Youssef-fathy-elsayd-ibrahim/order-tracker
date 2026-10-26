# Order Tracker

A small order tracking app for the AI Dev Tools Zoomcamp observability homework. It includes a web page, API, tests, and a Docker Compose setup. Homework 4 adds observability and incident response in stages.

The main user flow is creating an order and checking its status. Three sample orders are created on first startup.

## Run it

You need Docker with Compose. To run the tests, you also need Python 3.11+ and `uv`.

```bash
docker compose up --build -d --wait
```

Open <http://127.0.0.1:8000>. The API is at `/api/orders`, and the health check is at `/healthz`. Data is stored in a Docker volume and survives container recreation.

Order lookup requests to `/api/orders/{order_id}` emit OpenTelemetry metrics, logs, and traces to the app's standard output and to the local observability stack.

## Observability

Start the complete application and observability stack with:

```bash
docker compose up --build -d --wait
```

The OpenTelemetry Collector receives OTLP telemetry from the app and routes metrics to Prometheus, structured logs to Loki, and traces to Tempo. Grafana is provisioned with all three datasources and the **Order Tracker - Requests** dashboard.

- Grafana: <http://localhost:3000> (local default login: `admin` / `admin`)
- Dashboard: **Order Tracker / Order Tracker - Requests**
- Prometheus: <http://localhost:9090>
- Loki: <http://localhost:3100>
- Tempo: <http://localhost:3200>

Generate a lookup to populate the dashboard and telemetry stores:

```bash
curl -i http://localhost:8000/api/orders/standard-1001
```

The same telemetry remains available in the container logs with `docker compose logs app` and `docker compose logs otel-collector`.

Grafana also provisions **Order Tracker - 5xx Errors**, which evaluates order lookup `5xx` responses over a five-minute window every 10 seconds. With no matching 5xx series, the query has no data and Grafana maps that state to **Normal**; query execution errors remain **Error**. Inspect the rule under **Alerting / Alert rules**. Firing notifications are routed to the incident responder; resolved notifications are disabled.

## Verified end-to-end observability validation

These are two separate, previously observed validations:

- **October 26, 2026 — normal-request telemetry:** all seven Compose services started; `/healthz` returned `{"status":"ok"}`, `/api/orders` returned existing orders, and a normal order lookup returned HTTP 200. Prometheus scraped `otel-collector:9464` with target health `up`. For the same `order_lookup_requests_total` series, the value increased from `1` to `2` after a successful lookup and an 8-second wait. Loki stored the successful lookup log, and Tempo stored its corresponding `order lookup` trace for service `order-tracker`. Both shared trace ID `999f2de032e07a4868088ce3468aa5f8`; the reported span duration was 27 ms. Grafana reported the 5xx rule health as `ok` and its observed state as **Normal (NoData)**; no alert was firing during this validation.
- **October 22, 2026 — failure-to-incident demonstration:** two requests to `/api/orders/express-1002` returned HTTP 500; the Grafana alert fired, Grafana's webhook reached the responder with HTTP 202, and incident `20261022T174540Z-4b828403-1eb1-46c8-8b44-6f37fff270ef` was persisted with firing status and Loki/Tempo evidence. The incident remains `status=firing` and `resolution_status=investigating`; preserve it and do not call `/verify` as part of this telemetry check. The incident's agent output was `unavailable` because no assistant CLI was configured. AI diagnosis is therefore not demonstrated as working.

The commands below are read-only except for one successful GET lookup, which emits normal telemetry. They do not create alerts or incidents.

### Check the app and Prometheus

Run from PowerShell with Compose already running:

```powershell
Invoke-RestMethod http://localhost:8000/healthz
Invoke-RestMethod http://localhost:8000/api/orders

$query = 'order_lookup_requests_total{http_route="/api/orders/{order_id}",http_response_status_code="200"}'
$encodedQuery = [uri]::EscapeDataString($query)
$before = Invoke-RestMethod "http://localhost:9090/api/v1/query?query=$encodedQuery"
$before.data.result

Invoke-RestMethod http://localhost:8000/api/orders/standard-1001
Start-Sleep -Seconds 8
$after = Invoke-RestMethod "http://localhost:9090/api/v1/query?query=$encodedQuery"
$after.data.result

(Invoke-RestMethod http://localhost:9090/api/v1/targets).data.activeTargets |
  Select-Object scrapeUrl, health, lastScrape, lastError
```

Compare values for the same `exported_instance` and labels; do not assume a fixed starting counter value.

### Check Loki and Tempo

These selectors match the labels and span attributes used by the configured collector and responder. Loki uses `service_name` and `http_route`; Tempo uses `resource.service.name` and `span.http.route`.

```powershell
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$lokiQuery = '{service_name="order-tracker"} | http_route="/api/orders/{order_id}"'
$lokiParams = @{
  query = $lokiQuery
  start = [long]($now - 300) * 1000000000
  end = [long]$now * 1000000000
  limit = 100
  direction = 'backward'
}
$lokiUri = 'http://localhost:3100/loki/api/v1/query_range?' +
  (($lokiParams.GetEnumerator() | ForEach-Object {
    [uri]::EscapeDataString($_.Key) + '=' +
      [uri]::EscapeDataString([string]$_.Value)
  }) -join '&')
(Invoke-RestMethod $lokiUri).data.result

$tempoQuery = '{ resource.service.name = "order-tracker" && span.http.route = "/api/orders/{order_id}" }'
$tempoParams = @{
  q = $tempoQuery
  start = $now - 300
  end = $now
  limit = 20
}
$tempoUri = 'http://localhost:3200/api/search?' +
  (($tempoParams.GetEnumerator() | ForEach-Object {
    [uri]::EscapeDataString($_.Key) + '=' +
      [uri]::EscapeDataString([string]$_.Value)
  }) -join '&')
$traces = (Invoke-RestMethod $tempoUri).traces
$traces | Select-Object traceID, rootServiceName, rootTraceName, durationMs
```

To inspect a returned trace by ID, use `Invoke-RestMethod "http://localhost:3200/api/traces/<traceID>"`. Loki's `/ready` and Tempo's `/ready` are also available for readiness checks.

### Check Grafana's provisioned alert and webhook

The contact point URL is `http://incident-response:8001/alerts`; it uses the Compose service name, not a host port. The provisioned root notification policy routes to contact point `incident-responder`. These API calls use the local Compose default credentials shown above; do not put real credentials in shared command history.

```powershell
$auth = [Convert]::ToBase64String(
  [Text.Encoding]::ASCII.GetBytes('admin:admin')
)
$headers = @{ Authorization = "Basic $auth" }

(Invoke-RestMethod 'http://localhost:3000/api/v1/provisioning/contact-points' `
  -Headers $headers) |
  Where-Object { $_.name -eq 'incident-responder' }
Invoke-RestMethod 'http://localhost:3000/api/v1/provisioning/policies' `
  -Headers $headers

$rules = Invoke-RestMethod `
  'http://localhost:3000/api/prometheus/grafana/api/v1/rules' `
  -Headers $headers
$rules.data.groups.rules

Invoke-RestMethod `
  'http://localhost:3000/api/alertmanager/grafana/api/v2/alerts' `
  -Headers $headers
```

The last endpoint shows current active alerts; an empty result means none are firing at query time. The provisioned rule is **Order Tracker - 5xx Errors**, with 10-second evaluation, a five-minute 5xx query window, and `noDataState: OK`. Its currently observed **Normal (NoData)** state is not an alert.

## Incident response

The local incident-response API listens on port `8001`. Grafana sends firing alerts to `POST http://incident-response:8001/alerts`. The responder saves incident details and the collected Loki logs/Tempo traces under `incident-response/incidents/`, and prepares a prompt for a headless coding assistant. Runtime incident artifacts are ignored by Git. A missing or failing assistant is recorded as such; an alert is not treated as resolved.

Set `IR_APPROVAL_TOKEN` in the environment before starting Compose; use a random secret of at least 32 characters and do not commit it. For example, in PowerShell:

```powershell
$env:IR_APPROVAL_TOKEN = "<at-least-32-random-characters>"
docker compose up --build -d --wait
```

The assistant is instructed to be read-only; application and test mounts are read-only, but the configured assistant CLI must also be set to its no-edit/no-tools mode because a prompt is not a sandbox. Review and apply any patch yourself, then rebuild/restart the application before asking the responder to verify recovery:

```bash
docker compose up --build -d --wait app
```

Verification runs `pytest -q /workspace/tests` in the responder environment and checks `http://app:8000/healthz`. Both must pass to set `resolution_status` to `resolved`. Otherwise the incident remains `unresolved`; test output and health evidence are saved in `verification.json`. Verification requires both `"approved": true` and the configured bearer token; keep the responder bound to localhost as configured.

Use the following PowerShell request after setting the incident ID:

```powershell
$incidentId = "<incident_id>"
$body = @{
  approved = $true
  remediation_summary = "Reviewed and applied the fix in the application source"
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8001/incidents/$incidentId/verify" `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer $env:IR_APPROVAL_TOKEN" } `
  -Body $body
```

To smoke-test alert collection, POST an alert payload:

```bash
curl -X POST http://localhost:8001/alerts \
  -H "Content-Type: application/json" \
  -d "{\"alertname\":\"Local smoke test\",\"status\":\"firing\",\"endpoint\":\"/api/orders/{order_id}\",\"description\":\"Local responder smoke test\"}"
```

An assistant executable installed inside the responder image can be configured with trusted `AGENT_COMMAND` and optional `AGENT_ARGS_JSON` environment settings; the latter is a JSON argument array and supports `{prompt}` as an argument placeholder. Arguments are executed without a shell. No coding-assistant CLI is bundled in the responder image; when none is configured, the incident is still saved and the agent status records that it was unavailable.

If port 8000 is occupied, set `ORDER_TRACKER_PORT`, for example:

```bash
ORDER_TRACKER_PORT=18080 docker compose up --build -d --wait
```

Run the application tests with `uv run --frozen pytest -q`, and the incident-responder tests with `uv run --directory incident-response --frozen pytest -q`. CI runs both test suites on pull requests and pushes to the default branch.

Stop the app with `docker compose down`. Add `-v` only if you also want to delete the order data.

## Deployment and rollback

The repository provides a local Docker Compose deployment; it does not currently
publish a public deployment. Build and start the stack with the command above.
For a local rollback, restore the last known-good source revision, then rebuild
and restart the affected service:

```bash
git switch --detach <known-good-commit>
docker compose up --build -d --wait
```

Return to your development branch after confirming recovery. Preserve the order
database volume during rollback; do not add `-v`.

See [AGENTS.md](./AGENTS.md) and [the agent workflow guide](./docs/agent-workflow.md)
for responsibilities and incident handoffs.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Web page |
| GET | `/healthz` | Database health check |
| GET | `/api/orders` | List orders |
| POST | `/api/orders` | Create an order |
| GET | `/api/orders/{id}` | Check an order |
| PATCH | `/api/orders/{id}` | Change an order status |

The app uses SQLite to keep setup small. Run one app container at a time. The course exercise is about detecting and handling an incident, not scaling the database.
