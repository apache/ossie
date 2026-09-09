import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def normalize(obj: Any) -> Any:
    """Recursively sort dict keys so structurally-equal JSON compares equal regardless of order."""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    return obj
