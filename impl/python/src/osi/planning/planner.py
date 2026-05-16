"""The single Foundation query planner.

Takes a validated :class:`SemanticModel` (via :class:`PlannerContext`) and a
user-supplied :class:`SemanticQuery`, and returns a frozen
:class:`QueryPlan` — a deterministic DAG of :class:`PlanStep` whose states
step through the closed algebra defined in
:mod:`osi.planning.algebra.operations`.

Pipeline (``Proposed_OSI_Semantics.md §5``):

1. Resolve dimensions and measures against the :class:`Namespace`.
2. Classify ``where`` into row-level and semi-join conjuncts and
   ``having`` into post-aggregate conjuncts.
3. Group measures by *fact dataset* — each group becomes a
   `measure-group state` that:
   a. ``SOURCE`` s the fact dataset
   b. applies row-level ``WHERE`` restricted to that dataset, then
      enriches dimension datasets via N:1 ``ENRICH`` (raising
      ``E3011`` for any N:N edge),
   c. applies any ``WHERE`` that references joined-in dimensions,
   d. applies any ``EXISTS_IN`` semi-joins via ``FILTERING_JOIN``,
   e. ``AGGREGATE`` s to the query's dimension grain.
4. ``MERGE`` the measure-group states on the shared dimension grain
   (chasm-trap safe — §4.11).
5. Apply ``HAVING`` as post-aggregate ``FILTER`` steps on the merged
   state.
6. ``PROJECT`` to the final output column list.
7. Wrap everything in a :class:`QueryPlan` with ``order_by`` and
   ``limit`` carried alongside (outside the algebra).

Every intermediate state is built *through* the algebra operators so all
the ``E3xxx`` / ``E4xxx`` safety checks fire exactly once at plan time.
The planner never inspects SQL text — all introspection happens on
SQLGlot ASTs.

Out-of-scope (raises ``E1105`` up in parsing / here in the planner):
fixed-grain overrides, per-metric filter context, ad-hoc aggregate
expressions in the ``measures`` slot, window functions, grouping sets,
pivot, metric reset.
"""

from __future__ import annotations

from typing import Sequence

