"""Re-expose DuckDB fixtures so the property layer can run real SQL.

Property tests for laws like §4.9 (enrich preserves rows) and §4.10
(explosion safety) need the same seeded DuckDB the E2E suite uses.
``conftest.py`` only auto-applies inside its own subtree, so we
re-import the fixture here rather than duplicating the seed code.
"""

from __future__ import annotations

from tests.e2e.conftest import duckdb_sales  # noqa: F401
