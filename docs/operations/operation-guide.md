# Local operation and usage guide

This guide describes the current local Docker Compose application. It does not
describe a public deployment. See the [product requirements](../product/requirements.md)
for the product scope and the [agent workflow](../agent-workflow.md) for
incident handoff and approval rules.

## Prerequisites and configuration

- Docker Desktop with Docker Compose v2.
- PowerShell 5.1 or later for the Windows examples below.
- `uv` and Python 3.11+ to run the test suites locally.
- No Node.js or npm is needed; the UI is static HTML/JavaScript served by
  FastAPI.

The optional Copilot provider needs a user-owned fine-grained GitHub PAT with
the **Copilot Requests** permission. Classic PATs are not supported. Put the
token in a local ignored `.env` file; never paste it into chat or a command
line. The `.env.example` contains a blank placeholder:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set `COPILOT_GITHUB_TOKEN` in `.env` only if you intend to enable AI diagnosis.
The responder accepts alerts without it. `IR_APPROVAL_TOKEN` is a separate
secret, required only for human-approved incident verification; it must be at
least 32 characters. Neither value belongs in source control.

## Start, health, and stop

From the repository root:

```powershell
docker compose up --build -d --wait
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8001/healthz
```

The app and responder health checks should return `status=ok`. Host ports bind
to `127.0.0.1`; they are not public listeners by default.

| Service | Local address |
| --- | --- |
| Order Tracker UI and API | <http://127.0.0.1:8000> |
| FastAPI interactive API docs | <http://127.0.0.1:8000/docs> |
| Incident responder | <http://127.0.0.1:8001> |
| Grafana | <http://127.0.0.1:3000> |
| Prometheus | <http://127.0.0.1:9090> |
| Loki | <http://127.0.0.1:3100> |
| Tempo | <http://127.0.0.1:3200> |

Grafana's local default login is `admin` / `admin`; do not reuse it for a
public deployment. The OpenTelemetry Collector is internal to the Compose
network and does not have a host-published port.

Open the UI through its HTTP address. Opening `static/index.html` with
`file://` does not work because its API requests use `/api/...` paths.

Stop and restart without removing containers or data:

```powershell
docker compose stop
docker compose start
```

`docker compose down` removes the regular project's containers and network but
keeps its named volumes. Do not add `-v` for routine stopping or restarting.
The app database is `/data/orders.db` on the `orders` volume. Prometheus, Loki,
Tempo, and Grafana also have project-scoped named volumes.

Host port mappings can be changed with `ORDER_TRACKER_PORT`,
`INCIDENT_RESPONSE_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `LOKI_PORT`, and
`TEMPO_PORT`. The default network subnet is configurable through
`ORDER_TRACKER_SUBNET`.

## What users can do

The page lists seeded and saved orders. The create form has Customer, Item, and
Priority fields (`standard` or `express`) and a **Create order** button.
Successful creation makes a `received` order and refreshes the list. Each order
has a **Check status** button; it displays the current status and, for express
orders, the estimated delivery date. The page shows accessible errors for
failed list, create, or lookup requests and keeps failed form values available
for retry.

The current UI does **not** have a control to change an order's status. That
operation is API-only. User-provided text is escaped before it is rendered.

## API and operator endpoints

FastAPI's interactive documentation is at `/docs`; the generated schema is at
`/openapi.json`. The checked-in snapshot is
[`docs/api/openapi.json`](../api/openapi.json).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | App health and database connectivity. |
| GET | `/api/orders` | List orders, newest first. |
| POST | `/api/orders` | Create an order; returns HTTP 201. Customer length is 1–80, item length is 1–120, and priority is `standard` or `express`. |
| GET | `/api/orders/{order_id}` | Retrieve an order; returns 404 if missing. Express delivery estimate is derived from `created_at`. |
| PATCH | `/api/orders/{order_id}` | Set status to `received`, `preparing`, `shipped`, or `delivered`. This has no UI control. |
| GET | `/healthz` on responder | Responder health. |
| POST | `/alerts` on responder | Persist an alert and evidence; returns HTTP 202 even when diagnosis is unavailable or fails. |
| GET | `/incidents/{incident_id}` on responder | Read a persisted incident. There is no incident-list endpoint. |
| POST | `/incidents/{incident_id}/verify` on responder | Run the human-approved verification gates. |

An example API-only status update:

```powershell
$body = @{ status = 'preparing' } | ConvertTo-Json
Invoke-RestMethod -Method Patch `
  -Uri 'http://127.0.0.1:8000/api/orders/<order_id>' `
  -ContentType 'application/json' -Body $body
