"""Guard against drift between the vendored test inputs under tests/fixtures/ and
the canonical copies under the repo-level examples/ directory.

The core test suite reads the vendored copies (so it runs from an sdist/wheel or
subset checkout). This test only runs when examples/ is available — it fails if a
vendored file falls out of sync, prompting a refresh.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# tests/ -> ontology -> converters -> <repo root>
_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"

# (vendored filename, canonical filename in examples/)
_VENDORED = [("flights.yaml", "flights.yaml")]


@pytest.mark.parametrize("vendored_name, example_name", _VENDORED)
def test_vendored_input_matches_example(vendored_name: str, example_name: str):
    example_path = _EXAMPLES_DIR / example_name
    if not example_path.is_file():
        pytest.skip(f"canonical example not present at {example_path}")

    vendored_path = _FIXTURES_DIR / vendored_name
    assert vendored_path.is_file(), f"vendored input missing: {vendored_path}"
    assert vendored_path.read_text(encoding="utf-8") == example_path.read_text(encoding="utf-8"), (
        f"'{vendored_path}' is out of sync with '{example_path}'. "
        f"Refresh it with: cp {example_path} {vendored_path}"
    )