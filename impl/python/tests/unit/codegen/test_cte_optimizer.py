"""Unit tests for :mod:`osi.codegen.cte_optimizer`."""

from __future__ import annotations

import sqlglot

from osi.codegen.cte_optimizer import optimize_ctes


def _parse(sql: str):
    return sqlglot.parse_one(sql)


def test_optimize__no_with_is_noop() -> None:
    ast = _parse("SELECT 1")
    out = optimize_ctes(ast)
    assert out is ast
    assert out.args.get("with") is None


def test_optimize__all_ctes_live_is_noop() -> None:
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), step_001 AS (SELECT x FROM step_000) "
        "SELECT x FROM step_001"
    )
    out = optimize_ctes(ast)
    with_clause = out.args.get("with")
    assert with_clause is not None
    names = {c.alias_or_name for c in with_clause.expressions}
    assert names == {"step_000", "step_001"}


def test_optimize__drops_unreferenced_cte() -> None:
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), step_unused AS (SELECT 2 AS y) "
        "SELECT x FROM step_000"
    )
    out = optimize_ctes(ast)
    with_clause = out.args.get("with")
    assert with_clause is not None
    names = {c.alias_or_name for c in with_clause.expressions}
    assert names == {"step_000"}


def test_optimize__keeps_transitively_referenced_cte() -> None:
    """If a kept CTE references another, the referent survives too."""
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), "
        "step_001 AS (SELECT x FROM step_000), "
        "step_unused AS (SELECT 2 AS y) "
        "SELECT x FROM step_001"
    )
    out = optimize_ctes(ast)
    with_clause = out.args.get("with")
    assert with_clause is not None
    names = {c.alias_or_name for c in with_clause.expressions}
    assert names == {"step_000", "step_001"}


def test_optimize__is_idempotent() -> None:
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), step_unused AS (SELECT 2 AS y) "
        "SELECT x FROM step_000"
    )
    once = optimize_ctes(ast).sql()
    twice = optimize_ctes(optimize_ctes(_parse(once))).sql()
    assert once == twice
