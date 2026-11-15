import json
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.main import app


contract_path = project_root / "docs" / "api" / "openapi.json"
contract_path.parent.mkdir(parents=True, exist_ok=True)
contract_path.write_text(
    json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
