"""Unit + property coverage for ``osi.planning.windows``.

The S-12 module ``windows.py`` is purely shape-analysis — no I/O, no
side effects, deterministic. That makes it a high-value target for
property-style coverage: the rejection rules should hold over a wide
range of expression shapes, not just the hand-picked positive cases
in the compliance suite.

This file pairs targeted unit tests (one per public function) with
small Hypothesis property tests that exercise the boundary between
"window present" and "window absent".
"""

from __future__ import annotations

import sqlglot
from hypothesis import given
from hypothesis import strategies as st

from osi.planning.windows import (
    contains_window,
    first_deferred_frame_clause,
    first_nested_window,
    is_windowed_expression,
    references_windowed_metric,
)


def _parse(sql: str):
    return sqlglot.parse_one(sql)


# ---------------------------------------------------------------------------
# contains_window
# ---------------------------------------------------------------------------


class TestContainsWindow:
    def test_true_for_simple_window(self) -> None:
        assert contains_window(_parse("SUM(amount) OVER (PARTITION BY a)"))

    def test_true_for_window_inside_arithmetic(self) -> None:
        assert contains_window(_parse("SUM(x) OVER () + 1"))

    def test_false_for_no_window(self) -> None:
        assert not contains_window(_parse("SUM(amount)"))

    def test_false_for_plain_column(self) -> None:
        assert not contains_window(_parse("orders.amount"))


# ---------------------------------------------------------------------------
# is_windowed_expression
# ---------------------------------------------------------------------------


class TestIsWindowedExpression:
    def test_true_for_top_level_window(self) -> None:
        assert is_windowed_expression(_parse("ROW_NUMBER() OVER ()"))

    def test_false_when_window_inside_arithmetic(self) -> None:
        # ``SUM(x) OVER () + 1`` is *not* a windowed expression at the
        # top level — it's an Add whose lhs happens to be a window.
        # composition rules use this distinction.
        assert not is_windowed_expression(_parse("SUM(x) OVER () + 1"))

    def test_false_for_plain_aggregate(self) -> None:
        assert not is_windowed_expression(_parse("SUM(x)"))


# ---------------------------------------------------------------------------
# first_nested_window
# ---------------------------------------------------------------------------


class TestFirstNestedWindow:
    def test_detects_window_in_window_argument(self) -> None:
        nested = first_nested_window(
            _parse(
                "SUM(SUM(amount) OVER (PARTITION BY a)) OVER (PARTITION BY b)"
            )
        )
        assert nested is not None

    def test_no_match_for_simple_window(self) -> None:
        assert first_nested_window(_parse("SUM(amount) OVER ()")) is None

    def test_no_match_for_aggregate_alone(self) -> None:
        assert first_nested_window(_parse("SUM(amount)")) is None


# ---------------------------------------------------------------------------
# first_deferred_frame_clause
# ---------------------------------------------------------------------------


class TestFirstDeferredFrameClause:
    def test_no_match_for_rows_frame(self) -> None:
        result = first_deferred_frame_clause(
            _parse(
                "SUM(x) OVER (ORDER BY a "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            )
        )
        assert result is None

    def test_no_match_for_range_frame(self) -> None:
        result = first_deferred_frame_clause(
            _parse(
                "SUM(x) OVER (ORDER BY a "
                "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            )
        )
        assert result is None

    def test_no_match_for_no_frame(self) -> None:
        assert first_deferred_frame_clause(_parse("SUM(x) OVER ()")) is None


# ---------------------------------------------------------------------------
# references_windowed_metric
# ---------------------------------------------------------------------------


class TestReferencesWindowedMetric:
    def test_finds_bare_reference(self) -> None:
        names = frozenset({"running_total"})
        result = references_windowed_metric(
            _parse("running_total / SUM(amount)"),
            windowed_metric_names=names,
        )
        assert result == "running_total"

    def test_finds_qualified_reference(self) -> None:
        names = frozenset({"orders.running_total"})
        result = references_windowed_metric(
            _parse("orders.running_total + 1"),
            windowed_metric_names=names,
        )
        assert result == "orders.running_total"

    def test_no_match_when_set_empty(self) -> None:
        result = references_windowed_metric(
            _parse("amount + 1"),
            windowed_metric_names=frozenset(),
        )
        assert result is None

    def test_no_match_when_no_reference(self) -> None:
        result = references_windowed_metric(
            _parse("SUM(amount)"),
            windowed_metric_names=frozenset({"running_total"}),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


_ARITH_OPS = ["+", "-", "*", "/"]
_AGGS = ["SUM", "AVG", "MIN", "MAX", "COUNT"]


@st.composite
def _windowed_expr(draw) -> str:
    agg = draw(st.sampled_from(_AGGS))
    return f"{agg}(amount) OVER (PARTITION BY a)"


@st.composite
def _non_windowed_expr(draw) -> str:
    agg = draw(st.sampled_from(_AGGS))
    op = draw(st.sampled_from(_ARITH_OPS))
    return f"{agg}(amount) {op} 1"


class TestProperties:
    @given(_windowed_expr())
    def test_contains_window_holds_for_every_windowed_form(self, sql: str) -> None:
        assert contains_window(_parse(sql))

    @given(_non_windowed_expr())
    def test_no_window_implies_contains_window_false(self, sql: str) -> None:
        assert not contains_window(_parse(sql))

    @given(_windowed_expr())
    def test_contains_implies_top_level_or_descendant(self, sql: str) -> None:
        # If contains_window is True, then either is_windowed_expression
        # is True (windowed at the top level) OR a descendant has the
        # window. We sample expressions where the window IS top-level,
        # so the property simplifies.
        expr = _parse(sql)
        assert contains_window(expr)
        # A simple "AGG(...) OVER (...)" parses as a top-level Window.
        assert is_windowed_expression(expr)
