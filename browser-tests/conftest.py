import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, sync_playwright


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser):
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def app_url(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment["ORDER_DB_PATH"] = str(tmp_path / "browser-orders.db")
    environment.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    python_paths = [str(project_root), environment.get("PYTHONPATH", "")]
    environment["PYTHONPATH"] = os.pathsep.join(path for path in python_paths if path)

    log_path = tmp_path / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=project_root,
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "Test application exited before becoming ready:\n"
                        + log_path.read_text(encoding="utf-8")
                    )
                try:
                    with urlopen(f"{url}/healthz", timeout=0.25) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError, TimeoutError):
                    time.sleep(0.05)
            else:
                raise RuntimeError(
                    "Test application did not become ready:\n"
                    + log_path.read_text(encoding="utf-8")
                )

            yield url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
