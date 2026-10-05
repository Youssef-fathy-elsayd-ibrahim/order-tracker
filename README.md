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

Grafana also provisions **Order Tracker - 5xx Errors**, which evaluates order lookup `5xx` responses over a five-minute window every 10 seconds. With no matching 5xx series, the query has no data and Grafana maps that state to **Normal**; query execution errors remain **Error**. Inspect the rule under **Alerting / Alert rules**. This rule has no notification integration configured.

## Incident response (Q5)

The local incident-response API listens on port `8001`. `POST http://localhost:8001/alerts` accepts alert payloads, saves incident details and the collected Loki logs/Tempo traces under `incident-response/incidents/`, and prepares a prompt for a headless coding assistant. The responder queries Loki and Tempo directly using the configured Compose service names. Runtime incident artifacts are ignored by Git.

To smoke-test it manually:

```bash
curl -X POST http://localhost:8001/alerts \
  -H "Content-Type: application/json" \
  -d "{\"alertname\":\"Local smoke test\",\"status\":\"firing\",\"endpoint\":\"/api/orders/{order_id}\",\"description\":\"Local responder smoke test\"}"
```

An assistant executable installed inside the responder image can be configured with trusted `AGENT_COMMAND` and optional `AGENT_ARGS_JSON` environment settings; the latter is a JSON argument array and supports `{prompt}` as an argument placeholder. Arguments are executed without a shell. No coding-assistant CLI is bundled in the responder image; when none is configured, the incident is still saved and the agent status records that it was unavailable. No Grafana webhook is configured.

If port 8000 is occupied, set `ORDER_TRACKER_PORT`, for example:

```bash
ORDER_TRACKER_PORT=18080 docker compose up --build -d --wait
```

Run tests with `uv run --frozen pytest -q`. Stop the app with `docker compose down`. Add `-v` only if you also want to delete the order data.

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
