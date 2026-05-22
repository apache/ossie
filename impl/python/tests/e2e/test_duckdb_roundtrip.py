"""DuckDB roundtrip tests — plan → compile → execute → assert rows.

These are the *behavioural* tests: every plan/SQL golden has a
counterpart here that asserts the query returns the rows we expect
against a seeded DuckDB instance. When a SQL golden changes, the
corresponding E2E test is the safety net that catches semantic drift
(the SQL shape is different, but does it still compute the same
answer?).

We always compile with :attr:`Dialect.DUCKDB` because we're executing
on DuckDB; cross-dialect rendering is covered by the golden layer.
"""

from __future__ import annotations

import duckdb
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


def _run(conn: duckdb.DuckDBPyConnection, query: SemanticQuery) -> list[tuple]:
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.DUCKDB)
    return sorted(conn.execute(sql).fetchall())


@pytest.mark.e2e
def test_e2e__single_table_dim_plus_measure(duckdb_sales) -> None:
    """SUM(amount) grouped by status, no joins."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    rows = _run(duckdb_sales, query)
    assert rows == [
        ("paid", 650.0),
        ("pending", 200.0),
    ]


@pytest.mark.e2e
def test_e2e__enrichment_dim_on_joined_table(duckdb_sales) -> None:
    """SUM(amount) grouped by customers.region — enrich join path."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    rows = _run(duckdb_sales, query)
    assert rows == [
        ("APAC", 125.0),
        ("EMEA", 300.0),
        ("NA", 425.0),
    ]


@pytest.mark.e2e
def test_e2e__two_fact_merge_on_shared_dimension(duckdb_sales) -> None:
    """Chasm-trap safety: two facts merged on region, each aggregated first."""
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    rows = _run(duckdb_sales, query)
    assert rows == [
        ("APAC", 125.0, 10.0),
        ("EMEA", 300.0, 50.0),
        ("NA", 425.0, 20.0),
    ]


@pytest.mark.e2e
def test_e2e__where_pushed_below_aggregate(duckdb_sales) -> None:
    """WHERE amount > 100 filters rows before aggregate."""
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=_sql("orders.amount > 100"),
    )
    rows = _run(duckdb_sales, query)
    assert rows == [
        ("paid", 500.0),
        ("pending", 125.0),
    ]


@pytest.mark.e2e
def test_e2e__composite_metric_avg_order_value(duckdb_sales) -> None:
    """Composite metric ``avg_order_value = total_revenue / order_count``.

    Exercises the AGGREGATE + ADD_COLUMNS path end-to-end: both base
    aggregates land under AGGREGATE, and the derived ratio is computed
    on a subsequent ADD_COLUMNS step whose leaves address the aggregate
    column names directly.
    """
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            Reference(dataset=None, name=normalize_identifier("avg_order_value")),
        ),
    )
    rows = _run(duckdb_sales, query)
    # Seed: APAC has 1 order (125), EMEA has 1 order (300), NA has 4
    # orders totalling 425 (avg 106.25).
    assert len(rows) == 3
    expected = {"APAC": 125.0, "EMEA": 300.0, "NA": 425.0 / 4}
    for region, avg in rows:
        assert avg == pytest.approx(expected[region], rel=1e-9)


@pytest.mark.e2e
def test_e2e__order_by_and_limit(duckdb_sales) -> None:
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        order_by=(
            OrderBy(
                target=_ref("orders", "total_revenue"),
                direction=SortDirection.DESC,
            ),
        ),
        limit=1,
    )
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.DUCKDB)
    rows = duckdb_sales.execute(sql).fetchall()
    assert rows == [("paid", 650.0)]
