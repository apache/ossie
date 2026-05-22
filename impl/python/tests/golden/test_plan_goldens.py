"""Plan-only golden tests (Phase 3).

This module freezes the *planner output* — the
:class:`~osi.planning.QueryPlan` — for a curated corpus of semantic
queries. The SQL-level goldens (ANSI / DuckDB / Snowflake) are added in
Phase 4 once the codegen ships; see ``tests/golden/README.md``.

Snapshots live next to this file in ``__snapshots__/``. To refresh them
after an intentional planner change, run::

    make golden-refresh

which invokes ``pytest --snapshot-update`` under the hood. Any refresh
in a PR must be justified: a plan diff is a behaviour diff.
"""

from __future__ import annotations

import json

import pytest
import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


def _canonical(query: SemanticQuery) -> str:
    ctx = orders_context()
    p = plan(query, ctx)
    return json.dumps(p.to_json(), indent=2, sort_keys=True)


@pytest.mark.golden
def test_plan__single_table_dim_plus_measure(snapshot) -> None:
    """Baseline: one fact + one dimension on the fact itself."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    assert _canonical(query) == snapshot


@pytest.mark.golden
def test_plan__enrichment_dim_on_joined_table(snapshot) -> None:
    """§4.4 enrich: dimension lives on ``customers`` (N:1 from orders)."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    assert _canonical(query) == snapshot


@pytest.mark.golden
def test_plan__two_fact_merge_on_shared_dimension(snapshot) -> None:
    """§4.11 chasm-trap safety: two facts, merge on shared region."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    assert _canonical(query) == snapshot


@pytest.mark.golden
def test_plan__where_pushed_below_aggregate(snapshot) -> None:
    """WHERE rows are filtered *before* aggregate (§4.2)."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=_sql("orders.amount > 100"),
    )
    assert _canonical(query) == snapshot


@pytest.mark.golden
def test_plan__order_by_and_limit(snapshot) -> None:
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        order_by=(
            OrderBy(
                target=_ref("orders", "total_revenue"),
                direction=SortDirection.DESC,
            ),
        ),
        limit=10,
    )
    assert _canonical(query) == snapshot
