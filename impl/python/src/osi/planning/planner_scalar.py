"""Scalar query planner branch (D-011).

A scalar query (``Proposed_OSI_Semantics.md §5.1.2``) selects a list of
``fields`` from the home grain of one anchor dataset, with no
``GROUP BY`` and no aggregation. It emits one row per anchor row.

This module owns the scalar shape only. Aggregation queries continue to
flow through :func:`osi.planning.planner.plan`.

Foundation v0.1 rules enforced here:

* **D-011 / E_AGGREGATE_IN_SCALAR_QUERY** — a ``fields`` entry must
  resolve to a dataset field. A reference that resolves to a
  :class:`~osi.planning.resolve.ResolvedMetric` is rejected.
* **D-023 / E_FAN_OUT_IN_SCALAR_QUERY** — every non-anchor field's
  dataset must be reachable from the anchor via an N:1 enrichment chain.
  Any 1:N or N:N edge ⇒ this code. We translate the
  :class:`~osi.planning.joins.find_enrichment_path` ``E3011`` into the
  scalar-specific code so callers can route on intent.
* **D-010 / E_EMPTY_SCALAR_QUERY** — handled by
  :class:`SemanticQuery.__post_init__`.

The anchor is the *first field's dataset*. Order matters: the user
controls which side of the relationship is the "row" of the scalar
result by listing that dataset's field first. This matches the
spec's intent that scalar queries preserve home-grain rows.
"""

from __future__ import annotations

