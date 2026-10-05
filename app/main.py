import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import ConsoleLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Status, StatusCode, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from pydantic import BaseModel, Field


DB_PATH = Path(os.getenv("ORDER_DB_PATH", "data/orders.db"))
STATUSES = {"received", "preparing", "shipped", "delivered"}
ORDER_LOOKUP_ROUTE = "/api/orders/{order_id}"
telemetry_resource = Resource.create({"service.name": "order-tracker"})

metric_reader = InMemoryMetricReader()
metric_exporter = ConsoleMetricExporter(out=sys.stdout)
meter_provider = MeterProvider(resource=telemetry_resource, metric_readers=[metric_reader])
order_lookup_requests = meter_provider.get_meter(__name__).create_counter(
    "order.lookup.requests",
    description="Number of order lookup requests",
    unit="{request}",
)

tracer_provider = TracerProvider(resource=telemetry_resource)
tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout)))
tracer = tracer_provider.get_tracer(__name__)

logger_provider = LoggerProvider(resource=telemetry_resource)
logger_provider.add_log_record_processor(
    SimpleLogRecordProcessor(ConsoleLogRecordExporter(out=sys.stdout))
)
telemetry_logger = logger_provider.get_logger(__name__)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                item TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        if db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            now = datetime.now(timezone.utc)
            previous_month_end = now.replace(day=1) - timedelta(days=1)
            for order in (
                ("standard-1001", "Avery", "Notebook", "standard", "received", now),
                ("express-1002", "Sam", "Headphones", "express", "preparing", previous_month_end),
                ("standard-1003", "Riley", "Water bottle", "standard", "shipped", now),
            ):
                db.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                    (*order[:5], order[5].isoformat()),
                )


def as_dict(row):
    return dict(row) if row else None


def order_detail(row):
    order = as_dict(row)
    if order["priority"] == "express":
        placed_at = datetime.fromisoformat(order["created_at"])
        estimated_at = placed_at.replace(day=placed_at.day + 2)
        order["estimated_delivery"] = estimated_at.date().isoformat()
    return order


def lookup_order(order_id):
    with connect() as db:
        row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return order_detail(row)


def record_order_lookup(status_code):
    order_lookup_requests.add(
        1,
        {
            "http.route": ORDER_LOOKUP_ROUTE,
            "http.response.status_code": status_code,
        },
    )
    metrics_data = metric_reader.get_metrics_data()
    if metrics_data is not None:
        metric_exporter.export(metrics_data)


def emit_order_lookup_log(status_code, found, error_type=None):
    severity = SeverityNumber.INFO
    body = "Order lookup completed"
    if status_code >= 500:
        severity = SeverityNumber.ERROR
        body = "Order lookup failed"
    elif not found:
        severity = SeverityNumber.WARN
        body = "Order lookup not found"

    attributes = {
        "http.route": ORDER_LOOKUP_ROUTE,
        "http.response.status_code": status_code,
        "order.found": found,
    }
    if error_type is not None:
        attributes["error.type"] = error_type
    telemetry_logger.emit(
        severity_number=severity,
        body=body,
        attributes=attributes,
    )


class NewOrder(BaseModel):
    customer: str = Field(min_length=1, max_length=80)
    item: str = Field(min_length=1, max_length=120)
    priority: str = "standard"


class StatusUpdate(BaseModel):
    status: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Order Tracker", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")


@app.get("/healthz")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/orders")
def list_orders():
    with connect() as db:
        rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [as_dict(row) for row in rows]


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    status_code = 500
    found = False
    with tracer.start_as_current_span("order lookup") as span:
        span.set_attribute("http.route", ORDER_LOOKUP_ROUTE)
        span.set_attribute("http.request.method", "GET")
        try:
            order = lookup_order(order_id)
        except HTTPException as exc:
            status_code = exc.status_code
            if status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            emit_order_lookup_log(status_code, found)
            raise
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR))
            emit_order_lookup_log(status_code, found, type(exc).__name__)
            raise
        else:
            status_code = 200
            found = True
            emit_order_lookup_log(status_code, found)
            return order
        finally:
            span.set_attribute("http.response.status_code", status_code)
            span.set_attribute("order.found", found)
            record_order_lookup(status_code)


@app.post("/api/orders", status_code=201)
def create_order(order: NewOrder):
    if order.priority not in {"standard", "express"}:
        raise HTTPException(422, "Priority must be standard or express")
    order_id = str(uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, order.customer, order.item, order.priority, "received",
             datetime.now(timezone.utc).isoformat()),
        )
    return lookup_order(order_id)


@app.patch("/api/orders/{order_id}")
def update_status(order_id: str, update: StatusUpdate):
    if update.status not in STATUSES:
        raise HTTPException(422, "Invalid status")
    with connect() as db:
        cursor = db.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (update.status, order_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(404, "Order not found")
    return lookup_order(order_id)
