"""Shared fixtures for the ontology converter test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from osi.model import OsiOntology
from osi.parser import OsiParser

# Test inputs are vendored under tests/fixtures/ so the suite runs even when the
# repo-level examples/ directory isn't present (e.g. from an sdist/wheel or a
# subset checkout). tests/test_examples_in_sync.py guards against drift from the
# canonical examples/ copies.
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return _FIXTURES_DIR


@pytest.fixture(scope="session")
def flights_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "flights.yaml"


@pytest.fixture
def flights_model(flights_path: Path) -> OsiOntology:
    return OsiParser().parse(flights_path)