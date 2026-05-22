"""Tests for parameter substitution and named-filter inlining.

These are pre-classification AST rewrites — they happen before the
classifier splits predicates into row-level / semi-join / post-aggregate
buckets. The tests target the rewriter directly (unit) *and* the
plan-level observable behaviour (integration) so a regression shows
up at both layers.
"""

from __future__ import annotations

import textwrap

import pytest
import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.parser import parse_semantic_model
from osi.planning import PlanOperation, Reference, SemanticQuery, plan
from osi.planning.planner_context import PlannerContext
from osi.planning.preprocess import inline_named_filters, substitute_parameters


def _sql(txt: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(txt))


_MODEL_WITH_PARAMS_AND_FILTERS = textwrap.dedent("""\
    semantic_model:
      - name: demo
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [order_id]
            fields:
              - {name: order_id,    expression: order_id,    role: dimension}
              - {name: status,      expression: status,      role: dimension}
              - {name: amount,      expression: amount,      role: fact}
            metrics:
              - {name: total_revenue, expression: SUM(amount)}
        filters:
          - {name: completed_orders, expression: "status = 'completed'"}
        parameters:
          - {name: min_amount, data_type: NUMBER, default: 0}
          - {name: region_filter, data_type: STRING}
    """)


def _ctx() -> PlannerContext:
    # Per-dataset ``metrics:`` block in the fixture is deferred under
    # the strict Foundation; opt back in via the legacy-permissive
    # flag set so the preprocess contract stays exercised.
    parsed = parse_semantic_model(
        _MODEL_WITH_PARAMS_AND_FILTERS,
        flags=FoundationFlags.legacy_permissive(),
    )
    return PlannerContext(
        model=parsed.model, namespace=parsed.namespace, graph=parsed.graph
    )


# ---------------------------------------------------------------------------
# Parameter substitution
# ---------------------------------------------------------------------------


class TestSubstituteParameters:
    def test_placeholder_replaced_with_provided_value(self) -> None:
        ctx = _ctx()
        out = substitute_parameters(
            _sql("amount > :min_amount"),
            provided={normalize_identifier("min_amount"): 250},
            declared=ctx.model.parameters,
        )
        assert out is not None
        assert out.canonical == _sql("amount > 250").canonical

    def test_placeholder_uses_default_when_not_provided(self) -> None:
        ctx = _ctx()
        out = substitute_parameters(
            _sql("amount > :min_amount"),
            provided={},
            declared=ctx.model.parameters,
        )
        assert out is not None
        assert out.canonical == _sql("amount > 0").canonical

    def test_missing_value_and_no_default_raises_E1002(self) -> None:
        ctx = _ctx()
        with pytest.raises(OSIPlanningError) as excinfo:
            substitute_parameters(
                _sql("status = :region_filter"),
                provided={},
                declared=ctx.model.parameters,
            )
        assert excinfo.value.code is ErrorCode.E1002_MISSING_REQUIRED_FIELD

    def test_unknown_placeholder_raises_E2002(self) -> None:
        ctx = _ctx()
        with pytest.raises(OSIPlanningError) as excinfo:
            substitute_parameters(
                _sql("amount > :nonexistent"),
                provided={},
                declared=ctx.model.parameters,
            )
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_unknown_provided_name_raises_E2002(self) -> None:
        ctx = _ctx()
        with pytest.raises(OSIPlanningError) as excinfo:
            substitute_parameters(
                _sql("amount > 100"),
                provided={normalize_identifier("nope"): 1},
                declared=ctx.model.parameters,
            )
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_none_expression_still_validates_provided_names(self) -> None:
        ctx = _ctx()
        # No expression, but invalid provided name → still must reject.
        with pytest.raises(OSIPlanningError) as excinfo:
            substitute_parameters(
                None,
                provided={normalize_identifier("nope"): 1},
                declared=ctx.model.parameters,
            )
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_plan_level_parameter_substitution_produces_filter_step(self) -> None:
        ctx = _ctx()
        q = SemanticQuery(
            dimensions=(
                Reference(
                    dataset=normalize_identifier("orders"),
                    name=normalize_identifier("status"),
                ),
            ),
            measures=(
                Reference(
                    dataset=normalize_identifier("orders"),
                    name=normalize_identifier("total_revenue"),
                ),
            ),
            where=_sql("amount > :min_amount"),
            parameters={normalize_identifier("min_amount"): 150},
        )
        p = plan(q, ctx)
        filt_step = next(s for s in p.steps if s.operation is PlanOperation.FILTER)
        # The substituted literal reaches the filter payload verbatim.
        assert "150" in filt_step.payload.predicate.canonical


# ---------------------------------------------------------------------------
# Named filter inlining
# ---------------------------------------------------------------------------


class TestInlineNamedFilters:
    def test_bare_reference_replaced_by_filter_expression(self) -> None:
        ctx = _ctx()
        out = inline_named_filters(
            _sql("completed_orders"),
            filters=ctx.model.filters,
            field_names=frozenset(),
        )
        assert out is not None
        assert out.canonical == ctx.model.filters[0].expression.canonical

    def test_qualified_reference_is_left_alone(self) -> None:
        """Preserve dataset-qualified references during inlining.

        ``orders.completed_orders`` is a dataset-qualified field / metric
        reference; named-filter inlining must not steal it even if the
        bare name matches a declared filter.
        """
        ctx = _ctx()
        out = inline_named_filters(
            _sql("orders.completed_orders"),
            filters=ctx.model.filters,
            field_names=frozenset(),
        )
        assert out is not None
        assert out.canonical == _sql("orders.completed_orders").canonical

    def test_bare_reference_that_collides_with_field_is_left_alone(self) -> None:
        ctx = _ctx()
        out = inline_named_filters(
            _sql("completed_orders"),
            filters=ctx.model.filters,
            field_names=frozenset({normalize_identifier("completed_orders")}),
        )
        assert out is not None
        # Unchanged — field wins.
        assert out.canonical == _sql("completed_orders").canonical

    def test_plan_level_filter_inlining_produces_filter_step(self) -> None:
        ctx = _ctx()
        q = SemanticQuery(
            dimensions=(
                Reference(
                    dataset=normalize_identifier("orders"),
                    name=normalize_identifier("status"),
                ),
            ),
            measures=(
                Reference(
                    dataset=normalize_identifier("orders"),
                    name=normalize_identifier("total_revenue"),
                ),
            ),
            where=_sql("completed_orders"),
        )
        p = plan(q, ctx)
        filt_step = next(s for s in p.steps if s.operation is PlanOperation.FILTER)
        assert "'completed'" in filt_step.payload.predicate.canonical
