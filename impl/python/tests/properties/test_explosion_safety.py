"""Law §4.10 — Explosion Safety.

The closed algebra promises that every plan it accepts compiles to SQL
that does not silently multiply rows from the parent fact. The tight
operational form of this law:

* For any accepted plan, ``SUM(<additive measure>)`` over the result
  equals ``SUM(<expression>)`` evaluated directly against the parent
  fact's ``source:``.

If the algebra ever lets a 1:N enrich slip through, the join would
duplicate parent rows and the equality breaks. We don't need a
reference interpreter to expose that drift — we just need a measure
whose physical-table answer is computable in pure SQL.

This file replaces the long-skipped placeholder with concrete checks
against the seeded DuckDB schema. A hypothesis-driven generator over
arbitrary topologies is a follow-up; the curated cases here exercise
the join paths the Foundation supports today.
"""

from __future__ import annotations

import duckdb

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.planning import Reference, SemanticQuery, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _semantic_total(conn: duckdb.DuckDBPyConnection, query: SemanticQuery) -> float:
    p = plan(query, orders_context())
    sql = compile_plan(p, dialect=Dialect.DUCKDB)
    rows = conn.execute(sql).fetchall()
    measure_index = len(query.dimensions)
    return float(sum(r[measure_index] for r in rows))


def _physical_total(conn: duckdb.DuckDBPyConnection, expr: str) -> float:
    """Directly compute ``SUM(<expr>)`` against the parent fact table."""
    return float(conn.execute(expr).fetchone()[0])


def test_no_explosion__sum_amount_via_enriched_dim(duckdb_sales) -> None:
    """``SUM(amount)`` grouped by ``customers.region`` must match the parent.

    If enrich exploded, the per-region sums would be inflated by the
    fan-out factor. The total across the result set is the cleanest
    invariant: it is preserved by *any* additive aggregation under a
    non-explosive join.
    """
    semantic = _semantic_total(
        duckdb_sales,
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        ),
    )
    physical = _physical_total(duckdb_sales, "SELECT SUM(amount) FROM sales.orders")
    assert semantic == physical


def test_no_explosion__count_orders_via_enriched_dim(duckdb_sales) -> None:
    """``COUNT(*)`` over orders must equal a direct ``SELECT COUNT(*)``."""
    semantic = _semantic_total(
        duckdb_sales,
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "order_count"),),
        ),
    )
    physical = _physical_total(duckdb_sales, "SELECT COUNT(*) FROM sales.orders")
    assert int(semantic) == int(physical)


def test_no_explosion__sum_via_two_enriched_dims(duckdb_sales) -> None:
    """Adding a second N:1 enrich (region + segment) does not double-count."""
    semantic = _semantic_total(
        duckdb_sales,
        SemanticQuery(
            dimensions=(
                _ref("customers", "region"),
                _ref("customers", "segment"),
            ),
            measures=(_ref("orders", "total_revenue"),),
        ),
    )
    physical = _physical_total(duckdb_sales, "SELECT SUM(amount) FROM sales.orders")
    assert semantic == physical
