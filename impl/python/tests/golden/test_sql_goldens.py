"""SQL golden tests (Phase 4).

Freezes the *codegen output* — the rendered SQL string — for a curated
corpus of semantic queries across every supported dialect. Plan-level
goldens live in ``test_plan_goldens.py``; this module is the final
behavioural check that a plan really does lower to the SQL we expect.

Snapshots live next to this file in ``__snapshots__/``. To refresh them
after an intentional codegen change, run::

    make golden-refresh

Any refresh in a PR must be justified: SQL drift is a behaviour diff.
"""

from __future__ import annotations

import pytest
import sqlglot

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


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


_DIALECTS = (Dialect.ANSI, Dialect.DUCKDB, Dialect.SNOWFLAKE)


def _compile(query: SemanticQuery, dialect: Dialect) -> str:
    p = plan(query, orders_context())
    return compile_plan(p, dialect=dialect)


@pytest.mark.golden
@pytest.mark.parametrize("dialect", _DIALECTS, ids=lambda d: d.name.lower())
def test_sql__single_table_dim_plus_measure(snapshot, dialect: Dialect) -> None:
    """Baseline: one fact + one dimension on the fact itself."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    assert _compile(query, dialect) == snapshot


@pytest.mark.golden
@pytest.mark.parametrize("dialect", _DIALECTS, ids=lambda d: d.name.lower())
def test_sql__enrichment_dim_on_joined_table(snapshot, dialect: Dialect) -> None:
    """§4.4 enrich: dimension on ``customers``; join key pair customer_id/id."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    assert _compile(query, dialect) == snapshot


@pytest.mark.golden
@pytest.mark.parametrize("dialect", _DIALECTS, ids=lambda d: d.name.lower())
def test_sql__two_fact_merge_on_shared_dimension(snapshot, dialect: Dialect) -> None:
    """§4.11 chasm-trap safety: two facts, merge on shared region."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    assert _compile(query, dialect) == snapshot


@pytest.mark.golden
@pytest.mark.parametrize("dialect", _DIALECTS, ids=lambda d: d.name.lower())
def test_sql__where_pushed_below_aggregate(snapshot, dialect: Dialect) -> None:
    """WHERE is materialised as a FILTER step below the aggregate."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=_sql("orders.amount > 100"),
    )
    assert _compile(query, dialect) == snapshot


@pytest.mark.golden
@pytest.mark.parametrize("dialect", _DIALECTS, ids=lambda d: d.name.lower())
def test_sql__order_by_and_limit(snapshot, dialect: Dialect) -> None:
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
    assert _compile(query, dialect) == snapshot
