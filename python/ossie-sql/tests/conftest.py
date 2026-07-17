"""Shared test fixtures.

Importing ``ossie_sql`` registers the ``"ossie"`` dialect with SQLGlot (via
the ``Ossie(Dialect)`` subclass's registration metaclass), so every test
module can call ``sqlglot.parse_one(sql, read="ossie")`` /
``expression.sql(dialect="ossie")`` without an explicit import of
``ossie_sql.dialect``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlglot
from sqlglot import exp

import ossie_sql  # noqa: F401  (registers the "ossie" dialect as a side effect)


@pytest.fixture
def parse_ossie() -> Callable[[str], exp.Expr]:
    """Return a helper that parses SQL text with the Ossie dialect."""

    def _parse(sql: str) -> exp.Expr:
        return sqlglot.parse_one(sql, read="ossie")

    return _parse