from typing import Sequence

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.errors import ErrorCode, OSIParseError, OSIPlanningError
from osi.planning.algebra.operations import project
from osi.planning.joins import find_enrichment_path
from osi.planning.plan import (
    OrderByEntry,
    PlanOperation,
    PlanStep,
    ProjectPayload,
    QueryPlan,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import (
    ResolvedDimension,
    ResolvedFact,
    ResolvedMetric,
    resolve_reference,
)
from osi.planning.semantic_query import OrderBy, SemanticQuery, SortDirection
from osi.planning.steps import (
    PlanBuilder,
    enrich_step,
    fact_dataset,
    filter_step,
    source_step,
)


def plan_scalar(query: SemanticQuery, context: PlannerContext) -> QueryPlan:
    """Plan a scalar (Fields-only) query.

    Pure; deterministic given the field order in ``query.fields``.
    """
    resolved = _resolve_fields(query.fields, context)
    anchor = resolved[0].dataset
    other_datasets = frozenset(r.dataset for r in resolved if r.dataset != anchor)

    builder = PlanBuilder()
    enrichment = ()
    if other_datasets:
        try:
            enrichment = find_enrichment_path(
                root=anchor,
                targets=other_datasets,
                graph=context.graph,
            )
        except OSIPlanningError as exc:
            if exc.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED:
                # D-023: a 1:N traversal in a scalar query would
                # multiply the anchor rows. Surface the scalar-specific
                # code so the user sees they need an aggregation query.
                raise OSIPlanningError(
                    ErrorCode.E_FAN_OUT_IN_SCALAR_QUERY,
                    (
                        "scalar query references fields across a 1:N "
                        f"edge from anchor {anchor!r}; the anchor row "
                        "cannot be preserved without aggregation. "
                        "Convert to an aggregation query (Dimensions / "
                        "Measures) or pick the many-side dataset's "
                        "field first to flip the anchor. See "
                        "Proposed_OSI_Semantics.md D-023."
                    ),
                    context={
                        "anchor": anchor,
                        "fan_out_datasets": sorted(str(d) for d in other_datasets),
                    },
                ) from exc
            raise

    fact_ds = fact_dataset(anchor, context)
    current = source_step(fact_ds, builder, context)
    for join in enrichment:
        current = enrich_step(current, join, builder, context)

    # Split filters into pre-window (against base columns only) and
    # post-window (references at least one windowed-metric column).
    # The QUALIFY pattern (D-030) requires the post-window predicate
    # to land *after* the ADD_COLUMNS that introduces the windowed
    # column.
    windowed_metric_names = frozenset(
        r.metric.name for r in resolved if isinstance(r, ResolvedMetric)
    )
    pre_window_predicates, post_window_predicates = _partition_filters(
        query.where, windowed_metric_names, context
    )
    for pred in pre_window_predicates:
        current = filter_step(current, pred, builder)

    # S-22: append windowed-metric definitions as derived ADD_COLUMNS.
    # The window AST passes through codegen unchanged (sqlglot renders
    # ``OVER(...)`` natively); the column kind is DIMENSION because the
    # value is per-row, never per-group.
    windowed_metrics = tuple(r for r in resolved if isinstance(r, ResolvedMetric))
    if windowed_metrics:
        current = _add_windowed_metric_columns(
            current=current,
            windowed_metrics=windowed_metrics,
            builder=builder,
        )

    for pred in post_window_predicates:
        current = filter_step(current, pred, builder)

    output_columns = tuple(_field_or_metric_name(r) for r in resolved)
    projected = builder.add(
        PlanOperation.PROJECT,
        inputs=(current.step_id,),
        state=project(current.state, output_columns),
        payload=ProjectPayload(columns=output_columns),
    )

    order_by = _resolve_order_by(query.order_by, output_columns)
    return QueryPlan(
        steps=builder.steps,
        root_step_id=projected.step_id,
        order_by=order_by,
        limit=query.limit,
        output_columns=output_columns,
    )


def _partition_filters(
    where,
    windowed_metric_names: frozenset[Identifier],
    context: PlannerContext,
):
    """Split row-level WHERE predicates into pre-window vs post-window.

    A predicate is *post-window* iff any of its referenced columns
    (after parsing) names a windowed metric. Everything else is
    pre-window. Subquery / semi-join predicates are not yet supported
    by the scalar planner — if they appear, surface a clean error
    rather than silently dropping them.
    """
    from osi.planning.classify import classify_where

    classified = classify_where(where, context.namespace)
    if classified.semi_joins:
        raise OSIPlanningError(
            ErrorCode.E_AGGREGATE_IN_SCALAR_QUERY,
            "scalar query filters cannot contain EXISTS_IN / NOT_EXISTS_IN; "
            "convert to an aggregation query.",
        )
    pre: list = []
    post: list = []
    for pred in classified.row_level:
        if pred.columns & windowed_metric_names:
            post.append(pred)
        else:
            pre.append(pred)
    return tuple(pre), tuple(post)


def _field_or_metric_name(
    resolved: ResolvedDimension | ResolvedFact | ResolvedMetric,
) -> Identifier:
    if isinstance(resolved, ResolvedMetric):
        return resolved.metric.name
    return resolved.field.name


def _add_windowed_metric_columns(
    *,
    current: PlanStep,
    windowed_metrics: Sequence[ResolvedMetric],
    builder: PlanBuilder,
) -> PlanStep:
    from osi.common.sql_expr import FrozenSQL
    from osi.planning.algebra.composition import add_columns
    from osi.planning.algebra.state import Column, ColumnKind
    from osi.planning.plan import AddColumnsPayload

    state_columns = current.state.column_names
    definitions: list[Column] = []
    for resolved in windowed_metrics:
        body = resolved.metric.expression.expr
        deps = frozenset(
            normalize_identifier(c.name)
            for c in body.find_all(exp.Column)
            if normalize_identifier(c.name) in state_columns
        )
        definitions.append(
            Column(
                name=resolved.metric.name,
                expression=FrozenSQL.of(body.copy()),
                dependencies=deps,
                kind=ColumnKind.DIMENSION,
            )
        )
    return builder.add(
        PlanOperation.ADD_COLUMNS,
        inputs=(current.step_id,),
        state=add_columns(current.state, tuple(definitions)),
        payload=AddColumnsPayload(definitions=tuple(definitions)),
    )


def _resolve_fields(
    refs: Sequence,
    context: PlannerContext,
) -> tuple[ResolvedDimension | ResolvedFact | ResolvedMetric, ...]:
    from osi.planning.windows import is_windowed_expression

    out = []
    for ref in refs:
        resolved = resolve_reference(ref, context.namespace)
        if isinstance(resolved, ResolvedMetric):
            # S-22 (D-028 / D-030): windowed metrics produce one value
            # per row, not one value per partition; they are scalar by
            # definition and belong in the fields slot. They are
            # synthesised as derived ``ADD_COLUMNS`` after the source
            # / enrichment chain.
            if is_windowed_expression(resolved.metric.expression.expr):
                out.append(resolved)
                continue
            raise OSIPlanningError(
                ErrorCode.E_AGGREGATE_IN_SCALAR_QUERY,
                (
                    f"scalar query field {ref!s} resolves to metric "
                    f"{resolved.metric.name!r}; metrics aggregate over "
                    "rows and cannot appear in a Fields list. Move "
                    "the metric to Measures and switch to an "
                    "aggregation query. See Proposed_OSI_Semantics.md "
                    "D-011."
                ),
                context={
                    "field": str(ref),
                    "metric": resolved.metric.name,
                },
            )
        _reject_unaggregated_finer_grain_reference(resolved, context)
        out.append(resolved)
    return tuple(out)


def _reject_unaggregated_finer_grain_reference(
    resolved: ResolvedDimension | ResolvedFact,
    context: PlannerContext,
) -> None:
    """Reject a field body that reads a foreign dataset without aggregating.

    Foundation v0.1 D-024: a field body containing a column from a
    different dataset (e.g. ``customers.first_order_amount:
    orders.amount``) is only valid when wrapped in an aggregate; the
    bare row-level reference would imply fan-out without any rule
    for collapsing it. Reject with
    ``E_UNAGGREGATED_FINER_GRAIN_REFERENCE``.
    """
    home = resolved.dataset
    body = resolved.field.expression.expr
    if any(isinstance(n, exp.AggFunc) for n in (body, *body.find_all(exp.Expression))):
        # The field aggregates internally; implicit home-grain
        # aggregation (S-4) takes care of it. No D-024 violation.
        return
    for col in body.find_all(exp.Column):
        if not col.table:
            continue
        try:
            ref_dataset = normalize_identifier(col.table)
        except OSIParseError:
            continue
        if ref_dataset == home:
            continue
        raise OSIPlanningError(
            ErrorCode.E_UNAGGREGATED_FINER_GRAIN_REFERENCE,
            (
                f"field {home}.{resolved.field.name!r} references column "
                f"{ref_dataset}.{normalize_identifier(col.name)!r} from a "
                "different dataset without aggregating it; wrap the "
                "reference in an aggregate (SUM, COUNT, …) so the "
                "implicit home-grain aggregation can resolve. See "
                "Proposed_OSI_Semantics.md D-024."
            ),
            context={
                "home": home,
                "field": resolved.field.name,
                "referenced_dataset": ref_dataset,
                "referenced_column": normalize_identifier(col.name),
            },
        )


def _resolve_order_by(
    entries: Sequence[OrderBy], output: tuple[Identifier, ...]
) -> tuple[OrderByEntry, ...]:
    out: list[OrderByEntry] = []
    allowed = set(output)
    for entry in entries:
        col = entry.target.name
        if col not in allowed:
            raise OSIPlanningError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"order_by column {col!r} is not in the scalar query output",
                context={"column": col, "output": sorted(str(o) for o in output)},
            )
        out.append(
            OrderByEntry(column=col, descending=entry.direction is SortDirection.DESC)
        )
    return tuple(out)


__all__ = ["plan_scalar"]
