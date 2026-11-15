# Order Tracker

A small order tracking app for the AI Dev Tools Zoomcamp observability homework. It includes a web page, API, tests, and a Docker Compose setup. Homework 4 adds observability and incident response in stages.

The main user flow is creating an order and checking its status. Three sample orders are created on first startup.

## Run it

You need Docker with Compose. To run the Python tests, you also need Python 3.11+
and `uv`.

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

### Direct webhook smoke test (not end-to-end)

This sends an alert directly to the responder and bypasses Grafana's alert rule;
it does not demonstrate the Grafana-to-responder flow below.

```bash
curl -X POST http://localhost:8001/alerts \
  -H "Content-Type: application/json" \
  -d "{\"alertname\":\"Local smoke test\",\"status\":\"firing\",\"endpoint\":\"/api/orders/{order_id}\",\"description\":\"Local responder smoke test\"}"
```

### Reproducible Grafana-to-responder demonstration (isolated)

This is the end-to-end demonstration: Grafana evaluates the app's `5xx` metric
and sends a firing alert to the responder. It is separate from the direct
webhook smoke test above, which bypasses Grafana, and from the previously
recorded October 22, 2026 incident. This run creates a new incident in its own
named volume and does not access or overwrite `incident-response/incidents/`.

Run from the repository root in PowerShell. The demo uses the same host ports as
the normal stack, so first stop that stack without removing its containers or
volumes. The different Compose project name also gives the demo its own app
database and observability volumes.

```powershell
$demoCompose = @('-p', 'order-tracker-demo', '-f', 'compose.yaml', '-f', 'compose.demo.yaml')
$demoConfig = docker compose @demoCompose config --format json | ConvertFrom-Json
$incidentMount = @($demoConfig.services.'incident-response'.volumes |
  Where-Object { $_.target -eq '/incidents' })
if ($incidentMount.Count -ne 1 -or $incidentMount[0].type -ne 'volume') {
  throw 'Demo responder must use a named volume at /incidents, not a host bind mount'
}
$incidentMount | Select-Object type, source, target
$demoConfig.volumes.PSObject.Properties |
  Select-Object Name, @{Name='docker_volume';Expression={$_.Value.name}}

docker compose stop
docker compose @demoCompose up -d --wait
```

The config check should show a `volume` mount at `/incidents` with source
`demo-incidents`, not the repository's incident directory. The top-level volume
configuration shows its Docker name as `order-tracker-demo_demo-incidents`;
similarly, app and observability volumes are prefixed with
`order-tracker-demo_`. The regular stack is stopped, not removed.

The app's metric exporter runs every 5 seconds; [Prometheus](./prometheus.yml)
scrapes every 2 seconds and evaluates every 5 seconds. The Grafana alert group
evaluates every 10 seconds and looks back 5 minutes. The following controlled
failure changes only the seeded `express-1002` timestamp in the isolated demo
database. Save that timestamp and the existing demo incident IDs first; the
`finally` block restores the timestamp even if a check fails.

