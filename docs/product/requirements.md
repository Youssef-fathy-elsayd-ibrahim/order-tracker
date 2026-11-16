# Order Tracker product requirements

## Problem and intended users

Order Tracker is a small local application for recording customer orders and
checking their fulfillment status. Its intended users are operators or
demonstrators who need a simple order workflow and a local observability and
incident-response exercise. The application does not implement user accounts
or distinguish roles.

## Product workflows

1. **List orders:** opening the page loads the seeded or previously stored
   orders. The API returns orders ordered by creation time.
2. **Create an order:** a user enters customer, item, and priority in the
   browser form. A successful request creates the order in `received` status
   and refreshes the list. Failed list or create requests produce visible,
   accessible feedback; a failed create keeps the entered values available
   for retry.
3. **Check status:** the user selects an order's status-check control. The API
   returns its current status and, for express orders, an estimated delivery
   date. Lookup failures are announced in the page.
4. **Update status:** an API client may submit a valid status update. The API
   returns the updated order; invalid status values are rejected.

## API behavior and validation

The checked-in OpenAPI contract at `docs/api/openapi.json` describes the
FastAPI routes. The main API provides:

- `GET /healthz`: database health check.
- `GET /api/orders`: list orders.
- `POST /api/orders`: create an order and return HTTP 201. Customer is required
  and 1-80 characters; item is required and 1-120 characters. Priority is
  `standard` by default and supports `standard` or `express`.
- `GET /api/orders/{order_id}`: return the order or HTTP 404 if it does not
  exist. Express-order delivery estimates are derived from `created_at`.
- `PATCH /api/orders/{order_id}`: update status to one of `received`,
  `preparing`, `shipped`, or `delivered`; invalid values are rejected.

The API does not authenticate callers. Browser controls provide labels and
status/error announcements, escape user-provided text, and prevent duplicate
order submissions while a request is in progress.

## Data and persistence

Orders are stored in SQLite at `ORDER_DB_PATH`. The Docker Compose deployment
uses `/data/orders.db` on the `orders` named volume. On first initialization,
three sample orders are seeded only if the database is empty. Restarting the
application against the same database preserves order data and status updates.
The persistence test uses a temporary database and two sequential app clients.

## Observability and user-impact alerting

Order lookup requests emit OpenTelemetry metrics, structured logs, and traces.
The local collector exports metrics to Prometheus, logs to Loki, and traces to
Tempo. Grafana provides a request dashboard and a 5xx alert for order lookups.
Firing notifications are routed to the incident responder. Telemetry and alert
processing are operational features; they do not themselves change order data
or resolve an incident.

## Incident response and AI diagnosis

The responder accepts alerts and persists the alert, logs, traces, and diagnosis
artifacts. When configured, the responder invokes the pinned GitHub Copilot CLI
in non-interactive mode with a read-only tool allowlist. It validates and saves
a structured diagnosis containing likely cause, uncertainty, proposed
remediation, evidence, and a suggested patch. Diagnosis is fallible and
untrusted: it never applies a patch or marks an incident resolved. Missing
credentials/provider failures do not prevent alert acceptance.

An operator must review and apply any remediation. Verification requires
explicit human approval and the configured bearer token, passing project tests,
and a healthy application endpoint. Diagnosis alone is never sufficient to
resolve an incident.

## Non-goals

- Authentication, authorization, user profiles, or multi-user permissions.
- Payments, billing, inventory management, or shipping-provider integration.
- Public hosting, paid observability services, or horizontal SQLite scaling.
- Autonomous patch application or automatic incident resolution.

## Acceptance criteria and evidence links

- **[Issue #1 — browser tests for core interactions](https://github.com/Youssef-fathy-elsayd-ibrahim/order-tracker/issues/1):** browser coverage for listing/creating orders, checking status, and visible API failures; documented local command and CI job.
- **[Issue #2 — persistence across app restarts](https://github.com/Youssef-fathy-elsayd-ibrahim/order-tracker/issues/2):** create and update an order in a temporary SQLite file, restart the test client, and verify persisted values.
- **[Issue #3 — Grafana-to-responder demonstration](https://github.com/Youssef-fathy-elsayd-ibrahim/order-tracker/issues/3):** document the controlled local alert flow using an isolated Compose project, named data/evidence volumes, and preserved responder approval gates.
- **[Issue #4 — bounded AI diagnosis](https://github.com/Youssef-fathy-elsayd-ibrahim/order-tracker/issues/4):** use an optional pinned assistant CLI with enforced read-only permissions; validate structured diagnosis; preserve HTTP 202 ingestion and investigating state on success or failure; retain human remediation and verification gates.

## Local setup and verification

Start the application and observability stack:

```powershell
docker compose up --build -d --wait
```

Run the API and OpenAPI tests, responder tests, and isolated browser tests:

```powershell
uv run --frozen pytest -q
uv run --directory incident-response --frozen pytest -q
uv run --frozen playwright install chromium
uv run --frozen pytest -q browser-tests
```

Browser tests start a local app process with a SQLite database under pytest's
temporary directory. They do not use the development database or Docker
volumes.
