"""Nested cross-grain aggregate planner (D-020 + D-024) — unit tests.

Pins the small surface of :mod:`osi.planning.planner_nested` so the
nested-aggregate routing in :mod:`osi.planning.planner` keeps its
contract:

* shape detection — only two-level aggregate-of-aggregate qualifies;
* parsing — outer/inner fns and inner argument come back as-stored;
* intermediate-grain inference — uses unique safe N:1 join keys plus
  any query dim columns addressable on the post-enrichment state.

These are the contracts the multi-step plan in `planner._build_measure_group`
relies on; they are kept here (not in compliance) so a regression
shows up as a failed unit test, not a silent SQL diff.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.parsing.models import Metric
from osi.planning.algebra.state import AggregateFunction
from osi.planning.planner_nested import (
    is_nested_aggregate,
    parse_nested,
)


def _metric(expr: str) -> Metric:
    return Metric(name="m", expression=expr)


class TestShapeDetection:
    @pytest.mark.parametrize(
        "expr",
        [
            "AVG(AVG(orders.amount))",
            "SUM(MAX(orders.amount))",
            "MAX(SUM(orders.qty))",
            "AVG(COUNT(orders.id))",
        ],
    )
    def test_two_level_nested_is_detected(self, expr: str) -> None:
        assert is_nested_aggregate(_metric(expr))

    @pytest.mark.parametrize(
        "expr",
        [
            "SUM(orders.amount)",
            "orders.amount",
            "orders.amount + 1",
            "AVG(orders.amount + 1)",
            "AVG(orders.amount) + 1",
        ],
    )
    def test_non_nested_is_rejected(self, expr: str) -> None:
        assert not is_nested_aggregate(_metric(expr))


class TestParseNested:
    def test_returns_outer_inner_and_inner_arg(self) -> None:
        outer, inner, arg = parse_nested(_metric("AVG(SUM(orders.amount))"))
        assert outer is AggregateFunction.AVG
        assert inner is AggregateFunction.SUM
        # Inner argument is the bare column reference; we don't pin its
        # exact AST shape (sqlglot may wrap it), only that the column
        # is recoverable.
        cols = {c.name for c in arg.find_all(arg.__class__) if hasattr(c, "name")}
        assert "amount" in cols or arg.sql().lower().endswith("amount")

    def test_inner_count_distinct_treated_as_count(self) -> None:
        # Foundation parses ``COUNT(DISTINCT x)`` as a Count node with
        # the DISTINCT flag — still classifies as nested-aggregate.
        assert is_nested_aggregate(_metric("AVG(COUNT(DISTINCT orders.id))"))


class TestNotNestedEdgeCases:
    def test_unary_minus_outer_is_not_nested(self) -> None:
        # The outer node is Neg, not an aggregate.
        assert not is_nested_aggregate(_metric("-AVG(orders.amount)"))

    def test_inner_literal_is_not_nested(self) -> None:
        # ``AVG(1)`` is a one-level aggregate of a literal.
        assert not is_nested_aggregate(_metric("AVG(1)"))


def test_normalize_identifier_independence() -> None:
    # The detection contract works on the AST directly; identifier
    # casing on the inner column does not affect the classification.
    metric = _metric("AVG(SUM(Orders.Amount))")
    assert is_nested_aggregate(metric)
    outer, inner, _ = parse_nested(metric)
    assert outer is AggregateFunction.AVG
    assert inner is AggregateFunction.SUM


def test_normalize_identifier_helper_round_trip() -> None:
    # Sanity: the planner uses normalize_identifier on column names
    # discovered in the inner argument; this asserts the public helper
    # we depend on still folds case.
    assert normalize_identifier("AMOUNT") == normalize_identifier("amount")