```powershell
$originalTimestamp = (Invoke-RestMethod 'http://localhost:8000/api/orders/express-1002').created_at
if (-not $originalTimestamp) { throw 'Could not save the demo order timestamp' }
$beforeIncidentIds = @(
  docker compose @demoCompose exec -T incident-response python -c "from pathlib import Path; print('\n'.join(sorted(p.name for p in Path('/incidents').iterdir() if p.is_dir())))"
)

try {
  docker compose @demoCompose exec -T app python -c "import sqlite3; db=sqlite3.connect('/data/orders.db'); db.execute('UPDATE orders SET created_at=? WHERE id=?', ('not-a-timestamp','express-1002')); db.commit(); db.close()"
  if ($LASTEXITCODE -ne 0) { throw 'Could not inject the isolated demo failure' }

  foreach ($attempt in 1..2) {
    $status = $null
    try {
      $response = Invoke-WebRequest -UseBasicParsing 'http://localhost:8000/api/orders/express-1002'
      $status = [int]$response.StatusCode
    } catch {
      if ($_.Exception.Response) {
        $status = [int]$_.Exception.Response.StatusCode
      } else {
        throw
      }
    }
    if ($status -ne 500) { throw "Expected the controlled lookup to return 500; got $status" }
    "Expected demo failure: HTTP $status"
    if ($attempt -eq 1) { Start-Sleep -Seconds 3 }
  }

  # Allow export, scrape, Grafana evaluation, and notification dispatch to complete.
  Start-Sleep -Seconds 60

  $auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes('admin:admin'))
  $headers = @{ Authorization = "Basic $auth" }
  $activeAlerts = Invoke-RestMethod `
    'http://localhost:3000/api/alertmanager/grafana/api/v2/alerts' -Headers $headers
  $activeAlerts |
    Where-Object { $_.labels.alertname -eq 'Order Tracker - 5xx Errors' } |
    Select-Object @{Name='alertname';Expression={$_.labels.alertname}},
      @{Name='state';Expression={$_.status.state}}
  # Also check Grafana: Alerting > Alert rules > Order Tracker - 5xx Errors (Firing).

  docker compose @demoCompose logs --since 10m incident-response |
    Select-String 'POST /alerts.*202'

  $afterIncidentIds = @(
    docker compose @demoCompose exec -T incident-response python -c "from pathlib import Path; print('\n'.join(sorted(p.name for p in Path('/incidents').iterdir() if p.is_dir())))"
  )
  $newIncidentIds = @($afterIncidentIds | Where-Object { $_ -and $_ -notin $beforeIncidentIds })
  if ($newIncidentIds.Count -eq 0) { throw 'No new incident was persisted by the demo responder' }
  $incidentId = ($newIncidentIds | Sort-Object | Select-Object -Last 1).Trim()

  $incident = Invoke-RestMethod "http://localhost:8001/incidents/$incidentId"
  $incident | Select-Object incident_id, status, resolution_status, alert_name, http_status_code |
    Format-List
  if ($incident.resolution_status -ne 'investigating') {
    throw "Expected resolution_status=investigating, got $($incident.resolution_status)"
  }

  $evidenceCommand = "from pathlib import Path; import sys; d=Path('/incidents')/sys.argv[1]; [print('--- '+n+' ---\n'+(d/n).read_text(encoding='utf-8')) for n in ('incident.json','logs.json','traces.json')]"
  docker compose @demoCompose exec -T incident-response python -c $evidenceCommand $incidentId
} finally {
  docker compose @demoCompose exec -T app python -c "import sqlite3,sys; db=sqlite3.connect('/data/orders.db'); db.execute('UPDATE orders SET created_at=? WHERE id=?',(sys.argv[1],'express-1002')); db.commit(); db.close()" "$originalTimestamp"
  if ($LASTEXITCODE -ne 0) { throw 'Failed to restore the demo order timestamp' }
  $restoredTimestamp = (Invoke-RestMethod 'http://localhost:8000/api/orders/express-1002').created_at
  if ($restoredTimestamp -ne $originalTimestamp) { throw 'Demo order timestamp was not restored' }
}
```

The responder access log should show `POST /alerts` with `202`; the newly
persisted `incident.json` should have `resolution_status: investigating`, and
the same incident directory contains `logs.json` and `traces.json`.
**Do not call `/verify` during this demonstration.** A demo incident volume may
retain evidence between runs; compare IDs against the pre-run list as above.

When finished, stop the demo without deleting its containers or volumes, then
restart the original stack:

```powershell
docker compose @demoCompose stop
docker compose up -d --wait
```

An assistant executable installed inside the responder image can be configured with trusted `AGENT_COMMAND` and optional `AGENT_ARGS_JSON` environment settings; the latter is a JSON argument array and supports `{prompt}` as an argument placeholder. Arguments are executed without a shell. No coding-assistant CLI is bundled in the responder image; when none is configured, the incident is still saved and the agent status records that it was unavailable.

If port 8000 is occupied, set `ORDER_TRACKER_PORT`, for example:

```bash
ORDER_TRACKER_PORT=18080 docker compose up --build -d --wait
```

Run the application tests with `uv run --frozen pytest -q`, and the incident-responder tests with `uv run --directory incident-response --frozen pytest -q`. CI runs both test suites on pull requests and pushes to the default branch.

### Browser tests

Browser tests use Python Playwright and headless Chromium. Install the project
dependencies and browser once, then run the separate browser suite in
PowerShell:

```powershell
uv sync --frozen
uv run --frozen playwright install chromium
uv run --frozen pytest -q browser-tests
```

Each browser test starts its own local FastAPI process with a SQLite database
under pytest's temporary directory and stops that process afterward. The tests
do not use the development database or Docker volumes. CI runs them in a
separate `browser-tests` job, installs Chromium, and uses the same temporary
database fixture.

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

`docs/api/openapi.json` is the checked-in API contract generated from the
running FastAPI application's OpenAPI schema. The automated drift test compares
it with `app.openapi()` so API changes require an intentional snapshot update;
this snapshot documents the current API and is not a claim that development was
historically contract-first. After reviewing an intentional API change,
regenerate it with:

```powershell
uv run --frozen python scripts/export_openapi.py
uv run --frozen pytest -q tests/test_openapi_contract.py
```

The app uses SQLite to keep setup small. Run one app container at a time. The course exercise is about detecting and handling an incident, not scaling the database.