```

Order data persists across app restarts in SQLite. On first startup, three
sample orders are seeded only when the database is empty. The persistence test
uses a temporary database and does not access the development volume.

## Run tests

```powershell
uv sync --frozen
uv run --frozen pytest -q
uv run --directory incident-response --frozen pytest -q
uv run --frozen playwright install chromium
uv run --frozen pytest -q browser-tests
```

The API/OpenAPI and responder suites use their existing test fixtures; browser
tests start a separate local app with a temporary SQLite database. None of
these tests make a Copilot request. CI runs `tests` and `browser-tests` jobs on
pull requests and pushes to `main` or `master`.

After an intentional API change, regenerate and check the API snapshot:

```powershell
uv run --frozen python scripts/export_openapi.py
uv run --frozen pytest -q tests/test_openapi_contract.py
```

## Inspect observability

Generate a normal lookup, which emits the counter, structured log, and span:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/orders/standard-1001
```

Prometheus scrapes the Collector every 2 seconds; its global evaluation
interval is 5 seconds. The application exports every 5 seconds. Query the
Prometheus counter for successful lookups:

```powershell
$query = 'order_lookup_requests_total{http_route="/api/orders/{order_id}",http_response_status_code="200"}'
$encodedQuery = [uri]::EscapeDataString($query)
Invoke-RestMethod "http://127.0.0.1:9090/api/v1/query?query=$encodedQuery"
```

In Grafana, open **Order Tracker / Order Tracker - Requests**. The provisioned
**Order Tracker - 5xx Errors** rule evaluates every 10 seconds over a 5-minute
window. No matching 5xx data maps to Normal; it is not itself an alert.

Query Loki for the application lookup log:

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
$lokiUri = 'http://127.0.0.1:3100/loki/api/v1/query_range?' +
  (($lokiParams.GetEnumerator() | ForEach-Object {
    [uri]::EscapeDataString($_.Key) + '=' +
      [uri]::EscapeDataString([string]$_.Value)
  }) -join '&')
(Invoke-RestMethod $lokiUri).data.result
```

Search Tempo for the corresponding trace:

```powershell
$tempoQuery = '{ resource.service.name = "order-tracker" && span.http.route = "/api/orders/{order_id}" }'
$tempoParams = @{ q = $tempoQuery; start = $now - 300; end = $now; limit = 20 }
$tempoUri = 'http://127.0.0.1:3200/api/search?' +
  (($tempoParams.GetEnumerator() | ForEach-Object {
    [uri]::EscapeDataString($_.Key) + '=' +
      [uri]::EscapeDataString([string]$_.Value)
  }) -join '&')
$traces = (Invoke-RestMethod $tempoUri).traces
$traces | Select-Object traceID, rootServiceName, rootTraceName, durationMs
```

Follow a result with
`http://127.0.0.1:3200/api/traces/<traceID>`. Use `docker compose logs -f app`
or `docker compose logs -f otel-collector` to inspect service output. Loki and
Tempo readiness endpoints are `/ready`.

## Alerts, incidents, and human verification

Grafana sends firing notifications to the internal responder URL
`http://incident-response:8001/alerts`. The provisioned rule sends firing
notifications only; resolved notifications are disabled. `POST /alerts`
persists the alert, queried Loki logs and Tempo traces, and a bounded diagnosis
when the optional provider is configured. Diagnosis does not edit code or
resolve incidents.

A direct POST to the responder is a **webhook smoke test**, not the
Grafana-to-responder demonstration. It creates a new incident and can consume
a Copilot request if the provider token is enabled. Use the isolated launcher
below for a real provider request; do not send test alerts to the normal
development responder when you do not intend to create an incident.

For an intentional no-AI direct webhook check, use the isolated demo project
after it is running and confirm `compose.demo.yaml` keeps the token blank:

```powershell
$body = @{
  status = 'firing'
  alertname = 'Direct responder webhook smoke test'
  endpoint = '/api/orders/{order_id}'
  description = 'Direct POST; this does not exercise Grafana alerting'
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri 'http://localhost:8001/alerts' `
  -ContentType 'application/json' -Body $body