from osi.common.identifiers import Identifier
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning.algebra.composition import add_columns
from osi.planning.algebra.operations import aggregate, filter_, project
from osi.planning.algebra.state import CalculationState
from osi.planning.classify import (
    RowLevelPredicate,
    SemiJoinPredicate,
    classify_having,
    classify_where,
)
from osi.planning.columns import (
    composite_leaf_dependencies,
    composite_to_derived_column,
    metric_to_aggregate_column_from_metric,
)
from osi.planning.joins import JoinStep, find_enrichment_path
from osi.planning.metric_dispatch import metric_fact_dataset as _metric_fact_dataset
from osi.planning.plan import (
    AddColumnsPayload,
    AggregatePayload,
    FilterPayload,
    OrderByEntry,
    PlanOperation,
    PlanStep,
    ProjectPayload,
    QueryPlan,
)
from osi.planning.planner_bridge import (
    build_bridge_plan,
    build_nested_bridge_plan,
    can_apply_bridge_resolution,
    find_bridge_resolutions,
)
from osi.planning.planner_composites import (
    measure_plan_for_group,
    replace_metric_expression,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.planner_mn import MeasureGroup as _MeasureGroup
from osi.planning.planner_mn import build_dimension_only_group as _dimension_only_group
from osi.planning.planner_nested import (
    infer_intermediate_grain,
    insert_nested_aggregate,
    is_nested_aggregate,
)
from osi.planning.planner_mn import (
    group_allowed_relationships as _group_allowed_relationships,
)
from osi.planning.planner_mn import (
    validate_multi_fact_stitch as _validate_multi_fact_stitch,
)
from osi.planning.planner_scalar import plan_scalar
from osi.planning.preprocess import inline_named_filters, substitute_parameters
from osi.planning.resolve import (
    ResolvedDimension,
    ResolvedMetric,
    resolve_dimension,
    resolve_measure,
)
from osi.planning.semantic_query import OrderBy, SemanticQuery, SortDirection
from osi.planning.steps import (
    PlanBuilder,
    enrich_step,
    fact_dataset,
    filter_step,
    merge_groups,
    semi_join_step,
    source_step,
)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def plan(query: SemanticQuery, context: PlannerContext) -> QueryPlan:
    """Plan ``query`` against ``context``.

    Pure; determinism is guaranteed by the planner's topological
    traversal and by :mod:`osi.planning.prefixes` controlling every
    synthetic name.

    Foundation v0.1 (D-010 / D-011) routes by query shape:
    aggregation queries flow through this function; scalar queries
    delegate to :func:`osi.planning.planner_scalar.plan_scalar`.
    """
    if query.is_scalar:
        return plan_scalar(query, context)

    dims = tuple(resolve_dimension(d, context.namespace) for d in query.dimensions)
    measures = tuple(resolve_measure(m, context.namespace) for m in query.measures)

    if not measures and not dims:  # SemanticQuery checks this too
        raise OSIPlanningError(
            ErrorCode.E1002_MISSING_REQUIRED_FIELD,
            "query has no dimensions and no measures",
        )

    # Pre-classification AST rewrites: parameter substitution and
    # named-filter inlining (``Proposed_OSI_Semantics.md §4.6 / §5.1``).
    # Running these up front keeps the classifier focused on
    # row-level / semi-join / post-aggregate splitting.
    all_field_names = _all_field_names(context)
    where = substitute_parameters(
        query.where, provided=query.parameters, declared=context.model.parameters
    )
    where = inline_named_filters(
        where, filters=context.model.filters, field_names=all_field_names
    )
    having = substitute_parameters(
        query.having, provided=query.parameters, declared=context.model.parameters
    )

    classified = classify_where(where, context.namespace)
    post_agg_preds = classify_having(having, tuple(m.metric.name for m in measures))

    builder = PlanBuilder()

    groups = _group_measures(measures, context)
    if not groups:
        groups = _dimension_only_group(dims, context)
    _validate_multi_fact_stitch(groups, dims, context)

    group_roots: list[PlanStep] = []
    for group in groups:
        try:
            root = _build_measure_group(
                group=group,
                dimensions=dims,
                where=classified.row_level,
                semi_joins=classified.semi_joins,
                builder=builder,
                context=context,
            )
        except OSIPlanningError as exc:
            # S-9 / D-022: an enrichment-time fan-out rejection inside
            # an aggregation query is the "non-distributive over M:N
            # is unsafe" case from the spec. Translate the algebra
            # safety code to the named user-facing code so the
            # diagnostic matches Appendix C.
            if exc.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED:
                raise OSIPlanningError(
                    ErrorCode.E_UNSAFE_REAGGREGATION,
                    str(exc),
                    context=dict(exc.context),
                ) from exc
            raise
        group_roots.append(root)

    final = merge_groups(group_roots, dims, builder)

    for pred in post_agg_preds:
        final = builder.add(
            PlanOperation.FILTER,
            inputs=(final.step_id,),
            state=filter_(
                final.state,
                pred.expression,
                dependencies=pred.measures,
            ),
            payload=FilterPayload(
                predicate=pred.expression,
                dependencies=pred.measures,
                is_post_aggregate=True,
            ),
        )

    output_columns = _output_column_names(dims, measures)
    projected = builder.add(
        PlanOperation.PROJECT,
        inputs=(final.step_id,),
        state=project(final.state, output_columns),
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


# ---------------------------------------------------------------------------
# Measure grouping
# ---------------------------------------------------------------------------


def _group_measures(
    measures: Sequence[ResolvedMetric], context: PlannerContext
) -> tuple[_MeasureGroup, ...]:
    by_fact: dict[Identifier, list[ResolvedMetric]] = {}
    for m in measures:
        fact = _metric_fact_dataset(m, context)
        by_fact.setdefault(fact, []).append(m)
    return tuple(
        _MeasureGroup(fact_dataset=ds, measures=tuple(ms))
        for ds, ms in sorted(by_fact.items(), key=lambda kv: str(kv[0]))
    )


# ---------------------------------------------------------------------------
# Measure-group state construction
# ---------------------------------------------------------------------------


def _build_measure_group(
    *,
    group: _MeasureGroup,
    dimensions: Sequence[ResolvedDimension],
    where: Sequence[RowLevelPredicate],
    semi_joins: Sequence[SemiJoinPredicate],
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    # Compute partition of WHERE / dim datasets up-front so we can
    # speculatively decide between the standard plan and bridge
    # resolution before mutating ``builder``.
    fact_local = [p for p in where if p.datasets <= {group.fact_dataset}]
    foreign = [p for p in where if not p.datasets <= {group.fact_dataset}]
    dim_datasets = frozenset(d.dataset for d in dimensions) - {group.fact_dataset}
    filter_datasets = frozenset().union(*(p.datasets for p in foreign)) - {
        group.fact_dataset
    }
    needed_datasets = dim_datasets | filter_datasets

    enrichment_steps: tuple[JoinStep, ...] = ()
    if needed_datasets:
        try:
            enrichment_steps = find_enrichment_path(
                root=group.fact_dataset,
                targets=needed_datasets,
                graph=context.graph,
                allowed_relationships=_group_allowed_relationships(group),
            )
        except OSIPlanningError as exc:
            bridge_plan = _maybe_build_via_bridge(
                exc=exc,
                group=group,
                dimensions=dimensions,
                fact_local=fact_local,
                foreign=foreign,
                semi_joins=semi_joins,
                needed_datasets=needed_datasets,
                dim_datasets=dim_datasets,
                builder=builder,
                context=context,
            )
            if bridge_plan is not None:
                return bridge_plan
            raise

    fact_ds = fact_dataset(group.fact_dataset, context)
    current = source_step(fact_ds, builder, context)
    for pred in fact_local:
        current = filter_step(current, pred, builder)
    for join in enrichment_steps:
        current = enrich_step(current, join, builder, context)

    for pred in foreign:
        current = filter_step(current, pred, builder)

    for sj in semi_joins:
        current = semi_join_step(current, sj, builder, context)

    # Nested-aggregate metrics (D-020 + D-024 / `I-S5-impl`): route a
    # single-measure group containing a nested aggregate (e.g.
    # ``AVG(AVG(orders.amount))``) through the dedicated two-step
    # aggregate planner. Standard composites and bare aggregates fall
    # through to the existing path.
    nested_plan = _maybe_build_nested_aggregate(
        group=group,
        dimensions=dimensions,
        current=current,
        builder=builder,
        context=context,
    )
    if nested_plan is not None:
        return nested_plan

    # Split measures into base aggregates vs. composites. Composites
    # need their referenced base aggregates materialised in the same
    # AGGREGATE step, then a following ADD_COLUMNS to compute the
    # derived expression (``Proposed_OSI_Semantics.md §5.4``).
    measure_plan = measure_plan_for_group(
        measures=group.measures, fact_ds=group.fact_dataset, context=context
    )

    group_grain = _query_grain(dimensions, current.state)
    if not measure_plan.base_aggregates and not group_grain:
        return current

    agg_columns = tuple(
        metric_to_aggregate_column_from_metric(
            metric=base.metric, dataset=base.dataset, state=current.state
        )
        for base in measure_plan.base_aggregates
    )

    aggregated = builder.add(
        PlanOperation.AGGREGATE,
        inputs=(current.step_id,),
        state=aggregate(
            current.state,
            group_grain,
            agg_columns,
        ),
        payload=AggregatePayload(
            new_grain=group_grain,
            aggregations=agg_columns,
        ),
    )

    if not measure_plan.composite_definitions:
        return aggregated

    derived = tuple(
        composite_to_derived_column(
            name=comp.name,
            metric=replace_metric_expression(
                metric=comp.metric, new_expr=comp.expression
            ),
            dependency_names=composite_leaf_dependencies(
                replace_metric_expression(metric=comp.metric, new_expr=comp.expression)
            ),
        )
        for comp in measure_plan.composite_definitions
    )
    return builder.add(
        PlanOperation.ADD_COLUMNS,
        inputs=(aggregated.step_id,),
        state=add_columns(aggregated.state, derived),
        payload=AddColumnsPayload(definitions=derived),
    )


# ---------------------------------------------------------------------------
# Nested aggregate routing
# ---------------------------------------------------------------------------


def _maybe_build_nested_aggregate(
    *,
    group: _MeasureGroup,
    dimensions: Sequence[ResolvedDimension],
    current: PlanStep,
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep | None:
    """Return a two-step aggregate plan if the group is nested-only.

    Foundation v0.1 supports the simplest nested shape: exactly one
    measure whose body is ``f(g(<inner>))`` with both ``f`` and
    ``g`` Foundation aggregates. Mixed groups (one nested + one
    plain) fall through to the standard planner, which will surface
    a descriptive error if the shape is not supported.
    """
    if len(group.measures) != 1:
        return None
    only = group.measures[0]
    if not is_nested_aggregate(only.metric):
        return None
    intermediate_grain = infer_intermediate_grain(
        fact_dataset=group.fact_dataset,
        dimensions=dimensions,
        state_columns=current.state.column_names,
        context=context,
    )
    if not intermediate_grain:
        # No usable intermediate dim — the nested rewrite cannot
        # honour the per-row-first contract. Surface the standard
        # error from the existing path so users see the unsupported
        # shape, not a silently wrong result.
        return None
    return insert_nested_aggregate(
        parent=current,
        measure=only,
        dimensions=dimensions,
        intermediate_grain=intermediate_grain,
        builder=builder,
        context=context,
    )


# ---------------------------------------------------------------------------
# Output layout helpers
# ---------------------------------------------------------------------------


def _output_column_names(
    dims: Sequence[ResolvedDimension], measures: Sequence[ResolvedMetric]
) -> tuple[Identifier, ...]:
    return tuple(d.field.name for d in dims) + tuple(m.metric.name for m in measures)


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
                f"order_by column {col!r} is not in the output",
                context={"column": col, "output": sorted(str(o) for o in output)},
            )
        out.append(
            OrderByEntry(column=col, descending=entry.direction is SortDirection.DESC)
        )
    return tuple(out)


def _all_field_names(context: PlannerContext) -> frozenset[Identifier]:
    """Every field name addressable anywhere in the model.

    Consulted by :func:`~osi.planning.preprocess.inline_named_filters`
    to protect against silent rewrites: a bare reference that collides
    with a declared field name is left alone (field wins).
    """
    names: set[Identifier] = set()
    for ds in context.model.datasets:
        for f in ds.fields:
            names.add(f.name)
        for m in ds.metrics:
            names.add(m.name)
    for m in context.model.metrics:
        names.add(m.name)
    return frozenset(names)


def _query_grain(
    dims: Sequence[ResolvedDimension], state: CalculationState
) -> DimensionSet:
    grain = {d.field.name for d in dims}
    missing = grain - state.column_names
    if missing:
        raise OSIPlanningError(
            ErrorCode.E3002_UNSATISFIABLE_GRAIN,
            f"dimensions {sorted(str(m) for m in missing)} are not reachable "
            "from the current measure-group state",
            context={"missing": sorted(str(m) for m in missing)},
        )
    return frozenset(grain)


# ---------------------------------------------------------------------------
# Bridge-resolution dispatch (Proposed_OSI_Semantics.md §6.5.1, mid-pipeline)
# ---------------------------------------------------------------------------


def _maybe_build_via_bridge(
    *,
    exc: OSIPlanningError,
    group: _MeasureGroup,
    dimensions: Sequence[ResolvedDimension],
    fact_local: Sequence[RowLevelPredicate],
    foreign: Sequence[RowLevelPredicate],
    semi_joins: Sequence[SemiJoinPredicate],
    needed_datasets: frozenset[Identifier],
    dim_datasets: frozenset[Identifier],
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep | None:
    """Try to build the bridge-resolution plan. Return ``None`` on no-fit.

    Falls back to the original planner error (caller re-raises) when:

    * the planner error isn't an M:N rejection (``E3011`` / ``E3012``);
    * the model exposes no bridge that can resolve the unsafe edge;
    * any restriction in :mod:`osi.planning.planner_bridge` blocks the
      shape (non-distributive metrics, foreign filters, semi-joins,
      fact-side dimensions, …).
    """
    if exc.code not in (
        ErrorCode.E3011_MN_AGGREGATION_REJECTED,
        ErrorCode.E3012_MN_NO_STITCH_PATH,
    ):
        return None
    if fact_local or foreign or semi_joins:
        # Filters on the fact or foreign datasets, and semi-joins, are
        # not yet supported by the bridge plan shape (they require
        # extra grain bookkeeping). Fall back to the original error.
        return None

    nested_only = (
        len(group.measures) == 1
        and is_nested_aggregate(group.measures[0].metric)
    )

    applicable, _reason = can_apply_bridge_resolution(group)
    if not applicable and not nested_only:
        return None

    resolutions = find_bridge_resolutions(
        fact=group.fact_dataset,
        needed=needed_datasets,
        graph=context.graph,
    )
    if not resolutions:
        return None

    # All v1 query-grain dims must live on the bridge's right side.
    # Allow the multi-target case where every outstanding dim is reached
    # via the same bridge dataset.
    distinct_bridges = {r.bridge for r in resolutions}
    if len(distinct_bridges) > 1:
        raise OSIPlanningError(
            ErrorCode.E3001_AMBIGUOUS_JOIN_PATH,
            (
                "multiple bridge datasets resolve the M:N traversal: "
                f"{sorted(str(b) for b in distinct_bridges)}. "
                "Disambiguate with joins.using_relationships on the metric."
            ),
            context={"bridges": sorted(str(b) for b in distinct_bridges)},
        )
    bridge = sorted(resolutions, key=lambda r: str(r.right_target))[0]

    # All query dimensions must be reachable from the bridge side.
    # Anything still on the fact side blocks v1 bridge resolution.
    fact_side_dims = dim_datasets - {bridge.bridge, bridge.right_target}
    fact_side_dims_unreached = frozenset(
        d
        for d in fact_side_dims
        if not context.graph.find_paths(bridge.bridge, d, max_depth=4)
    )
    if fact_side_dims_unreached:
        return None

    post_bridge_targets = frozenset(d for d in dim_datasets if d != bridge.bridge)

    if nested_only:
        return build_nested_bridge_plan(
            group=group,
            bridge=bridge,
            dimensions=dimensions,
            builder=builder,
            context=context,
            intermediate_keys_dataset=bridge.right_target,
        )

    return build_bridge_plan(
        group=group,
        bridge=bridge,
        dimensions=dimensions,
        builder=builder,
        context=context,
        pre_agg_dim_targets=frozenset(),
        post_bridge_dim_targets=post_bridge_targets,
        query_grain=frozenset(d.field.name for d in dimensions),
    )


__all__ = ["plan"]
