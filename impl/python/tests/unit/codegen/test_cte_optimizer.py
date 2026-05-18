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


def test_optimize__trivial_pass_through_is_inlined() -> None:
    """A trivially pass-through final CTE is collapsed into the outer SELECT.

    The pattern ``step_001 AS (SELECT step_000.x FROM step_000)`` followed
    by ``SELECT step_001.x FROM step_001`` is equivalent to reading
    ``step_000`` directly; the optimizer removes ``step_001`` and rewrites
    all references.
    """
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), "
        "step_001 AS (SELECT step_000.x FROM step_000) "
        "SELECT step_001.x FROM step_001"
    )
    out = optimize_ctes(ast)
    # step_001 is inlined; only step_000 should remain.
    with_clause = out.args.get("with")
    assert with_clause is not None
    names = {c.alias_or_name for c in with_clause.expressions}
    assert names == {"step_000"}
    # Outer SELECT must now read from step_000.
    from_table = out.args["from"].this
    assert from_table.name == "step_000"


def test_optimize__non_trivial_final_cte_is_not_inlined() -> None:
    """A final CTE that is not a bare pass-through is left alone."""
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), "
        "step_001 AS (SELECT x FROM step_000 WHERE x > 0) "
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
    """If a kept (non-trivial) CTE references another, the referent survives."""
    ast = _parse(
        "WITH step_000 AS (SELECT 1 AS x), "
        "step_001 AS (SELECT x FROM step_000 WHERE x > 0), "
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