```

This only verifies direct alert acceptance and incident persistence. It does
not verify the Grafana rule, metric scrape, alert evaluation, or notification
route.

Verification is a separate operator action. It requires `approved: true`, a
valid bearer token corresponding to an `IR_APPROVAL_TOKEN` of at least 32
characters, passing repository tests, and a healthy app `/healthz`. Only then
can resolution become `resolved`. Review and apply any proposed patch yourself;
never use diagnosis output as approval and never call `/verify` during either
smoke test described below.

## Isolated Grafana-to-responder demonstration

This controlled demo uses the seeded `express-1002` order to trigger the
existing 5xx alert. It is separate from direct webhook ingestion, the
authenticated Copilot launcher below, and the historical October 22, 2026
incident. The demo Compose override mounts its incident evidence on a
project-scoped named volume and explicitly disables Copilot, even if `.env`
contains a token.

The regular and demo projects use the same host ports. Stop the regular stack
without deleting its containers or volumes, then start the isolated project:

```powershell
$demo = @('-p', 'order-tracker-demo', '-f', 'compose.yaml', '-f', 'compose.demo.yaml')
$configText = docker compose @demo config --format json
$config = ($configText -join "`n") | ConvertFrom-Json
$incidentMount = @($config.services.'incident-response'.volumes |
  Where-Object { $_.target -eq '/incidents' })
if ($incidentMount.Count -ne 1 -or $incidentMount[0].type -ne 'volume') {
  throw 'Demo responder must use its named incident volume'
}
$config = $null
$configText = $null

docker compose stop
docker compose @demo up -d --wait
```

The demo has its own app database and observability volumes because its Compose
project name differs. Save the original timestamp before changing the demo
database, and restore it in `finally`:

```powershell
$originalTimestamp = (Invoke-RestMethod 'http://localhost:8000/api/orders/express-1002').created_at
if (-not $originalTimestamp) { throw 'Could not save the demo order timestamp' }
$beforeIds = @(
  docker compose @demo exec -T incident-response python -c "from pathlib import Path; print('\n'.join(sorted(p.name for p in Path('/incidents').iterdir() if p.is_dir())))"
)

try {
  docker compose @demo exec -T app python -c "import sqlite3; db=sqlite3.connect('/data/orders.db'); db.execute('UPDATE orders SET created_at=? WHERE id=?', ('not-a-timestamp','express-1002')); db.commit(); db.close()"
  if ($LASTEXITCODE -ne 0) { throw 'Could not alter the isolated demo order' }

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
    if ($status -ne 500) { throw "Expected HTTP 500, got $status" }
    if ($attempt -eq 1) { Start-Sleep -Seconds 3 }
  }

  # Allow export, scrape, alert evaluation, and webhook delivery.
  Start-Sleep -Seconds 60
  $auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes('admin:admin'))
  $headers = @{ Authorization = "Basic $auth" }
  $activeAlerts = Invoke-RestMethod `
    'http://localhost:3000/api/alertmanager/grafana/api/v2/alerts' -Headers $headers
  $activeAlerts |
    Where-Object { $_.labels.alertname -eq 'Order Tracker - 5xx Errors' } |
    Select-Object @{Name='alertname';Expression={$_.labels.alertname}},
      @{Name='state';Expression={$_.status.state}}

  docker compose @demo logs --since 10m incident-response |
    Select-String 'POST /alerts.*202'
  $afterIds = @(
    docker compose @demo exec -T incident-response python -c "from pathlib import Path; print('\n'.join(sorted(p.name for p in Path('/incidents').iterdir() if p.is_dir())))"
  )
  $newIds = @($afterIds | Where-Object { $_ -and $_ -notin $beforeIds })
  if ($newIds.Count -eq 0) { throw 'No new incident was persisted' }
  $incidentId = ($newIds | Sort-Object | Select-Object -Last 1).Trim()
  $incident = Invoke-RestMethod "http://localhost:8001/incidents/$incidentId"
  $incident | Select-Object incident_id, status, resolution_status, alert_name |
    Format-List
  if ($incident.resolution_status -ne 'investigating') {
    throw 'Expected resolution_status=investigating'
  }

  $evidenceCommand = "from pathlib import Path; import sys; d=Path('/incidents')/sys.argv[1]; [print('--- '+n+' ---\n'+(d/n).read_text(encoding='utf-8')) for n in ('incident.json','logs.json','traces.json')]"
  docker compose @demo exec -T incident-response python -c $evidenceCommand $incidentId
} finally {
  docker compose @demo exec -T app python -c "import sqlite3,sys; db=sqlite3.connect('/data/orders.db'); db.execute('UPDATE orders SET created_at=? WHERE id=?',(sys.argv[1],'express-1002')); db.commit(); db.close()" "$originalTimestamp"
  if ($LASTEXITCODE -ne 0) { throw 'Failed to restore the demo timestamp' }
  $restored = (Invoke-RestMethod 'http://localhost:8000/api/orders/express-1002').created_at
  if ($restored -ne $originalTimestamp) { throw 'Demo timestamp restoration failed' }
}
```

Verify the alert is firing, the responder log records HTTP 202, and the new
incident remains `resolution_status=investigating`. **Do not call `/verify`.**
Then stop the demo and restart the original stack. This does not delete
containers or volumes:

