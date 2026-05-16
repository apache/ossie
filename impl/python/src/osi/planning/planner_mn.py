"""M:N resolution helpers for the planner.

Carved out of :mod:`osi.planning.planner` so the entry-point file
stays under the LOC cap. Everything here is :pep:`8` private to the
package — only re-exported via ``__all__`` so the planner can import
the small set of symbols it needs without re-exposing the whole
module.

Three concerns live here:

* ``Proposed_OSI_Semantics.md §6.5.1`` bridge anchor discovery for
  dimension-only queries (:func:`find_bridge_anchors`,
  :func:`build_dimension_only_group`).
* ``§6.5.2`` multi-fact stitch validation
  (:func:`validate_multi_fact_stitch`).
* ``§6.7`` per-metric ``using_relationships`` intersection
  (:func:`group_allowed_relationships`).

Pure functions over already-resolved planner inputs; no SQL is
generated and no algebra steps are emitted. Errors raised here are
the ``E3001`` / ``E3013`` family the spec mandates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from osi.common.identifiers import Identifier
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning.joins import datasets_connected, find_enrichment_path
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedDimension, ResolvedMetric


@dataclass(frozen=True, slots=True)
class MeasureGroup:
    """All measures that share a single fact dataset.

    The same shape as :class:`osi.planning.planner._MeasureGroup`;
    re-declared here so this module has no circular dependency on
    :mod:`osi.planning.planner`.
    """

    fact_dataset: Identifier
    measures: tuple[ResolvedMetric, ...]


def find_bridge_anchors(
    targets: frozenset[Identifier], context: PlannerContext
) -> tuple[Identifier, ...]:
    """Datasets outside ``targets`` that can reach every target safely.

    A bridge candidate is any dataset whose safe-direction enrichment
    closure (per :func:`osi.planning.joins.reachable_via_n1`) covers
    ``targets``. Used by :func:`build_dimension_only_group` to discover
    the ``§6.5.1`` bridge route when no referenced dataset can serve as
    anchor.
    """
    out: list[Identifier] = []
    for ds in sorted(context.model.datasets, key=lambda d: str(d.name)):
        if ds.name in targets:
            continue
        try:
            find_enrichment_path(root=ds.name, targets=targets, graph=context.graph)
        except OSIPlanningError:
            continue
        out.append(ds.name)
    return tuple(out)


def build_dimension_only_group(
    dims: Sequence[ResolvedDimension], context: PlannerContext
) -> tuple[MeasureGroup, ...]:
    """Pick a single safe anchor dataset for a dimension-only query.

    Rules (``Proposed_OSI_Semantics.md §5.3`` applied to the
    no-measures case):

    * Single-dataset queries always succeed.
    * Multi-dataset queries require a unique anchor that can reach
      every *other* referenced dataset via a safe N:1 (or 1:1) path.
      Picking an anchor this way is *direction-aware* — a 1:N
      traversal would be a fan trap and is rejected.
    * If no referenced dataset can serve as anchor, fall back to the
      ``§6.5.1`` bridge route: any third dataset that itself reaches
      every target via safe enrichment. Multiple bridge candidates
      raise ``E3001_AMBIGUOUS_JOIN_PATH``.
    * Otherwise: re-raise the path-finder's last error (typically
      ``E2004`` unreachable, ``E3011`` fan-trap, or ``E3012`` for an
      unresolvable N:N).
    * More than one valid anchor →
      :attr:`ErrorCode.E3001_AMBIGUOUS_JOIN_PATH`: without measures
      there is no fact-side signal to break the tie, so the caller
      must either drop a dimension or explicitly request a measure.
    """
    if not dims:
        raise OSIPlanningError(
            ErrorCode.E1002_MISSING_REQUIRED_FIELD,
            "cannot plan a query with neither dimensions nor measures",
        )
    datasets = tuple({d.dataset for d in dims})
    if len(datasets) == 1:
        return (MeasureGroup(fact_dataset=datasets[0], measures=()),)

    dataset_set = frozenset(datasets)
    valid: list[Identifier] = []
    last_error: OSIPlanningError | None = None
    for candidate in sorted(datasets, key=str):
        try:
            find_enrichment_path(
                root=candidate, targets=dataset_set, graph=context.graph
            )
        except OSIPlanningError as exc:
            last_error = exc
            continue
        valid.append(candidate)

    if len(valid) == 1:
        return (MeasureGroup(fact_dataset=valid[0], measures=()),)

    if not valid:
        bridges = find_bridge_anchors(dataset_set, context)
        if len(bridges) == 1:
            return (MeasureGroup(fact_dataset=bridges[0], measures=()),)
        if len(bridges) > 1:
            raise OSIPlanningError(
                ErrorCode.E3001_AMBIGUOUS_JOIN_PATH,
                (
                    "dimension-only query touches datasets "
                    f"{sorted(str(d) for d in datasets)} which are not "
                    "directly reachable from each other; multiple bridge "
                    f"datasets {sorted(str(b) for b in bridges)} can "
                    "resolve the join. Disambiguate with "
                    "joins.using_relationships on a metric or by adding "
                    "a measure that selects one bridge."
                ),
                context={
                    "datasets": sorted(str(d) for d in datasets),
                    "bridge_candidates": sorted(str(b) for b in bridges),
                },
            )
        assert last_error is not None
        raise last_error

    raise OSIPlanningError(
        ErrorCode.E3001_AMBIGUOUS_JOIN_PATH,
        (
            "dimension-only query touches datasets "
            f"{sorted(str(d) for d in datasets)} with multiple valid "
            f"anchors {sorted(str(d) for d in valid)}; add a measure or "
            "drop a dimension to resolve"
        ),
        context={
            "datasets": sorted(str(d) for d in datasets),
            "candidates": sorted(str(d) for d in valid),
        },
    )


def validate_multi_fact_stitch(
    groups: Sequence[MeasureGroup],
    dimensions: Sequence[ResolvedDimension],
    context: PlannerContext,
) -> None:
    """Reject silent Cartesian merges with ``E3013_NO_STITCHING_DIMENSION``.

    Per ``Proposed_OSI_Semantics.md §6.5.2`` the stitch route is
    rejected when the query's dimension set is empty AND the two
    endpoints share no path. Without this check the planner would
    aggregate each fact to ``frozenset()`` grain and emit a
    single-row Cartesian merge, which silently fabricates a
    relationship that the model never declared.

    Multi-fact queries that *do* provide dimensions surface the same
    issue downstream as ``E2004_UNREACHABLE_DATASET`` (the dim is not
    reachable from one of the facts), so they are intentionally not
    handled here.
    """
    if len(groups) <= 1:
        return
    if dimensions:
        return
    facts = [g.fact_dataset for g in groups]
    for i, a in enumerate(facts):
        for b in facts[i + 1 :]:
            if not datasets_connected(a, b, context.graph):
                raise OSIPlanningError(
                    ErrorCode.E3013_NO_STITCHING_DIMENSION,
                    (
                        f"facts {a!r} and {b!r} have no path through "
                        "the relationship graph and the query has no "
                        "shared dimension; their merge would be a "
                        "Cartesian product. Add a shared dimension to "
                        "the query, or declare a relationship that "
                        "links the two facts."
                    ),
                    context={
                        "facts": [str(a), str(b)],
                    },
                )


def group_allowed_relationships(
    group: MeasureGroup,
) -> frozenset[Identifier] | None:
    """Combine ``metric.joins.using_relationships`` across a measure group.

    Spec semantics (``Proposed_OSI_Semantics.md §6.7``): each metric's
    override applies to *that metric's* aggregation joins. Since the
    Foundation planner shares one enrichment chain per group, we
    combine the per-metric whitelists with **intersection** so every
    metric's restriction is honoured. Special cases:

    * No metric in the group declares ``using_relationships`` →
      return ``None`` (no restriction).
    * At least one metric declares ``using_relationships``: the
      effective whitelist is the intersection of every declared set
      (metrics that don't declare a list contribute the universe and
      therefore never narrow the intersection).
    * If the intersection is empty the planner falls through to
      :attr:`ErrorCode.E2004_UNREACHABLE_DATASET` — by construction no
      edge is allowed, so no dataset outside ``root`` is reachable.
    """
    declared: list[frozenset[Identifier]] = []
    for resolved in group.measures:
        joins = resolved.metric.joins
        if joins is not None and joins.using_relationships:
            declared.append(frozenset(joins.using_relationships))
    if not declared:
        return None
    intersection = declared[0]
    for s in declared[1:]:
        intersection = intersection & s
    return intersection


__all__ = [
    "MeasureGroup",
    "build_dimension_only_group",
    "find_bridge_anchors",
    "group_allowed_relationships",
    "validate_multi_fact_stitch",
]
