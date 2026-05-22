"""Law §4.9 — Enrichment Preserves Parent Rows.

For an N:1 ``enrich`` step the resulting state must contain *exactly*
the same multiset of rows as the parent state — adding RHS columns
must never change the row count. If it does, the join was not
single-valued and the algebra has either accepted a fan-trap (a
correctness bug in :func:`osi.planning.algebra.enrich`) or codegen
emitted something other than a left join (a correctness bug in
:mod:`osi.codegen.transpiler`).

This file lands the first executable version of the law against the
seeded DuckDB schema in ``tests/e2e/conftest.py``. A full
``hypothesis``-driven generator over arbitrary 1:N topologies still
belongs to a follow-up sprint; the curated cases here exercise every
shape of enrich the Foundation supports.
"""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.planning import Reference, SemanticQuery, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _row_count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """Direct row count from the seeded DuckDB table."""
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _grouped_count(conn: duckdb.DuckDBPyConnection, query: SemanticQuery) -> int:
    """Row count of the compiled query against DuckDB."""
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.DUCKDB)
    return len(conn.execute(sql).fetchall())


def test_enrich_preserves_pre_aggregate_row_count(duckdb_sales) -> None:
    """An ``enrich`` that exposes a parent-grain dimension keeps row count.

    Selecting ``orders.order_id`` plus ``customers.region`` runs an
    ``enrich`` from ``orders`` to ``customers`` (N:1) and groups by the
    parent's primary key. Because ``order_id`` is the parent grain, the
    grouped result must have exactly one row per order, i.e. the law
    holds at the *visible* level too.
    """
    parent_rows = _row_count(duckdb_sales, "sales.orders")
    query = SemanticQuery(
        dimensions=(_ref("orders", "order_id"), _ref("customers", "region")),
        measures=(),
    )
    assert _grouped_count(duckdb_sales, query) == parent_rows


def test_enrich_with_filter_preserves_filtered_row_count(duckdb_sales) -> None:
    """A WHERE narrows the parent multiset; enrich preserves what survived."""
    expected = int(
        duckdb_sales.execute(
            "SELECT COUNT(*) FROM sales.orders WHERE status = 'paid'"
        ).fetchone()[0]
    )
    query = SemanticQuery(
        dimensions=(_ref("orders", "order_id"), _ref("customers", "region")),
        measures=(),
        where=FrozenSQL.of(sqlglot.parse_one("orders.status = 'paid'")),
    )
    assert _grouped_count(duckdb_sales, query) == expected


@pytest.mark.parametrize(
    "rhs_dimension,expected_groups",
    [
        ("region", 3),  # NA, EMEA, APAC are present in seeded orders
        ("segment", 2),  # enterprise + smb both present
    ],
)
def test_enrich_aggregate_groups_match_distinct_rhs(
    duckdb_sales, rhs_dimension: str, expected_groups: int
) -> None:
    """Aggregating by an enriched RHS dim must not invent groups.

    The number of distinct values of the RHS dimension *among rows
    actually referenced by the parent fact* is the upper bound on the
    aggregate's row count. Anything larger means enrich exploded; the
    algebra is supposed to make that impossible.
    """
    query = SemanticQuery(
        dimensions=(_ref("customers", rhs_dimension),),
        measures=(_ref("orders", "total_revenue"),),
    )
    assert _grouped_count(duckdb_sales, query) == expected_groups
