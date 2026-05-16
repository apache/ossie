"""TPC-DS Foundation E2E tests.

Each test corresponds to a query that is natively expressible in the
OSI Foundation — simple star-schema aggregates, optional single-hop
enrichment, optional filter/ORDER/LIMIT. Queries outside the thin
slice (correlated subqueries, window functions, GROUPING SETS) are
deliberately not covered here; :class:`E1105` is their contract.

The ten labels below are chosen to fuzz through the representative
query shapes — single-fact aggregate, multi-dim enrichment, filtered
aggregate, top-N, multi-fact merge, etc. They're *spiritually* drawn
from TPC-DS Q1/3/6/7/19/26/42/52/55/73 but simplified to what the thin
slice guarantees.
"""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from tests.e2e.tpcds_fixtures import load_tpcds_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


def _run(conn: duckdb.DuckDBPyConnection, query: SemanticQuery) -> list[tuple]:
    ctx = load_tpcds_context()
    sql = compile_plan(plan(query, ctx), dialect=Dialect.DUCKDB)
    return sorted(conn.execute(sql).fetchall())


@pytest.mark.e2e
def test_tpcds__q52_total_sales_by_item_category(duckdb_tpcds) -> None:
    """Q52-like: SUM(sales) grouped by item.i_category via single enrichment."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_category"),),
        measures=(_ref("store_sales", "total_sales"),),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("Books", 60.0),
        ("Music", 55.0),
        ("Sports", 150.0),
    ]


@pytest.mark.e2e
def test_tpcds__q42_sales_by_category_with_filter(duckdb_tpcds) -> None:
    """Q42-like: SUM(sales) by category, filtered by ss_quantity > 1."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_category"),),
        measures=(_ref("store_sales", "total_sales"),),
        where=_sql("store_sales.ss_quantity > 1"),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("Books", 50.0),
        ("Music", 40.0),
        ("Sports", 100.0),
    ]


@pytest.mark.e2e
def test_tpcds__q3_sales_and_profit_by_brand(duckdb_tpcds) -> None:
    """Q3-like: multi-measure aggregate by item.i_brand."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_brand"),),
        measures=(
            _ref("store_sales", "total_sales"),
            _ref("store_sales", "total_profit"),
        ),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("acme", 60.0, 22.0),
        ("fit", 150.0, 60.0),
        ("zen", 55.0, 20.0),
    ]


@pytest.mark.e2e
def test_tpcds__q7_sales_by_customer_country(duckdb_tpcds) -> None:
    """Q7-like: enrichment over customer, aggregate by country."""
    q = SemanticQuery(
        dimensions=(_ref("customer", "c_birth_country"),),
        measures=(_ref("store_sales", "total_sales"),),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("CANADA", 50.0),
        ("MEXICO", 100.0),
        ("USA", 115.0),
    ]


@pytest.mark.e2e
def test_tpcds__q26_sales_qty_and_orders_by_store_state(duckdb_tpcds) -> None:
    """Q26-like: enrichment over store; quantities and counts by state."""
    q = SemanticQuery(
        dimensions=(_ref("store", "s_state"),),
        measures=(
            _ref("store_sales", "total_qty"),
            _ref("store_sales", "order_count"),
        ),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("CA", 5, 3),
        ("NY", 4, 2),
        ("ON", 3, 2),
    ]


@pytest.mark.e2e
def test_tpcds__q19_sales_by_category_and_country(duckdb_tpcds) -> None:
    """Q19-like: dual enrichment — item × customer."""
    q = SemanticQuery(
        dimensions=(
            _ref("item", "i_category"),
            _ref("customer", "c_birth_country"),
        ),
        measures=(_ref("store_sales", "total_sales"),),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("Books", "CANADA", 10.0),
        ("Books", "USA", 50.0),
        ("Music", "CANADA", 40.0),
        ("Music", "USA", 15.0),
        ("Sports", "MEXICO", 100.0),
        ("Sports", "USA", 50.0),
    ]


@pytest.mark.e2e
def test_tpcds__q55_top_n_by_sales(duckdb_tpcds) -> None:
    """Q55-like: top-N by measure with ORDER BY DESC + LIMIT."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_category"),),
        measures=(_ref("store_sales", "total_sales"),),
        order_by=(
            OrderBy(
                target=_ref("store_sales", "total_sales"),
                direction=SortDirection.DESC,
            ),
        ),
        limit=2,
    )
    ctx = load_tpcds_context()
    sql = compile_plan(plan(q, ctx), dialect=Dialect.DUCKDB)
    rows = duckdb_tpcds.execute(sql).fetchall()
    assert rows == [("Sports", 150.0), ("Books", 60.0)]


@pytest.mark.e2e
def test_tpcds__q1_net_sales_multi_fact_merge(duckdb_tpcds) -> None:
    """Q1-like (Foundation legal subset): sales and returns on shared state."""
    q = SemanticQuery(
        dimensions=(_ref("store", "s_state"),),
        measures=(
            _ref("store_sales", "total_sales"),
            _ref("store_returns", "total_returns"),
        ),
    )
    rows = _run(duckdb_tpcds, q)
    # CA (store 10): sales 20+15+100=135, returns 25 (ticket 1005)
    # NY (store 11): sales 30+50=80,    returns 50 (ticket 1002)
    # ON (store 12): sales 40+10=50,    no returns
    assert rows == [
        ("CA", 135.0, 25.0),
        ("NY", 80.0, 50.0),
        ("ON", 50.0, None),
    ]


@pytest.mark.e2e
def test_tpcds__q6_distinct_customer_count_by_country(duckdb_tpcds) -> None:
    """Q6-like: COUNT(DISTINCT ss_customer_sk) by customer birth country."""
    q = SemanticQuery(
        dimensions=(_ref("customer", "c_birth_country"),),
        measures=(_ref("store_sales", "distinct_customers"),),
    )
    rows = _run(duckdb_tpcds, q)
    assert rows == [
        ("CANADA", 1),
        ("MEXICO", 1),
        ("USA", 2),
    ]


@pytest.mark.e2e
def test_tpcds__q73_avg_ticket_by_preferred_flag(duckdb_tpcds) -> None:
    """Q73-like (Foundation legal): AVG by preferred-customer flag."""
    q = SemanticQuery(
        dimensions=(_ref("customer", "c_preferred_cust_flag"),),
        measures=(_ref("store_sales", "avg_ticket"),),
    )
    rows = _run(duckdb_tpcds, q)
    # Preferred (Y): customers 1 and 3 -> sales 20,15,40,10 -> avg 21.25
    # Non-preferred (N): customers 2 and 4 -> sales 30,50,100 -> avg 60
    assert rows == [
        ("N", pytest.approx(60.0)),
        ("Y", pytest.approx(21.25)),
    ]
