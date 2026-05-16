"""Metric → fact-dataset dispatch.

Given a :class:`ResolvedMetric`, this module computes which dataset its
expression actually reads from. The Foundation requires that every
metric resolve to *exactly one* fact dataset:

* For an aggregate metric (``SUM(orders.amount)``), the aggregate's
  argument columns must all live on the same dataset, otherwise we
  emit ``E1209_CROSS_DATASET_AD_HOC_AGGREGATE`` — composing across
  datasets requires explicit per-dataset metrics first.
* For a composite metric (``revenue - returns``), each referenced base
  metric is resolved recursively and its fact must agree.
* For a count-star or argument-less metric whose dataset cannot be
  inferred, we emit ``E1212_COUNT_STAR_AMBIGUOUS``.

Extracted from ``planner.py`` in S-11 to keep the planner under the
600 LOC cleanliness gate and to make this dispatch independently
mockable in tests.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning.metric_shape import (
    AggregateMetric,
    CompositeMetric,
    classify_metric,
    resolve_metric_by_name,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedMetric


def metric_fact_dataset(m: ResolvedMetric, context: PlannerContext) -> Identifier:
    """Determine which dataset ``m``'s expression reads from.

    For an aggregate metric: the aggregate argument's column references
    must resolve to fields of exactly one dataset. Mixed-dataset
    metrics raise ``E1209_CROSS_DATASET_AD_HOC_AGGREGATE``.

    For a composite metric (``§5.4``): every referenced base metric's
    fact dataset must agree; mismatches also raise ``E1209``.
    """
    if m.dataset is not None:
        return m.dataset
    shape = classify_metric(m.metric, context.namespace)
    if isinstance(shape, AggregateMetric):
        return _aggregate_fact_dataset(
            metric_name=m.metric.name,
            arg_columns=shape.arg_columns,
            context=context,
        )
    candidates = _composite_fact_datasets(shape, context)
    if not candidates:
        raise OSIPlanningError(
            ErrorCode.E1212_COUNT_STAR_AMBIGUOUS,
            f"composite metric {m.metric.name!r} has no resolvable fact dataset",
            context={"metric": m.metric.name},
        )
    if len(candidates) > 1:
        raise OSIPlanningError(
            ErrorCode.E1209_CROSS_DATASET_AD_HOC_AGGREGATE,
            f"composite metric {m.metric.name!r} references base metrics from "
            f"multiple datasets {sorted(str(c) for c in candidates)}; split "
            "into per-dataset composites",
            context={
                "metric": m.metric.name,
                "datasets": sorted(str(c) for c in candidates),
            },
        )
    return next(iter(candidates))


def _aggregate_fact_dataset(
    *,
    metric_name: Identifier,
    arg_columns: tuple[exp.Column, ...],
    context: PlannerContext,
) -> Identifier:
    candidates: set[Identifier] = set()
    for col in arg_columns:
        if col.table:
            candidates.add(normalize_identifier(col.table))
            continue
        candidates.add(context.namespace.resolve_bare(normalize_identifier(col.name)))
    if not candidates:
        raise OSIPlanningError(
            ErrorCode.E1212_COUNT_STAR_AMBIGUOUS,
            f"metric {metric_name!r} has no fact columns; "
            "declare it on a dataset or add an explicit argument",
            context={"metric": metric_name},
        )
    if len(candidates) > 1:
        raise OSIPlanningError(
            ErrorCode.E1209_CROSS_DATASET_AD_HOC_AGGREGATE,
            f"metric {metric_name!r} reads fields from multiple datasets "
            f"{sorted(str(c) for c in candidates)}; decompose into per-dataset "
            "metrics first",
            context={
                "metric": metric_name,
                "datasets": sorted(str(c) for c in candidates),
            },
        )
    return next(iter(candidates))


def _composite_fact_datasets(
    composite: CompositeMetric, context: PlannerContext
) -> set[Identifier]:
    candidates: set[Identifier] = set()
    for ref in composite.references:
        ref_metric, owner = resolve_metric_by_name(
            name=ref.name, dataset=ref.dataset, namespace=context.namespace
        )
        if owner is not None:
            candidates.add(owner)
            continue
        candidates.add(
            metric_fact_dataset(
                ResolvedMetric(dataset=None, metric=ref_metric), context
            )
        )
    return candidates


__all__ = ["metric_fact_dataset"]
