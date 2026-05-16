"""Unit tests for :func:`osi.diagnostics.explain`."""

from __future__ import annotations

import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.diagnostics import explain, explain_json
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


def _plan(query: SemanticQuery):
    return plan(query, orders_context())


def test_explain__lists_one_line_per_step() -> None:
    p = _plan(
        SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
        )
    )
    text = explain(p)
    for step in p.steps:
        alias = f"step_{step.step_id:03d}"
        assert alias in text


def test_explain__renders_aliases_that_match_codegen() -> None:
    """Aliases must be ``step_###`` so traces correlate with SQL CTEs."""
    p = _plan(
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        )
    )
    view = explain_json(p)
    assert view["root"] == f"step_{p.root_step_id:03d}"
    assert all(s["alias"].startswith("step_") for s in view["steps"])


def test_explain__shows_grain_for_every_step() -> None:
    p = _plan(
        SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
        )
    )
    view = explain_json(p)
    for step in view["steps"]:
        assert "grain" in step
        assert isinstance(step["grain"], list)


def test_explain__enrich_summary_uses_paired_keys() -> None:
    """Paired keys must render as ``parent=child`` — never ``k=k``."""
    p = _plan(
        SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        )
    )
    view = explain_json(p)
    enrich = next(s for s in view["steps"] if s["operation"] == "enrich")
    assert "customer_id=id" in enrich["summary"]


def test_explain__captures_order_by_and_limit() -> None:
    p = _plan(
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
        )
    )
    text = explain(p)
    assert "DESC" in text
    assert "limit=5" in text


def test_explain__is_deterministic() -> None:
    q = SemanticQuery(
        dimensions=(_ref("orders", "status"),),
        measures=(_ref("orders", "total_revenue"),),
        where=_sql("orders.amount > 100"),
    )
    a = explain(_plan(q))
    b = explain(_plan(q))
    assert a == b
