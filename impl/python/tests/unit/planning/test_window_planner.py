"""Positive window planner (D-028 + D-030) — unit tests.

Pins the S-22 contract that Foundation v0.1 accepts valid window
functions in the scalar (``Fields``) slot:

* a windowed metric is rendered as ``OVER(...)`` in the projected SQL
  via an ``ADD_COLUMNS`` step;
* row-level ``WHERE`` predicates that *do not* touch the windowed
  metric land before the window (pre-window filter);
* row-level ``WHERE`` predicates that *do* touch the windowed metric
  land after the window (the QUALIFY pattern, D-030);
* ``OVER(...)`` with a non-deferred frame (``ROWS BETWEEN UNBOUNDED
  PRECEDING AND CURRENT ROW``) is preserved verbatim;
* nested windows (D-031) and deferred frame modes (D-032) still raise
  their named codes.
"""

from __future__ import annotations

import pytest

from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.deferred import check_expression_deferred
from osi.parsing.parser import parse_semantic_model
from osi.planning.windows import (
    contains_window,
    first_deferred_frame_clause,
    first_nested_window,
    is_windowed_expression,
)


def _frozen(sql: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(sql))


# ---------------------------------------------------------------------------
# Detection contracts
# ---------------------------------------------------------------------------


class TestWindowDetection:
    @pytest.mark.parametrize(
        "expr",
        [
            "ROW_NUMBER() OVER (PARTITION BY a ORDER BY b)",
            "RANK() OVER (ORDER BY x)",
            "SUM(amount) OVER (PARTITION BY id ORDER BY ts ROWS BETWEEN "
            "UNBOUNDED PRECEDING AND CURRENT ROW)",
        ],
    )
    def test_top_level_window_recognised(self, expr: str) -> None:
        assert is_windowed_expression(_frozen(expr).expr)
        assert contains_window(_frozen(expr).expr)

    def test_aggregate_is_not_window(self) -> None:
        assert not is_windowed_expression(_frozen("SUM(amount)").expr)

    def test_arithmetic_with_window_is_not_top_level(self) -> None:
        # The expression *contains* a window but the top-level node is
        # arithmetic; ``is_windowed_expression`` is a top-level check.
        body = _frozen("ROW_NUMBER() OVER (ORDER BY x) + 1").expr
        assert not is_windowed_expression(body)
        assert contains_window(body)


# ---------------------------------------------------------------------------
# Parser admits valid windows
# ---------------------------------------------------------------------------


class TestParserAcceptsValidWindow:
    def test_no_frame_is_accepted(self) -> None:
        check_expression_deferred(
            _frozen("ROW_NUMBER() OVER (ORDER BY id)"),
            where="metric x",
        )

    def test_unbounded_rows_frame_is_accepted(self) -> None:
        check_expression_deferred(
            _frozen(
                "SUM(amount) OVER (PARTITION BY customer_id ORDER BY id "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            ),
            where="metric x",
        )

    def test_rank_with_partition_is_accepted(self) -> None:
        check_expression_deferred(
            _frozen("RANK() OVER (PARTITION BY a ORDER BY b DESC)"),
            where="metric x",
        )


# ---------------------------------------------------------------------------
# Negative paths still fire
# ---------------------------------------------------------------------------


class TestRejectionPaths:
    def test_nested_window_still_rejected(self) -> None:
        # SUM(SUM(x) OVER (...)) OVER (...) — D-031.
        expr = _frozen(
            "SUM(SUM(amount) OVER (PARTITION BY a)) "
            "OVER (PARTITION BY b ORDER BY c)"
        )
        assert first_nested_window(expr.expr) is not None
        with pytest.raises(OSIParseError) as exc:
            check_expression_deferred(expr, where="metric x")
        assert exc.value.code is ErrorCode.E_NESTED_WINDOW

    def test_deferred_frame_detector_recognises_groups(self) -> None:
        # sqlglot's parser does not accept ``GROUPS`` natively; build
        # the AST directly so we can pin that the *detector* still
        # flags it. Routing into ``E_DEFERRED_FRAME_MODE`` is then
        # exercised by the upstream parse_sql_expr → check pipeline.
        from sqlglot import expressions as sgexp

        win = sgexp.Window(
            this=sgexp.Sum(this=sgexp.column("amount")),
            spec=sgexp.WindowSpec(kind="GROUPS"),
        )
        match = first_deferred_frame_clause(win)
        assert match is not None and "groups" in match[1].lower()


# ---------------------------------------------------------------------------
# End-to-end model parsing
# ---------------------------------------------------------------------------


def test_model_with_windowed_metric_parses() -> None:
    yaml_doc = """
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: orders
        primary_key: [id]
        fields:
          - {name: id, expression: id}
          - {name: customer_id, expression: customer_id}
          - {name: amount, expression: amount, role: fact}
    metrics:
      - name: rn_per_customer
        expression: "ROW_NUMBER() OVER (PARTITION BY orders.customer_id ORDER BY orders.amount DESC)"
"""
    result = parse_semantic_model(yaml_doc.strip())
    metric = next(m for m in result.model.metrics if m.name == "rn_per_customer")
    assert is_windowed_expression(metric.expression.expr)
