"""Metric classification — aggregate vs. composite.

The Foundation supports two metric shapes (``Proposed_OSI_Semantics.md
§5.4``):

1. **Aggregate metric** — top-level expression is a single aggregate
   function (``SUM``, ``COUNT``, ``COUNT(DISTINCT …)``, ``COUNT(*)``,
   ``MIN``, ``MAX``, ``AVG``) applied to a fact expression. This is
   the base case — it produces a column under ``aggregate()``.

2. **Composite metric** — an arithmetic expression whose every leaf
   reference names another declared metric. Composites implement
   ratios, percentages, and deltas and are computed *after*
   :func:`~osi.planning.algebra.operations.aggregate` via
   :func:`~osi.planning.algebra.operations.add_columns`.

Anything else (bare-fact references in non-aggregate context, nested
aggregate functions, references to undeclared names) is a hard
``E1206`` failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIParseError, OSIPlanningError
from osi.parsing.models import Metric
from osi.parsing.namespace import Namespace
from osi.planning.algebra.state import AggregateFunction

_AGG_BY_AST: dict[type[exp.Expression], AggregateFunction] = {
    exp.Sum: AggregateFunction.SUM,
    exp.Count: AggregateFunction.COUNT,
    exp.Min: AggregateFunction.MIN,
    exp.Max: AggregateFunction.MAX,
    exp.Avg: AggregateFunction.AVG,
}


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    """A metric whose expression is a top-level aggregate function."""

    function: AggregateFunction
    arg_columns: tuple[exp.Column, ...]


@dataclass(frozen=True, slots=True)
class MetricRef:
    """A reference to another declared metric used inside a composite."""

    name: Identifier
    dataset: Identifier | None


@dataclass(frozen=True, slots=True)
class CompositeMetric:
    """An arithmetic combination of other declared metrics (``§5.4``).

    The inlined reference list is in source order for deterministic
    planning. ``expression`` is the original AST; every
    :class:`~sqlglot.expressions.Column` leaf corresponds to one entry
    in ``references``.
    """

    expression: FrozenSQL
    references: tuple[MetricRef, ...]


MetricShape = AggregateMetric | CompositeMetric


def classify_metric(metric: Metric, namespace: Namespace) -> MetricShape:
    """Determine whether ``metric`` is aggregate or composite.

    Raises :class:`OSIPlanningError` with
    :attr:`ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE` for any shape the
    Foundation does not accept (undeclared reference, mixed shape,
    nested aggregate inside a composite, etc.). A top-level aggregate
    whose function is in the OSI_SQL_2026 parse whitelist but does not
    yet have a planner lowering (``MEDIAN``, ``STDDEV``,
    ``PERCENTILE_CONT``, …) is rejected here with
    :attr:`ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT` so that the
    diagnostic surface matches the architectural reality (the parser
    knows the function, the planner does not yet model it). This
    closes the Phase 8 finding I1 — without it the composite path
    fires later with the misleading
    ``E1206_METRIC_IN_RAW_AGGREGATE`` message.
    """
    top = metric.expression.expr
    _reject_unsupported_top_level_aggregate(metric=metric, top=top)
    agg = _as_top_level_aggregate(top)
    if agg is not None:
        return agg
    refs = _collect_composite_refs(metric=metric, expression=top, namespace=namespace)
    _reject_nested_aggregates(metric=metric, expression=top)
    return CompositeMetric(expression=metric.expression, references=refs)


def _reject_unsupported_top_level_aggregate(
    *, metric: Metric, top: exp.Expression
) -> None:
    """Reject whitelisted-but-unsupported aggregates at the metric root.

    A metric body whose root AST node is a SQLGlot aggregate function
    (``exp.AggFunc``) but not one of the five operators the planner
    models (``SUM`` / ``COUNT`` / ``MIN`` / ``MAX`` / ``AVG``) is
    rejected with ``E1208`` so authors do not see a confusing
    ``E1206_METRIC_IN_RAW_AGGREGATE`` message later from the composite
    path. ``COUNT(*)`` / ``COUNT(DISTINCT …)`` are handled by the
    ``exp.Count`` branch and so reach this check as ``exp.Count``.
    """
    if not isinstance(top, exp.AggFunc):
        return
    if type(top) in _AGG_BY_AST:
        return
    raise OSIPlanningError(
        ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
        (
            f"metric {metric.name!r} uses aggregate function "
            f"{top.key.upper()!r}; the OSI_SQL_2026 parse whitelist "
            "admits this function in expressions, but the planner "
            "currently models only SUM / COUNT / MIN / MAX / AVG / "
            "COUNT(DISTINCT) at the metric root. Decompose the metric "
            "(e.g. AVG instead of MEDIAN), or use one of the supported "
            "aggregates."
        ),
        context={"metric": metric.name, "function": top.key.upper()},
    )


def _as_top_level_aggregate(top: exp.Expression) -> AggregateMetric | None:
    """Return an :class:`AggregateMetric` for a Foundation aggregate, else None.

    Recognises the seven Foundation aggregate shapes (``SUM``, ``AVG``,
    ``MIN``, ``MAX``, ``COUNT``, ``COUNT(*)``, ``COUNT(DISTINCT …)``).
    """
    func = _AGG_BY_AST.get(type(top))
    if func is None:
        return None
    if func is AggregateFunction.COUNT:
        arg = top.this
        if isinstance(arg, exp.Distinct):
            targets = tuple(arg.expressions)
            return AggregateMetric(
                function=AggregateFunction.COUNT_DISTINCT,
                arg_columns=_columns_in(targets),
            )
        if isinstance(arg, exp.Star):
            return AggregateMetric(function=func, arg_columns=())
        targets = (arg,)
    else:
        targets = (top.this,)
    return AggregateMetric(function=func, arg_columns=_columns_in(targets))


def _columns_in(exprs: tuple[exp.Expression, ...]) -> tuple[exp.Column, ...]:
    columns: list[exp.Column] = []
    for target in exprs:
        columns.extend(target.find_all(exp.Column))
    return tuple(columns)


def _collect_composite_refs(
    *, metric: Metric, expression: exp.Expression, namespace: Namespace
) -> tuple[MetricRef, ...]:
    refs: list[MetricRef] = []
    for col in expression.find_all(exp.Column):
        refs.append(
            _resolve_composite_leaf(metric=metric, col=col, namespace=namespace)
        )
    if not refs:
        raise OSIPlanningError(
            ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
            (
                f"metric {metric.name!r} is not a top-level aggregate and does "
                "not reference any other declared metric"
            ),
            context={"metric": metric.name},
        )
    return tuple(refs)


def _resolve_composite_leaf(
    *, metric: Metric, col: exp.Column, namespace: Namespace
) -> MetricRef:
    name = normalize_identifier(col.name)
    if col.table:
        dataset = normalize_identifier(col.table)
        ds_ns = namespace.datasets.get(dataset)
        if ds_ns is None or name not in ds_ns.metrics:
            raise _composite_leaf_error(metric=metric, reference=f"{dataset}.{name}")
        return MetricRef(name=name, dataset=dataset)
    # Bare: a model-scoped metric, or a dataset-scoped metric whose
    # bare name is unambiguous.
    if name in namespace.metrics:
        return MetricRef(name=name, dataset=None)
    try:
        owner = namespace.resolve_bare(name)
    except OSIParseError as exc:
        raise OSIPlanningError(
            ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
            (
                f"metric {metric.name!r}: bare reference {name!r} in composite "
                f"expression does not name a declared metric ({exc})"
            ),
            context={"metric": metric.name, "reference": name},
        ) from exc
    ds_ns = namespace.datasets[owner]
    if name not in ds_ns.metrics:
        raise _composite_leaf_error(metric=metric, reference=f"{owner}.{name}")
    return MetricRef(name=name, dataset=owner)


def _composite_leaf_error(*, metric: Metric, reference: str) -> OSIPlanningError:
    return OSIPlanningError(
        ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
        (
            f"metric {metric.name!r}: composite leaf {reference!r} is not a "
            "declared metric (composite metrics may only reference other "
            "declared metrics, not raw facts)"
        ),
        context={"metric": metric.name, "reference": reference},
    )


def _reject_nested_aggregates(*, metric: Metric, expression: exp.Expression) -> None:
    for node in expression.walk():
        if isinstance(node, (exp.Sum, exp.Count, exp.Min, exp.Max, exp.Avg)):
            raise OSIPlanningError(
                ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
                (
                    f"metric {metric.name!r}: aggregate function "
                    f"{type(node).__name__!r} may only appear at the top level; "
                    "composite metrics are built from metric references, not "
                    "fresh aggregates"
                ),
                context={"metric": metric.name},
            )


def resolve_metric_by_name(
    *, name: Identifier, dataset: Identifier | None, namespace: Namespace
) -> tuple[Metric, Identifier | None]:
    """Look up a metric by name (bare or dataset-qualified).

    Returns the :class:`Metric` object and the owning dataset (or
    ``None`` for model-scoped metrics). Raises ``E2002`` if the name
    does not resolve.
    """
    if dataset is not None:
        ds_ns = namespace.datasets.get(dataset)
        if ds_ns is None or name not in ds_ns.metrics:
            raise OSIPlanningError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"metric {dataset}.{name} is not declared",
                context={"dataset": dataset, "name": name},
            )
        return ds_ns.metrics[name], dataset
    if name in namespace.metrics:
        return namespace.metrics[name], None
    # Fall through: try dataset-scoped unambiguous bare name.
    try:
        owner = namespace.resolve_bare(name)
    except OSIParseError as exc:
        raise OSIPlanningError(
            ErrorCode.E2002_NAME_NOT_FOUND,
            f"metric {name!r} is not declared",
            context={"name": name, "reason": str(exc)},
        ) from exc
    ds_ns = namespace.datasets[owner]
    if name not in ds_ns.metrics:
        raise OSIPlanningError(
            ErrorCode.E2002_NAME_NOT_FOUND,
            f"metric {name!r} is not declared",
            context={"name": name, "owner": owner},
        )
    return ds_ns.metrics[name], owner


__all__ = [
    "AggregateMetric",
    "CompositeMetric",
    "MetricRef",
    "MetricShape",
    "classify_metric",
    "resolve_metric_by_name",
]
