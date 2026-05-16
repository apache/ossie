"""Composite-metric expansion for the planner.

Split out from :mod:`osi.planning.planner` to keep that file inside the
600-LOC cap (``INFRA.md §1.2``). Composite metrics
(``Proposed_OSI_Semantics.md §5.4``) are arithmetic combinations of
other declared metrics; they cannot be evaluated by
:func:`~osi.planning.algebra.operations.aggregate` directly and must
be materialised as a post-``AGGREGATE`` ``ADD_COLUMNS`` step over
the base aggregate columns.

Public contract:

* :class:`GroupMeasurePlan` — the per-group split produced by
  :func:`measure_plan_for_group`, consumed by the planner to decide
  whether an ``ADD_COLUMNS`` step is required.
* :func:`measure_plan_for_group` — classify each user-requested
  measure into a base aggregate or a composite; for composites,
  inline nested composite references and collect every transitively
  required base aggregate.
* :func:`replace_metric_expression` — small utility used to funnel
  a qualifier-stripped expression through ``columns`` helpers
  without mutating declared models.

Everything in this module is pure and deterministic. The module does
not emit plan steps; it only *describes* what the planner needs to
emit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import OSIPlanningError
from osi.parsing.models import Metric
from osi.planning.columns import strip_column_qualifiers
from osi.planning.metric_shape import (
    AggregateMetric,
    CompositeMetric,
    classify_metric,
    resolve_metric_by_name,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedMetric


@dataclass(frozen=True, slots=True)
class BaseAggregate:
    """A single base aggregate column to emit under AGGREGATE."""

    dataset: Identifier
    metric: Metric


@dataclass(frozen=True, slots=True)
class CompositeDefinition:
    """A derived metric emitted by ADD_COLUMNS after AGGREGATE.

    ``expression`` is already fully inlined (nested composites have
    been flattened) and qualifier-stripped; it addresses the base
    aggregate column names directly.
    """

    name: Identifier
    metric: Metric
    expression: FrozenSQL


@dataclass(frozen=True, slots=True)
class GroupMeasurePlan:
    """Per-group breakdown of how to realise the user's measures."""

    base_aggregates: tuple[BaseAggregate, ...]
    composite_definitions: tuple[CompositeDefinition, ...]


def measure_plan_for_group(
    *,
    measures: tuple[ResolvedMetric, ...],
    fact_ds: Identifier,
    context: PlannerContext,
) -> GroupMeasurePlan:
    """Split ``measures`` into base aggregates + composite definitions.

    Base aggregates are de-duplicated and kept in first-seen order;
    composites are emitted in the order the user declared them. That
    ordering is what ultimately lands in the plan's ``AGGREGATE`` and
    ``ADD_COLUMNS`` payloads, so golden-test stability depends on it.
    """
    base_order: list[Identifier] = []
    base_seen: dict[Identifier, BaseAggregate] = {}
    composites: list[CompositeDefinition] = []

    def _add_base(name: Identifier, metric_obj: Metric, dataset: Identifier) -> None:
        if name in base_seen:
            return
        base_order.append(name)
        base_seen[name] = BaseAggregate(dataset=dataset, metric=metric_obj)

    for resolved in measures:
        shape = classify_metric(resolved.metric, context.namespace)
        if isinstance(shape, AggregateMetric):
            ds = resolved.dataset if resolved.dataset is not None else fact_ds
            _add_base(resolved.metric.name, resolved.metric, ds)
            continue
        assert isinstance(shape, CompositeMetric)
        # Inline every nested composite reference so the final derived
        # expression only references base aggregate column names. A
        # two-level case such as ``avg_doubled = 2 * avg_order_value``
        # (where ``avg_order_value`` is itself composite) becomes
        # ``2 * (total_revenue / NULLIF(order_count, 0))``. That keeps
        # ADD_COLUMNS as a single flat step regardless of nesting depth.
        inlined = _inline_composite_refs(
            expression=shape.expression, context=context, fact_ds=fact_ds
        )
        stripped = strip_column_qualifiers(inlined)
        composites.append(
            CompositeDefinition(
                name=resolved.metric.name,
                metric=resolved.metric,
                expression=stripped,
            )
        )
        _walk_composite_bases(
            shape=shape, context=context, fact_ds=fact_ds, adder=_add_base
        )

    ordered_bases = tuple(base_seen[n] for n in base_order)
    return GroupMeasurePlan(
        base_aggregates=ordered_bases,
        composite_definitions=tuple(composites),
    )


def replace_metric_expression(*, metric: Metric, new_expr: FrozenSQL) -> Metric:
    """Return a shallow clone of ``metric`` carrying ``new_expr``.

    :class:`Metric` is a frozen pydantic model, so we construct a
    fresh one. Used only to funnel a qualifier-stripped expression
    through existing helpers without mutating declared models.
    """
    return metric.model_copy(update={"expression": new_expr})


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _walk_composite_bases(
    *,
    shape: CompositeMetric,
    context: PlannerContext,
    fact_ds: Identifier,
    adder: Callable[[Identifier, Metric, Identifier], None],
) -> None:
    """Add every transitively-referenced base aggregate via ``adder``."""
    for ref in shape.references:
        ref_metric, owner = resolve_metric_by_name(
            name=ref.name, dataset=ref.dataset, namespace=context.namespace
        )
        owner_dataset = owner if owner is not None else fact_ds
        ref_shape = classify_metric(ref_metric, context.namespace)
        if isinstance(ref_shape, AggregateMetric):
            adder(ref_metric.name, ref_metric, owner_dataset)
            continue
        _walk_composite_bases(
            shape=ref_shape, context=context, fact_ds=fact_ds, adder=adder
        )


def _inline_composite_refs(
    *, expression: FrozenSQL, context: PlannerContext, fact_ds: Identifier
) -> FrozenSQL:
    """Inline composite-metric references into ``expression``.

    Returns a :class:`FrozenSQL` with every composite-metric
    reference replaced by that metric's (transitively inlined)
    expression. Base aggregate references are left alone — they
    address the aggregate column names on the prior AGGREGATE step.
    """

    def _rewrite(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        name = normalize_identifier(node.name)
        dataset = normalize_identifier(node.table) if node.table else None
        try:
            ref_metric, _owner = resolve_metric_by_name(
                name=name, dataset=dataset, namespace=context.namespace
            )
        except OSIPlanningError:
            return node
        ref_shape = classify_metric(ref_metric, context.namespace)
        if not isinstance(ref_shape, CompositeMetric):
            return node
        inner = _inline_composite_refs(
            expression=ref_shape.expression, context=context, fact_ds=fact_ds
        )
        return inner.expr.copy()

    rewritten = expression.expr.copy().transform(_rewrite)
    return FrozenSQL.of(rewritten)


__all__ = [
    "BaseAggregate",
    "CompositeDefinition",
    "GroupMeasurePlan",
    "measure_plan_for_group",
    "replace_metric_expression",
]
