"""Unit tests for :func:`osi.diagnostics.resolve`."""

from __future__ import annotations

import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.diagnostics import resolve, resolve_json
from osi.planning import Reference, SemanticQuery
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


def test_resolve__single_table_touches_only_that_table() -> None:
    view = resolve_json(
        SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
        ),
        orders_context(),
    )
    assert view["datasets"] == ["orders"]
    assert view["relationships"] == []
    measure_names = {m["name"] for m in view["measures"]}
    assert measure_names == {"total_revenue"}


def test_resolve__enrichment_surfaces_relationship() -> None:
    view = resolve_json(
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        ),
        orders_context(),
    )
    assert "orders" in view["datasets"]
    assert "customers" in view["datasets"]
    rel_names = {r["name"] for r in view["relationships"]}
    assert "orders_to_customers" in rel_names
    rel = next(r for r in view["relationships"] if r["name"] == "orders_to_customers")
    assert rel["from_columns"] == ["customer_id"]
    assert rel["to_columns"] == ["id"]
    assert rel["join_type"] in ("INNER", "LEFT")


def test_resolve__two_fact_picks_one_relationship_per_fact() -> None:
    view = resolve_json(
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(
                _ref("orders", "total_revenue"),
                _ref("returns", "total_refunds"),
            ),
        ),
        orders_context(),
    )
    rel_names = {r["name"] for r in view["relationships"]}
    assert rel_names == {"orders_to_customers", "returns_to_customers"}


def test_resolve__captures_filter_expression() -> None:
    view = resolve_json(
        SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            where=_sql("orders.amount > 100"),
        ),
        orders_context(),
    )
    assert len(view["filters"]) == 1
    assert "amount" in view["filters"][0]


def test_resolve_text__contains_expected_headers() -> None:
    text = resolve(
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        ),
        orders_context(),
    )
    assert "datasets:" in text
    assert "dimensions:" in text
    assert "measures:" in text
    assert "relationships used:" in text
