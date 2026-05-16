"""Join-path resolution and cardinality-driven safety checks.

Consumed by :mod:`osi.planning.planner` during query planning. Exposes
two pure helpers over an already-built :class:`RelationshipGraph`:

* :func:`find_enrichment_path` — given a fact root dataset and a set of
  target datasets that need to be joined in (because their dimensions
  or facts are referenced), return a sequence of
  :class:`JoinStep` describing a safe N:1 enrichment chain.
* :func:`assert_m_n_rejected` — inspect every edge on the returned
  path and raise :class:`OSIPlanningError` with
  :attr:`ErrorCode.E3011_MN_AGGREGATION_REJECTED` if any edge is N:N.

Ambiguity surfaces as :attr:`ErrorCode.E3001_AMBIGUOUS_JOIN_PATH`;
unreachable targets as :attr:`ErrorCode.E2004_UNREACHABLE_DATASET`.

This module never produces a :class:`~osi.planning.algebra.state.CalculationState`.
It only returns declarative descriptions the planner then hands to the
algebra operators.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from osi.common.identifiers import Identifier
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.graph import Cardinality, RelationshipEdge, RelationshipGraph
from osi.planning.algebra.operations import JoinType


@dataclass(frozen=True, slots=True)
class JoinStep:
    """Enrichment step bringing ``child`` into a state rooted at ``parent``.

    ``keys`` lives on the parent side (the join's LHS columns). The
    :class:`JoinType` is chosen from the declared referential-integrity
    hints on the edge: ``INNER`` when ``from_all_rows_match`` is known,
    else ``LEFT`` (preserves parent rows).

    ``parent_keys`` and ``child_keys`` carry the positional key pairing
    across the relationship — they're ordered sequences so a composite
    key like ``(a, b) ↔ (x, y)`` round-trips safely into codegen. The
    frozenset ``keys`` field is retained for the algebra, which only
    needs parent-side addressability.
    """

    parent: Identifier
    child: Identifier
    keys: DimensionSet
    child_columns: DimensionSet
    join_type: JoinType
    edge: RelationshipEdge
    parent_keys: tuple[Identifier, ...] = ()
    child_keys: tuple[Identifier, ...] = ()


def find_enrichment_path(
    *,
    root: Identifier,
    targets: frozenset[Identifier],
    graph: RelationshipGraph,
    allowed_relationships: frozenset[Identifier] | None = None,
) -> tuple[JoinStep, ...]:
    """Return an enrichment chain from ``root`` covering every ``target``.

    Rules
    -----
    * Each step is a single N:1 (or 1:1) edge. Multi-hop chains are
      synthesised by walking intermediate datasets that sit on the
      shortest path between the visited set and an outstanding target,
      even when those intermediates are not themselves in ``targets``
      (spec §6.6 transitive enrichment).
    * Among outstanding targets, the one with the shortest distance
      from the visited set is picked first; the first edge of its
      shortest path is emitted. Ties are broken by alphabetical target
      name.
    * If any target is unreachable, raise ``E2004_UNREACHABLE_DATASET``.
    * If any edge on the returned path is N:N, raise
      ``E3011_MN_AGGREGATION_REJECTED``.
    * If more than one equal-length shortest path from the visited set
      reaches the same closest target via *distinct first edges*, the
      path is ambiguous and raises ``E3001_AMBIGUOUS_JOIN_PATH``.

    ``allowed_relationships`` (``Proposed_OSI_Semantics.md §6.7``)
    restricts the candidate edges to the named relationships. The
    planner threads this set down from every measure's
    ``metric.joins.using_relationships`` declaration. ``None`` means
    "no restriction"; any other value is interpreted as a hard
    whitelist — edges not in the set are invisible to BFS.
    """
    if not targets:
        return ()
    outstanding = set(targets) - {root}
    if not outstanding:
        return ()
    visited: set[Identifier] = {root}
    steps: list[JoinStep] = []
    while outstanding:
        step = _next_step(
            visited=frozenset(visited),
            outstanding=frozenset(outstanding),
            graph=graph,
            allowed_relationships=allowed_relationships,
        )
        steps.append(step)
        visited.add(step.child)
        outstanding.discard(step.child)
    return tuple(steps)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PathInfo:
    """Per-node BFS state.

    Stores the distance from the visited set plus every predecessor
    ``(parent, edge)`` pair that realises a shortest path.
    """

    distance: int
    predecessors: tuple[tuple[Identifier, RelationshipEdge], ...]


def _next_step(
    *,
    visited: frozenset[Identifier],
    outstanding: frozenset[Identifier],
    graph: RelationshipGraph,
    allowed_relationships: frozenset[Identifier] | None = None,
) -> JoinStep:
    """Pick the next enrichment step via shortest-path BFS.

    BFS from the frontier ``visited`` finds the closest outstanding
    target ``closest``. We then emit the first edge of a shortest path
    from ``visited`` to ``closest``. Intermediate datasets not in
    ``outstanding`` become visited implicitly as later iterations walk
    through them. Ambiguity (distinct first edges on equal-length
    shortest paths to the same closest target) raises ``E3001``.

    ``allowed_relationships`` restricts BFS to the named relationships
    only — see :func:`find_enrichment_path`.
    """
    info = _bfs_from_visited(
        visited=visited,
        graph=graph,
        allowed_relationships=allowed_relationships,
    )

    reachable = [t for t in outstanding if t in info]
    if not reachable:
        missing = sorted(str(t) for t in outstanding)
        raise OSIPlanningError(
            ErrorCode.E2004_UNREACHABLE_DATASET,
            f"cannot reach datasets {missing} from current join state",
            context={"missing": missing, "visited": sorted(str(v) for v in visited)},
        )

    min_d = min(info[t].distance for t in reachable)
    closest = sorted((t for t in reachable if info[t].distance == min_d), key=str)[0]

    # Collect every distinct *first edge* across all shortest paths to
    # ``closest``. A first edge is any edge whose parent is in
    # ``visited`` that lies on some shortest visited→closest path.
    first_edges = _first_edges_on_shortest_paths(
        node=closest, visited=visited, info=info
    )
    distinct_edge_names = {e.name for _, e in first_edges}
    if len(distinct_edge_names) > 1:
        raise OSIPlanningError(
            ErrorCode.E3001_AMBIGUOUS_JOIN_PATH,
            f"multiple relationships reach {closest!r}; "
            "add using_relationships or rename for disambiguation",
            context={
                "child": closest,
                "candidates": sorted(distinct_edge_names),
            },
        )

    # All shortest paths share the same first edge; pick a canonical
    # (parent, edge) pair deterministically.
    parent, edge = sorted(first_edges, key=lambda pe: (str(pe[0]), str(pe[1].name)))[0]
    if not _is_safe_direction(edge, parent=parent):
        target = edge.to_dataset if edge.from_dataset == parent else edge.from_dataset
        raise _classify_unsafe_step(
            parent=parent, target=target, edge=edge, graph=graph
        )
    target = edge.to_dataset if edge.from_dataset == parent else edge.from_dataset
    return _build_step(parent=parent, target=target, edge=edge)


def _classify_unsafe_step(
    *,
    parent: Identifier,
    target: Identifier,
    edge: RelationshipEdge,
    graph: RelationshipGraph,
) -> OSIPlanningError:
    """Pick the most specific M:N error for an unsafe enrichment step.

    Per ``Proposed_OSI_Semantics.md §6.5``:

    * Declared N:N with no bridge / stitch route → ``E3012``.
    * Declared N:N where a bridge or stitch could resolve →
      ``E3012`` with the resolution surfaced in the message (the
      planner cannot synthesise the resolution itself today; it will
      learn to in a follow-up sprint).
    * Fan-trap (walking an N:1 edge from the 1-side) → keep
      ``E3011``: the user asked for an unrunnable direction; the fix
      is to flip dim/measure roles, not to stitch.
    """
    if edge.cardinality is Cardinality.N_TO_N:
        bridge = _find_bridge(parent, target, graph)
        stitch = reachable_via_n1(parent, graph) & reachable_via_n1(target, graph)
        stitch -= {parent, target}
        suggestions: list[str] = []
        if bridge:
            candidates = sorted(str(b) for b in bridge)
            suggestions.append(f"introduce a bridge dataset (candidate: {candidates})")
        if stitch:
            suggestions.append(
                "rewrite as a stitch query against shared dimension(s) "
                f"{sorted(str(s) for s in stitch)} — "
                "drop the cross-edge measure and group by the shared dim"
            )
        suggestions.append(
            f"wrap the traversal in EXISTS_IN({parent}.k, {target}.k) "
            "to convert it to a semi-join filter"
        )
        return OSIPlanningError(
            ErrorCode.E3012_MN_NO_SAFE_REWRITE,
            (
                f"relationship {edge.name!r} between {parent!r} and "
                f"{target!r} is N:N; no bridge / stitch / filter route "
                f"resolves it. Try: {'; '.join(suggestions)}."
            ),
            context={
                "relationship": edge.name,
                "from": str(edge.from_dataset),
                "to": str(edge.to_dataset),
                "bridge_candidates": sorted(str(b) for b in bridge),
                "stitch_candidates": sorted(str(s) for s in stitch),
            },
        )
    return OSIPlanningError(
        ErrorCode.E3011_MN_AGGREGATION_REJECTED,
        f"relationship {edge.name!r} cannot be traversed from "
        f"{parent!r} as an enrichment step (cardinality "
        f"{edge.cardinality.value}); this direction would fan out "
        "the parent rows",
        context={
            "relationship": edge.name,
            "parent": parent,
            "cardinality": edge.cardinality.value,
        },
    )


def _find_bridge(
    a: Identifier, b: Identifier, graph: RelationshipGraph
) -> frozenset[Identifier]:
    """Datasets that have a safe-direction edge to *both* ``a`` and ``b``.

    Per ``§6.5.1`` a bridge is "any dataset with declared N:1
    relationships to two or more other datasets." Discovery is purely
    cardinality-driven — the optional ``role: bridge`` annotation is
    diagnostic only.
    """
    candidates: set[Identifier] = set()
    for edge in graph.edges:
        # Each edge contributes a candidate parent ↦ child if the
        # parent->child direction is safe-enrichment-compatible.
        for parent, child in (
            (edge.from_dataset, edge.to_dataset),
            (edge.to_dataset, edge.from_dataset),
        ):
            if _is_safe_direction(edge, parent=parent) and child in (a, b):
                candidates.add(parent)
    bridges = {
        c
        for c in candidates
        if a in reachable_via_n1(c, graph)
        and b in reachable_via_n1(c, graph)
        and c not in (a, b)
    }
    return frozenset(bridges)


def _bfs_from_visited(
    *,
    visited: frozenset[Identifier],
    graph: RelationshipGraph,
    allowed_relationships: frozenset[Identifier] | None = None,
) -> dict[Identifier, _PathInfo]:
    """Unweighted BFS from the frontier on the *undirected* graph.

    We explore all edges regardless of cardinality / direction, so the
    planner can surface the precise reason a target is unreachable via
    a safe enrichment path: an N:N edge on the only path raises
    ``E3011``, a fan-trap direction raises ``E3011``, and a genuinely
    disconnected target raises ``E2004``. Safety is checked at step
    extraction in :func:`_next_step`, not during BFS itself.

    ``allowed_relationships`` restricts which edges BFS considers. An
    edge whose name is not in the allowed set is skipped entirely, so
    a target reachable only through forbidden edges falls out as
    ``E2004_UNREACHABLE_DATASET`` — the same way a genuinely
    disconnected target would.
    """
    info: dict[Identifier, _PathInfo] = {
        v: _PathInfo(distance=0, predecessors=()) for v in visited
    }
    queue: deque[Identifier] = deque(sorted(visited, key=str))
    while queue:
        node = queue.popleft()
        d = info[node].distance
        for edge in graph.neighbors(node):
            if (
                allowed_relationships is not None
                and edge.name not in allowed_relationships
            ):
                continue
            for nxt in _outgoing_endpoints(edge, node):
                if nxt in visited:
                    continue
                nd = d + 1
                existing = info.get(nxt)
                if existing is None:
                    info[nxt] = _PathInfo(distance=nd, predecessors=((node, edge),))
                    queue.append(nxt)
                elif nd == existing.distance:
                    info[nxt] = _PathInfo(
                        distance=existing.distance,
                        predecessors=existing.predecessors + ((node, edge),),
                    )
                # nd > existing.distance: ignore (not a shortest path).
    return info


def _is_safe_direction(edge: RelationshipEdge, *, parent: Identifier) -> bool:
    """Return whether ``parent -> other`` via ``edge`` is a safe enrichment.

    Safe iff the edge is N:1 with ``parent`` on the N-side, or 1:1 in
    either direction. N:N is never safe, and traversing an N:1 edge
    from the 1-side to the N-side is a fan trap.
    """
    if edge.cardinality is Cardinality.N_TO_N:
        return False
    if edge.cardinality is Cardinality.ONE_TO_ONE:
        return edge.from_dataset == parent or edge.to_dataset == parent
    return edge.from_dataset == parent  # N_TO_ONE, N-side → 1-side only


def _first_edges_on_shortest_paths(
    *,
    node: Identifier,
    visited: frozenset[Identifier],
    info: dict[Identifier, _PathInfo],
) -> set[tuple[Identifier, RelationshipEdge]]:
    """Collect every distinct first-edge that starts a shortest path.

    Walk all shortest paths back to the visited frontier and return
    every ``(parent_in_visited, edge)`` that begins one. Edges are
    identified by ``.name`` in the caller; the tuple lets the caller
    reconstruct the step.
    """
    out: set[tuple[Identifier, RelationshipEdge]] = set()
    stack: list[Identifier] = [node]
    seen: set[Identifier] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for parent, edge in info[cur].predecessors:
            if parent in visited:
                out.add((parent, edge))
            else:
                stack.append(parent)
    return out


def _outgoing_endpoints(
    edge: RelationshipEdge, from_: Identifier
) -> tuple[Identifier, ...]:
    if edge.from_dataset == from_:
        return (edge.to_dataset,)
    if edge.to_dataset == from_:
        return (edge.from_dataset,)
    return ()


def reachable_via_n1(
    root: Identifier, graph: RelationshipGraph
) -> frozenset[Identifier]:
    """Datasets reachable from ``root`` by walking only safe-direction edges.

    "Safe-direction" means N:1 from ``root``'s side or 1:1 in either
    direction — the same predicate :func:`_is_safe_direction` uses to
    pick enrichment steps. Used by the planner's M:N classifier to
    decide whether two endpoints share a *stitching dimension*
    (``Proposed_OSI_Semantics.md §6.5.2``).

    The result includes ``root`` itself.
    """
    visited: set[Identifier] = {root}
    queue: deque[Identifier] = deque([root])
    while queue:
        node = queue.popleft()
        for edge in graph.neighbors(node):
            if not _is_safe_direction(edge, parent=node):
                continue
            for nxt in _outgoing_endpoints(edge, node):
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append(nxt)
    return frozenset(visited)


def datasets_connected(a: Identifier, b: Identifier, graph: RelationshipGraph) -> bool:
    """Return ``True`` iff ``a`` and ``b`` are in the same connected component.

    Direction-agnostic — used to decide whether two facts could in
    principle share a stitching dimension (which requires the graph
    to be connected through them).
    """
    if a == b:
        return True
    visited: set[Identifier] = {a}
    queue: deque[Identifier] = deque([a])
    while queue:
        node = queue.popleft()
        for edge in graph.neighbors(node):
            for nxt in _outgoing_endpoints(edge, node):
                if nxt in visited:
                    continue
                if nxt == b:
                    return True
                visited.add(nxt)
                queue.append(nxt)
    return False


def _build_step(
    *, parent: Identifier, target: Identifier, edge: RelationshipEdge
) -> JoinStep:
    if edge.from_dataset == parent and edge.to_dataset == target:
        parent_cols = tuple(edge.from_columns)
        child_cols = tuple(edge.to_columns)
        join_type = _choose_join_type(
            parent_all_match=edge.from_all_rows_match,
            to_all_match=edge.to_all_rows_match,
        )
    else:
        # Reverse direction (target declared as ``from``, current parent
        # as ``to``). Only safe when the reverse edge is N:1, i.e. the
        # original was 1:N — which is not N:1 from ``parent`` to
        # ``target``; we detect M:N separately.
        parent_cols = tuple(edge.to_columns)
        child_cols = tuple(edge.from_columns)
        join_type = _choose_join_type(
            parent_all_match=edge.to_all_rows_match,
            to_all_match=edge.from_all_rows_match,
        )
    return JoinStep(
        parent=parent,
        child=target,
        keys=frozenset(parent_cols),
        child_columns=frozenset(child_cols),
        join_type=join_type,
        edge=edge,
        parent_keys=parent_cols,
        child_keys=child_cols,
    )


def _choose_join_type(*, parent_all_match: bool, to_all_match: bool) -> JoinType:
    _ = to_all_match  # reserved: bias to INNER when both sides match
    return JoinType.INNER if parent_all_match else JoinType.LEFT


def _reject_m_n(edge: RelationshipEdge) -> None:
    if edge.cardinality is Cardinality.N_TO_N:
        raise OSIPlanningError(
            ErrorCode.E3011_MN_AGGREGATION_REJECTED,
            f"relationship {edge.name!r} is N:N; semantic enrich "
            "requires N:1 or 1:1",
            context={"relationship": edge.name},
        )


__all__ = [
    "JoinStep",
    "datasets_connected",
    "find_enrichment_path",
    "reachable_via_n1",
]
