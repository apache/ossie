"""Relationship graph built from a :class:`SemanticModel`.

Used by the planner (Phase 3) to find join paths between datasets. The
graph is directed (from → to, reflecting the declared N:1 direction) but
carries undirected adjacency for path-finding — the planner decides
direction based on the query context.

Cardinality is inferred here from declared PKs / UKs per
``Proposed_OSI_Semantics.md §6.1``. The result is stored on each edge
and re-used by the planner.

The graph itself is immutable. Building it is the final step of
parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from osi.common.identifiers import Identifier
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.models import Dataset, Relationship, SemanticModel


class Cardinality(StrEnum):
    """Inferred relationship cardinality (``§6.1``)."""

    N_TO_ONE = "N:1"
    ONE_TO_ONE = "1:1"
    N_TO_N = "N:N"


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    """Directed edge in the relationship graph."""

    name: Identifier
    from_dataset: Identifier
    to_dataset: Identifier
    from_columns: tuple[Identifier, ...]
    to_columns: tuple[Identifier, ...]
    cardinality: Cardinality
    from_all_rows_match: bool
    to_all_rows_match: bool


@dataclass(frozen=True, slots=True)
class RelationshipGraph:
    """Index over relationships with adjacency for path-finding."""

    edges: tuple[RelationshipEdge, ...]
    _adjacency: dict[Identifier, tuple[RelationshipEdge, ...]] = field(
        default_factory=dict
    )

    def neighbors(self, dataset: Identifier) -> tuple[RelationshipEdge, ...]:
        """Edges touching ``dataset`` on either side."""
        return self._adjacency.get(dataset, ())

    def find_paths(
        self,
        start: Identifier,
        end: Identifier,
        *,
        max_depth: int = 6,
    ) -> tuple[tuple[RelationshipEdge, ...], ...]:
        """All simple paths between two datasets up to ``max_depth``.

        The planner uses this to detect ambiguous joins (``E3001``).
        Order is deterministic (edges by declaration order).
        """
        if start == end:
            return ((),)
        results: list[tuple[RelationshipEdge, ...]] = []
        self._dfs_paths(start, end, tuple(), {start}, max_depth, results)
        return tuple(results)

    def _dfs_paths(
        self,
        current: Identifier,
        target: Identifier,
        path: tuple[RelationshipEdge, ...],
        visited: set[Identifier],
        depth_left: int,
        out: list[tuple[RelationshipEdge, ...]],
    ) -> None:
        if depth_left == 0:
            return
        for edge in self.neighbors(current):
            other = (
                edge.to_dataset if edge.from_dataset == current else edge.from_dataset
            )
            if other in visited:
                continue
            new_path = path + (edge,)
            if other == target:
                out.append(new_path)
                continue
            self._dfs_paths(
                other,
                target,
                new_path,
                visited | {other},
                depth_left - 1,
                out,
            )


def build_graph(model: SemanticModel) -> RelationshipGraph:
    """Construct a :class:`RelationshipGraph` from a validated model."""
    datasets_by_name = {ds.name: ds for ds in model.datasets}
    edges: list[RelationshipEdge] = []
    adjacency: dict[Identifier, list[RelationshipEdge]] = {
        ds.name: [] for ds in model.datasets
    }
    for rel in model.relationships:
        edge = _build_edge(rel, datasets_by_name)
        edges.append(edge)
        adjacency[edge.from_dataset].append(edge)
        adjacency[edge.to_dataset].append(edge)
    return RelationshipGraph(
        edges=tuple(edges),
        _adjacency={k: tuple(v) for k, v in adjacency.items()},
    )


def _build_edge(
    relationship: Relationship,
    datasets_by_name: dict[Identifier, Dataset],
) -> RelationshipEdge:
    from_ds = datasets_by_name.get(relationship.from_dataset)
    to_ds = datasets_by_name.get(relationship.to_dataset)
    if from_ds is None or to_ds is None:  # defensive — caught in validation
        raise OSIParseError(
            ErrorCode.E2006_INVALID_RELATIONSHIP,
            f"relationship {relationship.name!r} references missing dataset",
            context={"name": relationship.name},
        )
    cardinality = _infer_cardinality(
        from_columns=relationship.from_columns,
        to_columns=relationship.to_columns,
        from_dataset=from_ds,
        to_dataset=to_ds,
    )
    # ``referential_integrity`` is a deferred feature (D-018 / §10):
    # the model has no such field, so RI hints can't propagate into
    # the edge. ``from_all_rows_match`` / ``to_all_rows_match`` stay
    # ``False`` until the proposal lands.
    return RelationshipEdge(
        name=relationship.name,
        from_dataset=relationship.from_dataset,
        to_dataset=relationship.to_dataset,
        from_columns=relationship.from_columns,
        to_columns=relationship.to_columns,
        cardinality=cardinality,
        from_all_rows_match=False,
        to_all_rows_match=False,
    )


def _infer_cardinality(
    *,
    from_columns: tuple[Identifier, ...],
    to_columns: tuple[Identifier, ...],
    from_dataset: Dataset,
    to_dataset: Dataset,
) -> Cardinality:
    """Infer cardinality per ``§6.1``."""
    to_unique = _columns_match_any_key(to_columns, to_dataset)
    from_unique = _columns_match_any_key(from_columns, from_dataset)
    if to_unique and from_unique:
        return Cardinality.ONE_TO_ONE
    if to_unique:
        return Cardinality.N_TO_ONE
    return Cardinality.N_TO_N


def _columns_match_any_key(columns: tuple[Identifier, ...], dataset: Dataset) -> bool:
    """Return ``True`` if ``columns`` match the PK or any UK of ``dataset``."""
    target = frozenset(columns)
    if target and target == frozenset(dataset.primary_key):
        return True
    for uk in dataset.unique_keys:
        if target == frozenset(uk):
            return True
    return False


__all__ = [
    "Cardinality",
    "RelationshipEdge",
    "RelationshipGraph",
    "build_graph",
]