```powershell
docker compose @demo stop
docker compose up -d --wait
```

If a command fails after the regular stack is stopped, first confirm the demo
timestamp was restored; then run the same two cleanup commands above. They
stop the demo containers and restart the original stack without deleting
volumes. If restoration itself failed, keep the demo isolated and restore the
saved timestamp before stopping it.

The demo's named database, observability, and incident volumes persist. Before
repeating the failure demonstration, allow at least 5 minutes after the last
500 response for the alert's five-minute query window to clear and confirm the
rule is back to Normal. Continue comparing incident IDs with the pre-run list;
do not delete the demo volumes to reset the demonstration.

The earlier recorded **October 22, 2026** failure incident
`20261022T174540Z-4b828403-1eb1-46c8-8b44-6f37fff270ef` is historical and
must be preserved. It remains `status=firing` and
`resolution_status=investigating`; its diagnosis was `unavailable`, so it is
not evidence that the Copilot provider succeeded. The **October 26, 2026**
normal-lookup validation observed `order_lookup_requests_total` increase from
1 to 2 after a successful lookup and an 8-second wait. Loki and Tempo stored
the corresponding log and `order lookup` trace with shared trace ID
`999f2de032e07a4868088ce3468aa5f8` and reported span duration 27 ms. Grafana
reported the 5xx rule healthy and **Normal (NoData)**; no alert was firing.
These are historical local validations, not a public deployment or a recent
CI run.

## Optional real Copilot CLI smoke test

The responder image contains the official standalone Copilot CLI pinned to
v1.0.112. It is optional and receives a minimal environment, a fresh
`COPILOT_HOME`, and only the `view`, `grep`, and `glob` tools. Diagnosis is
read-only and proposes remediation only; a human applies any patch.

The safe Windows launcher starts a uniquely named Compose project with
project-scoped data and incident volumes, alternate host ports, and an unused
Docker subnet. It checks that `.env` is ignored, the token is configured and
available inside the responder, the responder is healthy, and `/incidents` is
a named volume rather than the repository bind mount. It sends a direct alert
to the isolated responder only after you type `RUN`; pressing Enter cancels
without creating an incident.

```powershell
.\scripts\run-copilot-smoke-test.bat
```

The launcher distinguishes completed diagnosis, provider/authentication
failure, malformed output, and diagnosis timeout. It never calls `/verify`.
Afterward it removes only containers and the network for its unique project;
the named incident volume is retained. The launcher was not executed as part
of this documentation change, and no authenticated Copilot request has been
confirmed in this project.

The launcher prints the volume name and incident ID. To inspect the incident
files later, mount that named volume read-only:

```powershell
$incidentVolume = '<volume printed by the launcher>'
$incidentId = '<incident ID printed by the launcher>'
docker run --rm `
  --mount "type=volume,source=$incidentVolume,target=/incidents,readonly" `
  --entrypoint python order-tracker-incident-response:local `
  -c "from pathlib import Path; import sys; d=Path('/incidents')/sys.argv[1]; [print('--- '+n+' ---\n'+(d/n).read_text(encoding='utf-8')) for n in ('incident.json','logs.json','traces.json','agent-output.json','agent-response.txt')]" `
  $incidentId
```

Review the printed volume name and preserve its evidence. This guide provides
no volume-deletion step; the launcher never removes volumes.

`agent-output.json` records a structured diagnosis and status:

| Status | Meaning |
| --- | --- |
| `completed` | The CLI returned valid JSON with all required diagnosis fields. This does not resolve the incident. |
| `unavailable` | The optional token or Copilot executable is unavailable. |
| `malformed_output` | The response was not valid JSON or did not match the required schema. |
| `failed` | The CLI exited unsuccessfully or the provider failed. |
| `timeout` | The bounded CLI request exceeded its timeout. |
| `configuration_error` | The configured tool or CLI permissions were invalid. |

`agent-response.txt` contains only bounded, token-redacted raw output. Treat
both files as untrusted evidence. Do not copy model-proposed patches into the
repository without human review and approval.

## Hosting and rollback

The project is currently local-only; it has no confirmed public deployment.
Public hosting would require a durable database suitable for the expected
write/load pattern, secure secret management for the responder approval and
Copilot tokens, authenticated and encrypted public access, restricted service
networking, production-grade health/readiness checks, and protected
observability endpoints with retention and access controls. SQLite and the
default Grafana credentials are for this local exercise, not a production
configuration. No hosting provider is selected here.

For a local rollback, restore a known-good source revision and rebuild only
the affected service. Preserve the SQLite and incident evidence volumes; do
not use volume-pruning or `down -v` commands as part of routine rollback.
