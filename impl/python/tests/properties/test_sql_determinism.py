"""Phase 4 — SQL rendering determinism.

For any valid plan, compiling twice with the same dialect must produce
byte-identical SQL. Hypothesis samples from a small corpus of shaped
queries against the fixed ``orders`` model so we exercise every
``PlanOperation`` (SOURCE, FILTER, ENRICH, AGGREGATE, PROJECT, MERGE).

Law §4.3 extended: *Determinism of rendering*. A byte diff in rendered
SQL between two identical compilations is always a regression.
"""

from __future__ import annotations

import sqlglot
from hypothesis import given, settings
from hypothesis import strategies as st

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


_QUERIES: tuple[SemanticQuery, ...] = (
    SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
    ),
    SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    ),
    SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    ),
    SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=FrozenSQL.of(sqlglot.parse_one("orders.amount > 100")),
    ),
    SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        order_by=(
            OrderBy(
                target=_ref("orders", "total_revenue"),
                direction=SortDirection.DESC,
            ),
        ),
        limit=5,
    ),
)


@given(
    query=st.sampled_from(_QUERIES),
    dialect=st.sampled_from(list(Dialect)),
)
@settings(max_examples=60, deadline=None)
def test_sql_rendering_is_deterministic(query: SemanticQuery, dialect: Dialect) -> None:
    """Compiling the same query+dialect twice yields identical SQL."""
    ctx = orders_context()
    sql_a = compile_plan(plan(query, ctx), dialect=dialect)
    sql_b = compile_plan(plan(query, ctx), dialect=dialect)
    assert sql_a == sql_b


@given(query=st.sampled_from(_QUERIES))
@settings(max_examples=30, deadline=None)
def test_compilation_is_pure(query: SemanticQuery) -> None:
    """The planner context is read-only — repeated compilation is pure."""
    ctx = orders_context()
    plan_a = plan(query, ctx)
    plan_b = plan(query, ctx)
    assert plan_a.to_json() == plan_b.to_json()
    assert compile_plan(plan_a, dialect=Dialect.ANSI) == compile_plan(
        plan_b, dialect=Dialect.ANSI
    )
