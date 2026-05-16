"""Shared pytest fixtures for osi_python.

Fixtures live here rather than per-layer so that property tests, unit
tests, golden tests, and E2E tests can all share the same generation
strategies and DuckDB harness.

Phase 0 scaffolding: keep this file small. Richer fixtures (DuckDB
engines, golden snapshot helpers) are added in later phases.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Pin async backend to asyncio for determinism across test runs."""
    return "asyncio"
