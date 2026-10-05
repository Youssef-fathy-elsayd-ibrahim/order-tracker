FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
WORKDIR /service
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app.py ./
ENV INCIDENT_DIR=/incidents
ENV ORDER_TRACKER_REPO=/workspace
EXPOSE 8001
CMD ["uv", "run", "--no-sync", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
