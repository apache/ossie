"""Mid-pipeline bridge resolution for the planner.

The Foundation spec
(``proposals/foundation-v0.1/Proposed_OSI_Semantics.md §6.8.1``, D-026
/ D-027) describes the bridge route for M:N references: a metric over a
fact reachable only through an ``N : N`` edge is well-defined when an
*intermediate bridge dataset* makes the (fact-row, group-key) pairing
explicit. The single-pass spec form is:

  *Materialise the distinct ``(fact-primary-key, group-key)`` row set
  by joining the fact, the bridge, and the right-hand dimension once;
  then aggregate once at the query's dimension grain.*

Because that materialisation is just a join with ``DISTINCT``, every
aggregate category (distributive, algebraic, holistic) is well-defined
over it — there is no two-stage decomposition. D-027 therefore accepts
bare ``SUM`` / ``AVG`` / ``MEDIAN`` / ``COUNT(DISTINCT)`` over an N:N
bridge.

The current reference implementation realises the spec by emitting a
plan that is equivalent for the four distributive operators (``SUM``,
``COUNT``, ``MIN``, ``MAX``) plus ``COUNT_DISTINCT`` (whose D-027
treatment matches because the dedup IS the distinct count). Non-
distributive operators (``AVG``, ``MEDIAN``, ``PERCENTILE_CONT``)
require the single-pass dedup form rather than the two-stage shape and
are still pending — they currently surface ``E_UNSAFE_REAGGREGATION``
from the standard planner. Tracked by
``compliance/foundation-v0.1/tests/bridge/hard/t-016`` and ``t-051`` in
the conformance suite.

Plan shape (distributive case):

1. ``source(fact)`` + safe enrichments to the bridge's left link.
2. ``aggregate`` at the bridge's left join-key grain (one row per fact
   row contributing to a group).
3. ``source(bridge)``, ``enrich`` the right-side target, then
   ``enrich`` the pre-aggregated state via
   :class:`EnrichDerivedPayload`.
4. ``aggregate`` at the query's dimension grain, re-aggregating each
   metric with its same operator (``SUM``-of-``SUM``,
   ``MAX``-of-``MAX``, …).

Limitations in this revision (re-examine when real models need more):

* **Non-distributive aggregates** (``AVG``, ``MEDIAN``,
  ``PERCENTILE_CONT``) over a bridge are not yet routed — see D-027
  and the ``t-016`` / ``t-051`` conformance cases.
* **Composite metrics** (``§5.4``) must use the standard plan shape.
* **Query dimensions must live on the bridge's right-hand side** —
  fact-side dimensions force a wider pre-aggregation grain than this
  revision supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.graph import Cardinality, RelationshipEdge, RelationshipGraph
from osi.planning.algebra.operations import JoinType, aggregate, enrich
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
)
from osi.planning.columns import (
    metric_to_aggregate_column_from_metric,
    parse_metric_aggregate,
)
from osi.planning.joins import find_enrichment_path, reachable_via_n1
from osi.planning.plan import (
    AggregatePayload,
    EnrichDerivedPayload,
    PlanOperation,
    PlanStep,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.planner_mn import MeasureGroup
from osi.planning.resolve import ResolvedDimension
from osi.planning.steps import PlanBuilder, enrich_step, fact_dataset, source_step

# ---------------------------------------------------------------------------
# Bridge discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BridgeResolution:
    """A bridge dataset that can resolve an unsafe enrichment step.

    ``bridge`` has safe `N : 1` edges to both ``left_link`` (which
    sits in the fact's safe-reachable closure) and ``right_target``
    (one of the originally-outstanding datasets). The planner uses
    this to route around the unsafe edge by sourcing the bridge as a
    fresh root.
    """

    bridge: Identifier
    left_link: Identifier
    left_edge: RelationshipEdge
    right_target: Identifier
    right_edge: RelationshipEdge


def _is_safe(edge: RelationshipEdge, *, parent: Identifier) -> bool:
    """Mirror of joins._is_safe_direction (kept private to avoid circular)."""
    if edge.cardinality is Cardinality.N_TO_N:
        return False
    if edge.cardinality is Cardinality.ONE_TO_ONE:
        return edge.from_dataset == parent or edge.to_dataset == parent
    return edge.from_dataset == parent  # N:1, N-side -> 1-side


def _safe_edges_from(
    bridge: Identifier, graph: RelationshipGraph
) -> tuple[tuple[RelationshipEdge, Identifier], ...]:
    """Edges where ``bridge`` can safely enrich into the other endpoint."""
    out: list[tuple[RelationshipEdge, Identifier]] = []
    for edge in graph.neighbors(bridge):
        if not _is_safe(edge, parent=bridge):
            continue
        other = edge.to_dataset if edge.from_dataset == bridge else edge.from_dataset
        out.append((edge, other))
    return tuple(out)


def find_bridge_resolutions(
    *,
    fact: Identifier,
    needed: frozenset[Identifier],
    graph: RelationshipGraph,
) -> tuple[BridgeResolution, ...]:
    """Bridges that connect ``fact``'s safe-reachable set to outstanding targets.

    The discovery is purely cardinality-driven (``§6.5.1``). A bridge
    candidate is any dataset with at least one safe edge to a
    fact-reachable dataset and one safe edge to an outstanding target.
    Returns every distinct ``(bridge, right_target)`` pair so the
    caller can decide between unique-resolution and ``E3001``-ambiguity.
    """
    safe = reachable_via_n1(fact, graph)
    outstanding = needed - safe
    if not outstanding:
        return ()
    candidates: list[BridgeResolution] = []
    for ds in sorted(
        {e.from_dataset for e in graph.edges} | {e.to_dataset for e in graph.edges},
        key=str,
    ):
        if ds == fact or ds in safe or ds in outstanding:
            continue
        bridge_edges = _safe_edges_from(ds, graph)
        # The bridge must reach at least one outstanding target *and*
        # at least one already-reachable dataset; otherwise it is not
        # actually a bridge between the two sides.
        targets = {other for _, other in bridge_edges if other in outstanding}
        links = {other for _, other in bridge_edges if other in safe}
        if not targets or not links:
            continue
        # Build one BridgeResolution per (right_target) and pick a
        # canonical left_link deterministically.
        for tgt in sorted(targets, key=str):
            right_edge = next(e for e, o in bridge_edges if o == tgt)
            left_link = sorted(links, key=str)[0]
            left_edge = next(e for e, o in bridge_edges if o == left_link)
            candidates.append(
                BridgeResolution(
                    bridge=ds,
                    left_link=left_link,
                    left_edge=left_edge,
                    right_target=tgt,
                    right_edge=right_edge,
                )
            )
    return tuple(candidates)


# ---------------------------------------------------------------------------
# Plan-shape construction
# ---------------------------------------------------------------------------


# Metrics that the bridge plan can resolve. Distributive aggregates
# (SUM/COUNT/MIN/MAX) re-aggregate trivially. ``COUNT_DISTINCT`` is
# also accepted per D-022 / §6.11.3 — the bridge's distinct
# (fact, group-key) materialisation IS the de-duplication
# ``COUNT(DISTINCT)`` needs. After dedup, each fact contributes once
# per dim group, so ``SUM`` of the per-fact COUNT_DISTINCT (which is
# 1 if the value is present) produces the correct result whenever the
# COUNT_DISTINCT argument is functionally determined by the fact PK
# (i.e. lives on the fact dataset). This is the spec's contract.
_BRIDGE_RESOLVABLE = (
    AggregateFunction.SUM,
    AggregateFunction.COUNT,
    AggregateFunction.MIN,
    AggregateFunction.MAX,
    AggregateFunction.COUNT_DISTINCT,
)


def _resolved_bridge_unique(
    candidates: tuple[BridgeResolution, ...],
) -> BridgeResolution:
    """Pick the single bridge candidate or raise ``E3001`` for ambiguity."""
    distinct_bridges = {c.bridge for c in candidates}
    if len(distinct_bridges) == 1:
        # Single bridge dataset; if multiple right_targets it covers
        # them all and the caller picks one per outstanding target.
        return sorted(candidates, key=lambda c: str(c.right_target))[0]
    raise OSIPlanningError(
        ErrorCode.E3001_AMBIGUOUS_JOIN_PATH,
        (
            "multiple bridge datasets can resolve the M:N traversal: "
            f"{sorted(str(b) for b in distinct_bridges)}. Disambiguate "
            "with joins.using_relationships on the metric, or rename one "
            "of the bridge relationships."
        ),
        context={"bridges": sorted(str(b) for b in distinct_bridges)},
    )


def can_apply_bridge_resolution(group: MeasureGroup) -> tuple[bool, str | None]:
    """Cheap precheck. Returns ``(applicable, reason_if_not)``."""
    if not group.measures:
        return False, "bridge resolution requires at least one measure"
    for resolved in group.measures:
        try:
            fn, _ = parse_metric_aggregate(resolved.metric)
        except OSIPlanningError as exc:
            return False, f"metric {resolved.metric.name!r}: {exc}"
        if fn not in _BRIDGE_RESOLVABLE:
            return False, (
                f"metric {resolved.metric.name!r} uses aggregate "
                f"{fn.name!r}; bridge resolution requires SUM / "
                "COUNT / MIN / MAX / COUNT_DISTINCT"
            )
    return True, None


def _materialised_metric_column(
    *,
    metric_name: Identifier,
    function: AggregateFunction,
    state: CalculationState,
) -> Column:
    """Re-aggregation column at the final grain reading the pre-agg output.

    For COUNT we must SUM the counts (re-aggregating COUNT-of-COUNT
    would double-count). For SUM/MIN/MAX the operator is its own
    re-aggregator (``§5.1``). ``state`` is the post-bridge-join state
    where the materialised column lives.
    """
    if function in (AggregateFunction.COUNT, AggregateFunction.COUNT_DISTINCT):
        re_fn = AggregateFunction.SUM
    else:
        re_fn = function
    arg_sql = FrozenSQL.of(exp.column(str(metric_name)))
    return Column(
        name=metric_name,
        expression=arg_sql,
        dependencies=frozenset({metric_name}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=re_fn, argument=arg_sql),
    )


def _dedup_metric_column(
    *,
    metric_name: Identifier,
) -> Column:
    """D-026 dedup column.

    The materialised column is constant per (link_key, dim_key) tuple
    by construction (it was pre-aggregated at the link grain), so any
    "pick one row" aggregate gives the same answer. We use ``MIN``
    because:

    * ``MIN`` is distributive, so the algebra's grain-coarsening
      preconditions are trivially satisfied;
    * ``MIN`` is well-defined on every numeric / temporal type the
      Foundation supports (no NaN-vs-NULL surprises);
    * ``MIN`` does not change the result vs ``MAX`` because the
      column is single-valued on the dedup grain.

    The output column reuses the materialised column's name so the
    final re-aggregation step (which reads ``metric_name``) sees no
    structural change.
    """
    arg_sql = FrozenSQL.of(exp.column(str(metric_name)))
    return Column(
        name=metric_name,
        expression=arg_sql,
        dependencies=frozenset({metric_name}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=AggregateFunction.MIN, argument=arg_sql),
    )


def build_bridge_plan(
    *,
    group: MeasureGroup,
    bridge: BridgeResolution,
    dimensions: Sequence[ResolvedDimension],
    builder: PlanBuilder,
    context: PlannerContext,
    pre_agg_dim_targets: frozenset[Identifier],
    post_bridge_dim_targets: frozenset[Identifier],
    query_grain: frozenset[Identifier],
) -> PlanStep:
    """Build the §6.5.1 bridge plan from ``group`` to ``query_grain``.

    Parameters
    ----------
    pre_agg_dim_targets:
        Datasets that must be enriched onto the fact side *before* the
        pre-aggregation step (because the query references their dims
        and they're reachable from the fact via safe N:1 only). For
        the simple Foundation case this is empty.
    post_bridge_dim_targets:
        Datasets that must be enriched onto the bridge state *after*
        the bridge is sourced. Always includes ``bridge.right_target``.
    """
    # 1. Source fact + any pre-agg-side enrichments. We currently
    #    require pre_agg_dim_targets to be empty (caller enforces);
    #    the parameter is kept for clarity and future extension.
    if pre_agg_dim_targets:
        raise OSIPlanningError(
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            (
                "bridge resolution v1 does not yet support fact-side "
                "dimensions; reference dimensions only on the bridge "
                f"side or rewrite the query. Got: "
                f"{sorted(str(d) for d in pre_agg_dim_targets)}"
            ),
            context={"fact_side_dims": sorted(str(d) for d in pre_agg_dim_targets)},
        )
    fact_ds = fact_dataset(group.fact_dataset, context)
    fact_state = source_step(fact_ds, builder, context)

    # 2. Walk the safe N:1 path from the fact to the bridge's left link
    #    so the link's join keys are addressable on the fact state. For
    #    the simple case where ``left_link == fact``, this is a no-op.
    if bridge.left_link != group.fact_dataset:
        link_path = find_enrichment_path(
            root=group.fact_dataset,
            targets=frozenset({bridge.left_link}),
            graph=context.graph,
        )
        for join in link_path:
            fact_state = enrich_step(fact_state, join, builder, context)

    # 3. Pre-aggregate the (possibly enriched) fact state to the
    #    bridge's link-side join key grain.
    left_keys = _join_keys_on_side(bridge.left_edge, side=bridge.left_link)
    if left_keys is None:
        raise OSIPlanningError(  # pragma: no cover
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            (
                f"bridge {bridge.bridge!r} edge {bridge.left_edge.name!r} "
                f"has no join keys on its link side {bridge.left_link!r}"
            ),
            context={"bridge": str(bridge.bridge)},
        )
    missing = frozenset(left_keys) - fact_state.state.column_names
    if missing:
        raise OSIPlanningError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            (
                f"bridge {bridge.bridge!r} requires keys "
                f"{sorted(str(k) for k in missing)} to be addressable on the "
                "fact state, but they are not. The fact's enrichment path to "
                "the bridge's left link did not surface the required columns."
            ),
            context={
                "bridge": str(bridge.bridge),
                "missing": sorted(str(k) for k in missing),
            },
        )
    pre_agg_grain = frozenset(left_keys)
    pre_agg_columns = tuple(
        metric_to_aggregate_column_from_metric(
            metric=resolved.metric,
            dataset=group.fact_dataset,
            state=fact_state.state,
        )
        for resolved in group.measures
    )
    pre_agg = builder.add(
        PlanOperation.AGGREGATE,
        inputs=(fact_state.step_id,),
        state=aggregate(fact_state.state, pre_agg_grain, pre_agg_columns),
        payload=AggregatePayload(new_grain=pre_agg_grain, aggregations=pre_agg_columns),
    )

    # 3. Source the bridge.
    bridge_ds = fact_dataset(bridge.bridge, context)
    bridge_state = source_step(bridge_ds, builder, context)

    # 4. Enrich the right-side target and any other post-bridge dim
    #    targets onto the bridge state via the standard path-finder
    #    (the bridge is the new root; everything past here is safe).
    if post_bridge_dim_targets:
        path = find_enrichment_path(
            root=bridge.bridge,
            targets=post_bridge_dim_targets,
            graph=context.graph,
        )
        for join in path:
            bridge_state = enrich_step(bridge_state, join, builder, context)

    # 5. Enrich the pre-aggregated fact onto the bridge state.
    bridge_keys_for_left = _join_keys_on_side(bridge.left_edge, side=bridge.bridge)
    if bridge_keys_for_left is None:
        raise OSIPlanningError(  # pragma: no cover — caught upstream
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"bridge {bridge.bridge!r} edge missing self-side join keys",
        )
    parent_column_names = bridge_state.state.column_names
    drops = frozenset(k for k in left_keys if k in parent_column_names)
    enriched = enrich(
        bridge_state.state,
        pre_agg.state,
        parent_keys=tuple(bridge_keys_for_left),
        child_keys=tuple(left_keys),
        join_type=JoinType.LEFT,
        drop_child_columns=drops,
    )
    # Children = the pre-agg's metric columns (PK of pre-agg is left_keys
    # which already align with the bridge's parent_keys, so they're
    # dropped automatically by the algebra below). We pass the metrics
    # explicitly into the payload so codegen knows which columns to surface.
    materialised_children = tuple(
        c for c in pre_agg.state.columns if c.name not in pre_agg.state.grain
    )
    bridge_extended = builder.add(
        PlanOperation.ENRICH,
        inputs=(bridge_state.step_id, pre_agg.step_id),
        state=enriched,
        payload=EnrichDerivedPayload(
            child_columns=materialised_children,
            keys=frozenset(bridge_keys_for_left),
            join_type=JoinType.LEFT,
            parent_keys=tuple(bridge_keys_for_left),
            child_keys=tuple(left_keys),
        ),
    )

    # 6.5 D-026 dedup. The materialised metric is single-valued on
    #    ``left_keys``, but the bridge fan-out has duplicated each
    #    (link_key, dim_key) tuple once per intermediate bridge row
    #    (e.g. once per actor sharing the same height watching the
    #    same movie). Without this step the final SUM would
    #    multi-count those duplicates. Aggregating at
    #    ``left_keys ∪ final_dim_keys`` with ``MIN`` collapses the
    #    duplicates to one row per (link_key, dim_key) tuple while
    #    preserving the materialised value.
    final_grain = _restricted_query_grain(dimensions, bridge_extended.state)
    dedup_grain = frozenset(left_keys) | final_grain
    dedup_columns = tuple(
        _dedup_metric_column(metric_name=resolved.metric.name)
        for resolved in group.measures
    )
    dedup = builder.add(
        PlanOperation.AGGREGATE,
        inputs=(bridge_extended.step_id,),
        state=aggregate(bridge_extended.state, dedup_grain, dedup_columns),
        payload=AggregatePayload(new_grain=dedup_grain, aggregations=dedup_columns),
    )

    # 7. Final aggregate at the query grain, re-aggregating each
    #    materialised metric with the appropriate distributive operator.
    re_agg_columns = tuple(
        _materialised_metric_column(
            metric_name=resolved.metric.name,
            function=parse_metric_aggregate(resolved.metric)[0],
            state=dedup.state,
        )
        for resolved in group.measures
    )
    return builder.add(
        PlanOperation.AGGREGATE,
        inputs=(dedup.step_id,),
        state=aggregate(dedup.state, final_grain, re_agg_columns),
        payload=AggregatePayload(new_grain=final_grain, aggregations=re_agg_columns),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join_keys_on_side(
    edge: RelationshipEdge, *, side: Identifier
) -> tuple[Identifier, ...] | None:
    """Return the join columns belonging to ``side`` on ``edge``."""
    if edge.from_dataset == side:
        return tuple(edge.from_columns)
    if edge.to_dataset == side:
        return tuple(edge.to_columns)
    return None


def _restricted_query_grain(
    dimensions: Sequence[ResolvedDimension], state: CalculationState
) -> frozenset[Identifier]:
    """Compute the final aggregation grain from query dimensions.

    Only the dimensions whose source dataset's columns are addressable
    on ``state`` count — every other dim is logically a fact-side
    dimension which the v1 bridge plan disallows (and the caller has
    already validated).
    """
    grain: set[Identifier] = set()
    for d in dimensions:
        col = d.field.name
        if col in state.column_names:
            grain.add(col)
    return frozenset(grain)


# ---------------------------------------------------------------------------
# Nested-aggregate-over-bridge composition (S-23, closes the I-S5 + I-S8
# composition). The shape is conceptually different from the dedup
# bridge: there is no fan-out worry because the inner aggregate sits
# next to the bridge join, so the per-row reading collapses naturally
# at the inner grain.
# ---------------------------------------------------------------------------


def build_nested_bridge_plan(
    *,
    group: MeasureGroup,
    bridge: BridgeResolution,
    dimensions: Sequence[ResolvedDimension],
    builder: PlanBuilder,
    context: PlannerContext,
    intermediate_keys_dataset: Identifier,
) -> PlanStep:
    """Plan ``f(g(<fact-col>))`` queried via a bridge dataset.

    Strategy (matches the spec's per-row-first reading of D-020 + D-024
    composed with D-022 / §6.5):

    1. ``source(bridge)`` then enrich the fact dataset and every
       post-bridge dim target via the standard safe-N:1 path-finder.
       This produces one row per bridge tuple with both the inner
       aggregate's argument and the dim columns addressable.
    2. Inner ``aggregate`` at ``(intermediate_keys_dataset.pk ∪
       query_dim_keys)`` with the *inner* aggregate function.
    3. Final ``aggregate`` at the query grain with the *outer*
       aggregate function applied to the inner column.

    Restrictions for v1:

    * Exactly one nested measure in the group (mixed groups still
      route through the standard planner; mixed nested-and-plain is
      rejected as ``E_UNSAFE_REAGGREGATION`` upstream).
    * Both inner and outer functions must be in
      :data:`_BRIDGE_RESOLVABLE` ∪ ``{AVG}``. ``AVG`` is allowed
      because the per-row reading converts ``AVG(AVG(…))`` into a
      sequence of two single-step aggregates, neither of which
      crosses fan-out.
    """
    from osi.planning.planner_nested import is_nested_aggregate, parse_nested

    if len(group.measures) != 1 or not is_nested_aggregate(group.measures[0].metric):
        raise OSIPlanningError(  # pragma: no cover — caller-contract
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            "build_nested_bridge_plan requires a single nested aggregate",
        )
    measure = group.measures[0]
    outer_fn, inner_fn, inner_arg_expr = parse_nested(measure.metric)

    # 1. Source bridge and enrich both the fact and the right-target +
    #    other post-bridge dim datasets.
    bridge_ds = fact_dataset(bridge.bridge, context)
    bridge_state = source_step(bridge_ds, builder, context)
    targets = frozenset({group.fact_dataset, bridge.right_target}) | frozenset(
        d.dataset for d in dimensions if d.dataset != bridge.bridge
    )
    if targets:
        for join in find_enrichment_path(
            root=bridge.bridge, targets=targets, graph=context.graph
        ):
            bridge_state = enrich_step(bridge_state, join, builder, context)

    # 2. Inner aggregate. The grain is the intermediate dataset's
    #    join key on the bridge (e.g. ``actor_id`` for the bridge
    #    ``appearances``) plus every query dim addressable on state.
    intermediate_pk = _intermediate_keys_for(
        intermediate_keys_dataset, bridge=bridge, graph=context.graph
    )
    dim_columns = frozenset(
        d.field.name
        for d in dimensions
        if d.field.name in bridge_state.state.column_names
    )
    intermediate_grain = frozenset(intermediate_pk) | dim_columns
    inner_arg_sql = FrozenSQL.of(inner_arg_expr.copy())
    inner_dependencies = frozenset(
        normalize_identifier(c.name)
        for c in inner_arg_expr.find_all(exp.Column)
        if normalize_identifier(c.name) in bridge_state.state.column_names
    )
    inner_column = Column(
        name=measure.metric.name,
        expression=measure.metric.expression,
        dependencies=inner_dependencies,
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=inner_fn, argument=inner_arg_sql),
    )
    inner_agg = builder.add(
        PlanOperation.AGGREGATE,
        inputs=(bridge_state.step_id,),
        state=aggregate(bridge_state.state, intermediate_grain, (inner_column,)),
        payload=AggregatePayload(
            new_grain=intermediate_grain, aggregations=(inner_column,)
        ),
    )

    # 3. Outer aggregate at the query grain.
    final_grain = _restricted_query_grain(dimensions, inner_agg.state)
    outer_arg_sql = FrozenSQL.of(exp.column(str(measure.metric.name)))
    outer_column = Column(
        name=measure.metric.name,
        expression=measure.metric.expression,
        dependencies=frozenset({measure.metric.name}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=outer_fn, argument=outer_arg_sql),
    )
    return builder.add(
        PlanOperation.AGGREGATE,
        inputs=(inner_agg.step_id,),
        state=aggregate(inner_agg.state, final_grain, (outer_column,)),
        payload=AggregatePayload(new_grain=final_grain, aggregations=(outer_column,)),
    )


def _intermediate_keys_for(
    dataset: Identifier, *, bridge: BridgeResolution, graph: RelationshipGraph
) -> tuple[Identifier, ...]:
    """Return the keys joining ``dataset`` to the bridge.

    Used to set the inner-aggregate grain so that the per-row reading
    of the nested metric runs once per ``dataset`` row.
    """
    for edge in graph.neighbors(bridge.bridge):
        # Both edges of the bridge are safe N:1; pick the one whose
        # target matches ``dataset`` and return the bridge-side keys.
        if edge.from_dataset == bridge.bridge and edge.to_dataset == dataset:
            return tuple(edge.from_columns)
        if edge.to_dataset == bridge.bridge and edge.from_dataset == dataset:
            return tuple(edge.to_columns)
    raise OSIPlanningError(  # pragma: no cover — bridge is well-formed
        ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
        f"bridge {bridge.bridge!r} has no edge to {dataset!r}",
    )


__all__ = [
    "BridgeResolution",
    "build_bridge_plan",
    "build_nested_bridge_plan",
    "can_apply_bridge_resolution",
    "find_bridge_resolutions",
]
