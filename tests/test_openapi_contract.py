import json
from pathlib import Path

from app.main import app


CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"
)


def test_openapi_contract_matches_application():
    expected = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert app.openapi() == expected, (
        "The checked-in OpenAPI contract is stale. After reviewing an intentional "
        "API change, run `uv run --frozen python scripts/export_openapi.py`."
    )
