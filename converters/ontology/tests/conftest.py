"""Shared fixtures for the ontology converter test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from osi.model import OsiOntology
from osi.parser import OsiParser

# Repo layout: <repo>/converters/ontology/tests/conftest.py -> <repo>/examples
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES_DIR = _REPO_ROOT / "examples"


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    if not _EXAMPLES_DIR.is_dir():
        pytest.skip(f"examples directory not found at {_EXAMPLES_DIR}")
    return _EXAMPLES_DIR


@pytest.fixture(scope="session")
def flights_path(examples_dir: Path) -> Path:
    path = examples_dir / "flights.yaml"
    if not path.is_file():
        pytest.skip(f"flights.yaml not found at {path}")
    return path


@pytest.fixture
def flights_model(flights_path: Path) -> OsiOntology:
    return OsiParser().parse(flights_path)