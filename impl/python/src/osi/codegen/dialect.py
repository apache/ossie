"""Dialect-specific rendering of a SQLGlot AST.

The Foundation supports three dialects:

* :attr:`Dialect.ANSI` — SQLGlot's default, portable baseline.
* :attr:`Dialect.DUCKDB` — the reference execution runtime used by the
  E2E harness.
* :attr:`Dialect.SNOWFLAKE` — the other production target covered by
  the Snowflake E2E corpus.

This module is intentionally thin: we let SQLGlot's own dialect
registry do the heavy lifting (``Expression.sql(dialect=...)``) and
layer on only OSI-specific rewrites here. Adding a new dialect means:

1. Adding the enum variant in :mod:`osi.codegen.types`.
2. Extending ``_DIALECT_NAMES`` below.
3. Adding a golden column in ``tests/golden/_driver.py``.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from osi.errors import ErrorCode, OSICodegenError

from .types import Dialect

_DIALECT_NAMES: dict[Dialect, str] = {
    # S-16 / D-021: OSI_SQL_2026 is the Foundation default. SQLGlot
    # has no dialect named ``osi_sql_2026`` so we render it through the
    # ANSI baseline (no dialect-specific rewrites) — the OSI_SQL_2026
    # subset is by construction a *subset* of ANSI SQL, so the ANSI
    # serializer produces a string that every conforming engine can
    # parse.
    Dialect.OSI_SQL_2026: "",
    Dialect.ANSI: "",
    Dialect.DUCKDB: "duckdb",
    Dialect.SNOWFLAKE: "snowflake",
}


def render_sql(expression: exp.Expression, *, dialect: Dialect) -> str:
    """Render ``expression`` as SQL text for ``dialect``.

    Uses SQLGlot's :func:`sql` with ``pretty=True`` so goldens are
    human-readable. Determinism comes from upstream: every node fed in
    has already been built by the deterministic planner + transpiler,
    and SQLGlot's serializer is stable for a fixed AST.

    ``identify=True`` is set so every emitted identifier is wrapped in
    the dialect's quote character (``"`` for ANSI / Postgres / DuckDB /
    Snowflake, backticks for BigQuery / MySQL when those land). This
    closes a class of bugs where a user-defined dataset / field /
    metric name happens to match a SQL reserved word for the target
    dialect: previously ``SELECT id, in FROM t`` was emitted bare
    and rejected by every strict-dialect parser; with quoting, the
    same name compiles cleanly to ``SELECT "id", "in" FROM "t"``.
    The trade-off is verbosity in golden snapshots — semantically
    equivalent SQL with quotes around every identifier — which is
    a one-time refresh cost weighed against the risk of silently
    emitting invalid SQL on stricter engines.

    Raises :class:`OSICodegenError` with ``E5002_SQLGLOT_RENDER_FAILED``
    if SQLGlot refuses to render the AST (typically a missing dialect
    feature on a vendor function).
    """
    dialect_name = _DIALECT_NAMES.get(dialect)
    if dialect_name is None:
        raise OSICodegenError(
            ErrorCode.E5001_DIALECT_UNSUPPORTED,
            f"dialect {dialect!r} is not registered",
            context={"dialect": dialect},
        )
    try:
        return expression.sql(dialect=dialect_name or None, pretty=True, identify=True)
    except Exception as err:  # pragma: no cover — SQLGlot internals
        raise OSICodegenError(
            ErrorCode.E5002_SQLGLOT_RENDER_FAILED,
            f"SQLGlot failed to render AST: {err}",
            context={"dialect": dialect, "error": str(err)},
        ) from err


__all__ = ["Dialect", "render_sql"]
