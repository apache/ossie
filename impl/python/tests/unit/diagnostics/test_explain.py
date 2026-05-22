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


def test_payload_summary_covers_every_payload_variant() -> None:
    """Every concrete ``PlanPayload`` variant must have a summary case.

    A missing case would silently render as an empty string in
    ``explain(plan)`` — users would lose visibility into the step.
    Walking the ``PlanPayload`` union catches the gap the moment a new
    payload type lands without an ``explain`` update.
    """
    from osi.diagnostics.explain import _payload_summary
    from osi.planning.algebra.operations import FilterMode, JoinType
    from osi.planning.algebra.state import Column, ColumnKind
    from osi.planning.plan import (
        AddColumnsPayload,
        AggregatePayload,
        BroadcastPayload,
        EnrichDerivedPayload,
        EnrichPayload,
        FilteringJoinPayload,
        FilterPayload,
        MergePayload,
        PlanPayload,
        ProjectPayload,
        SourcePayload,
    )

    sample_column = Column(
        name=normalize_identifier("col_x"),
        expression=_sql("1"),
        kind=ColumnKind.FACT,
        dependencies=frozenset(),
        aggregate=None,
    )
    sample_id = normalize_identifier("x")
    sample_grain = frozenset({sample_id})

    samples: list[object] = [
        SourcePayload(dataset=sample_id, primary_key=sample_grain, source="orders"),
        FilterPayload(
            predicate=_sql("1 = 1"),
            dependencies=frozenset(),
            is_post_aggregate=False,
        ),
        EnrichPayload(
            child_dataset=sample_id,
            child_columns=(sample_column,),
            keys=sample_grain,
            join_type=JoinType.LEFT,
            child_source="customers",
            parent_keys=(sample_id,),
            child_keys=(sample_id,),
        ),
        EnrichDerivedPayload(
            child_columns=(sample_column,),
            keys=sample_grain,
            join_type=JoinType.LEFT,
            parent_keys=(sample_id,),
            child_keys=(sample_id,),
        ),
        AggregatePayload(new_grain=sample_grain, aggregations=(sample_column,)),
        ProjectPayload(columns=(sample_id,)),
        AddColumnsPayload(definitions=(sample_column,)),
        MergePayload(on=sample_grain),
        FilteringJoinPayload(
            lhs_keys=sample_grain, rhs_keys=sample_grain, mode=FilterMode.SEMI
        ),
        BroadcastPayload(column=sample_column),
    ]

    # The union and the sample list must agree — adding a new payload
    # variant without a sample is a test gap, not a passing test.
    expected_variants = set(PlanPayload.__args__)
    sampled_variants = {type(s) for s in samples}
    assert sampled_variants == expected_variants, (
        "The exhaustive-match sample set is out of sync with the "
        f"PlanPayload union.\n  in samples but not in union: "
        f"{sorted(c.__name__ for c in sampled_variants - expected_variants)}\n"
        f"  in union but not in samples: "
        f"{sorted(c.__name__ for c in expected_variants - sampled_variants)}"
    )

    for payload in samples:
        summary = _payload_summary(payload)  # type: ignore[arg-type]
        assert summary, (
            f"_payload_summary returned an empty string for "
            f"{type(payload).__name__}; every payload variant must "
            "have a non-empty trace line."
        )
