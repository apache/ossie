"""Unit tests for :mod:`osi.codegen.transpiler`."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from osi.codegen import Dialect, compile_plan
from osi.codegen.transpiler import plan_to_select
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSICodegenError
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.plan import PlanOperation, PlanStep, QueryPlan, SourcePayload
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


def test_plan_to_select__emits_one_cte_per_step() -> None:
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    p = plan(query, orders_context())
    ast = plan_to_select(p)
    with_clause = ast.args.get("with")
    assert with_clause is not None
    assert len(with_clause.expressions) == len(p.steps)
    # Alias pattern is deterministic: step_000, step_001, ...
    names = [c.alias_or_name for c in with_clause.expressions]
    assert names == [f"step_{i:03d}" for i in range(len(p.steps))]


def test_plan_to_select__final_references_root_cte() -> None:
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    p = plan(query, orders_context())
    ast = plan_to_select(p)
    # The outer SELECT's FROM must reference the root step's CTE alias.
    tables = list(ast.find_all(exp.Table))
    from_tables = [t for t in tables if t.parent is not with_clause(ast)]
    assert any(
        t.name == f"step_{p.root_step_id:03d}" for t in from_tables
    ), f"expected outer SELECT to FROM step_{p.root_step_id:03d}"


def with_clause(ast: exp.Select):
    return ast.args.get("with")


def test_enrich_join__uses_different_column_names_on_each_side() -> None:
    """Regression: orders.customer_id ↔ customers.id must not collapse.

    ``compile_plan`` quotes every identifier so the join condition
    surfaces as ``"customer_id" = "step_000_r"."id"``. The negative
    assertion mirrors the same form to keep the regression tight on
    the actual rendered SQL.
    """
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.ANSI)
    assert '"customer_id" = "step_000_r"."id"' in sql
    assert '"customer_id" = "step_000_r"."customer_id"' not in sql


def test_missing_source__raises_e5001() -> None:
    """A SOURCE payload with an empty physical source is a codegen error."""
    empty_payload = SourcePayload(
        dataset=normalize_identifier("orders"),
        primary_key=frozenset(),
        source="",
    )
    bad_plan = QueryPlan(
        steps=(
            PlanStep(
                step_id=0,
                operation=PlanOperation.SOURCE,
                inputs=(),
                payload=empty_payload,
                state=_empty_state(),
            ),
        ),
        root_step_id=0,
    )
    with pytest.raises(OSICodegenError) as excinfo:
        plan_to_select(bad_plan)
    assert excinfo.value.code is ErrorCode.E5001_DIALECT_UNSUPPORTED


def _empty_state():
    from osi.planning.algebra.state import CalculationState

    return CalculationState(grain=frozenset(), columns=())


def test_compile_plan__where_materialises_as_filter_cte() -> None:
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=_sql("orders.amount > 100"),
    )
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.DUCKDB)
    # FILTER step is rendered as a WHERE clause *above* the aggregate.
    assert "WHERE" in sql
    # There must be a GROUP BY after the WHERE.
    assert sql.index("WHERE") < sql.index("GROUP BY")
