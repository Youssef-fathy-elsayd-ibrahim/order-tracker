FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY app ./app
COPY tests ./tests
COPY incident-response/app.py /service/app.py
WORKDIR /service
ENV INCIDENT_DIR=/incidents
ENV ORDER_TRACKER_REPO=/workspace
EXPOSE 8001
CMD ["uv", "run", "--project", "/workspace", "--no-sync", "uvicorn", "app:app", "--app-dir", "/service", "--host", "0.0.0.0", "--port", "8001"]
