"""The :class:`QueryPlan` value type — the planner's output.

A :class:`QueryPlan` is a deterministic, dialect-agnostic, immutable
description of the algebra composition that answers a
:class:`~osi.planning.semantic_query.SemanticQuery`. The codegen layer
turns it into SQL; nothing between planning and codegen inspects models
or namespaces.

Shape
-----
A plan is a directed acyclic graph of :class:`PlanStep` nodes. The root
is the step whose state matches the query's output columns and grain.
Every step carries:

* ``operation`` — which algebra operator produced this step
* ``inputs`` — the step IDs of upstream states (0 for ``SOURCE``, 1 for
  unary operators, 2 for ``MERGE`` / ``FILTERING_JOIN``)
* ``state`` — the :class:`~osi.planning.algebra.state.CalculationState`
  this step evaluates to (so goldens snapshot grain + columns)
* ``payload`` — operator-specific arguments (predicates, join keys,
  new grain, aggregation columns, etc.). Intentionally kept as a
  typed variant: the golden tests snapshot its canonical form.

Determinism invariants (``ARCHITECTURE.md §6``):

* step IDs are integers assigned in *topological* (post-order) traversal
* ``inputs`` are sorted by step ID
* ``columns`` within a step's state preserve operator-chosen order
  (e.g. ``project`` respects the caller's column list)

Golden tests import ``QueryPlan.to_json()`` to generate snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional

from osi.common.identifiers import Identifier
from osi.common.sql_expr import FrozenSQL
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra.operations import FilterMode, JoinType
from osi.planning.algebra.state import CalculationState, Column


class PlanOperation(StrEnum):
    """The nine operators of the closed algebra, surfaced into the plan."""

    SOURCE = "source"
    FILTER = "filter"
    ENRICH = "enrich"
    AGGREGATE = "aggregate"
    PROJECT = "project"
    ADD_COLUMNS = "add_columns"
    MERGE = "merge"
    FILTERING_JOIN = "filtering_join"
    BROADCAST = "broadcast"


# ---------------------------------------------------------------------------
# Operator-specific payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourcePayload:
    """Payload for :attr:`PlanOperation.SOURCE`.

    ``source`` is the physical table reference copied from
    :attr:`osi.parsing.models.Dataset.source`. Carrying it on the plan
    means codegen never has to reach back into the model — a strict
    Layer-3 boundary.
    """

    dataset: Identifier
    primary_key: DimensionSet
    source: str = ""


@dataclass(frozen=True, slots=True)
class FilterPayload:
    """Payload for :attr:`PlanOperation.FILTER`.

    Carries both the predicate AST and the column dependencies the
    algebra used to validate it. Keeping them together makes goldens
    self-describing.
    """

    predicate: FrozenSQL
    dependencies: frozenset[Identifier]
    is_post_aggregate: bool = False


@dataclass(frozen=True, slots=True)
class EnrichPayload:
    """Payload for :attr:`PlanOperation.ENRICH`.

    ``child_source`` is the child dataset's physical source — codegen
    uses it directly and never looks up the model.

    ``parent_keys`` / ``child_keys`` record the key pairing across the
    relationship. For self-matching keys they're equal, but relationships
    like ``orders.customer_id → customers.id`` have different names on
    each side. The algebra's ``keys`` field still addresses parent-side
    columns; the split-out sequences exist solely for codegen.
    """

    child_dataset: Identifier
    child_columns: tuple[Column, ...]
    keys: DimensionSet
    join_type: JoinType
    child_source: str = ""
    parent_keys: tuple[Identifier, ...] = ()
    child_keys: tuple[Identifier, ...] = ()


@dataclass(frozen=True, slots=True)
class EnrichDerivedPayload:
    """Payload for :attr:`PlanOperation.ENRICH` against a *derived* child.

    Carries the same join-key contract as :class:`EnrichPayload` but
    treats the child as an upstream :class:`PlanStep` rather than a
    base table. Used by the bridge-resolution planner
    (``Proposed_OSI_Semantics.md §6.5.1``, mid-pipeline form): the
    child is a pre-aggregated state at the bridge's join-key grain,
    not a freshly-sourced dataset.

    ``ENRICH`` steps with this payload have **two** inputs (parent
    step, child step) instead of one. Codegen reads the child as the
    second input's CTE alias, never as ``to_table(...)``.
    """

    child_columns: tuple[Column, ...]
    keys: DimensionSet
    join_type: JoinType
    parent_keys: tuple[Identifier, ...] = ()
    child_keys: tuple[Identifier, ...] = ()


@dataclass(frozen=True, slots=True)
class AggregatePayload:
    """Payload for :attr:`PlanOperation.AGGREGATE`."""

    new_grain: DimensionSet
    aggregations: tuple[Column, ...]


@dataclass(frozen=True, slots=True)
class ProjectPayload:
    """Payload for :attr:`PlanOperation.PROJECT`."""

    columns: tuple[Identifier, ...]


@dataclass(frozen=True, slots=True)
class AddColumnsPayload:
    """Payload for :attr:`PlanOperation.ADD_COLUMNS`.

    ``ADD_COLUMNS`` is emitted only for **composite metrics**
    (``Proposed_OSI_Semantics.md §5.4``). The planner lowers each
    composite metric in a measure group into a post-``AGGREGATE``
    ``ADD_COLUMNS`` step whose ``definitions`` reference base
    aggregate columns. No other planner path emits this step today.
    """

    definitions: tuple[Column, ...]


@dataclass(frozen=True, slots=True)
class MergePayload:
    """Payload for :attr:`PlanOperation.MERGE`."""

    on: DimensionSet


@dataclass(frozen=True, slots=True)
class FilteringJoinPayload:
    """Payload for :attr:`PlanOperation.FILTERING_JOIN`."""

    lhs_keys: DimensionSet
    rhs_keys: DimensionSet
    mode: FilterMode


@dataclass(frozen=True, slots=True)
class BroadcastPayload:
    """Payload for :attr:`PlanOperation.BROADCAST`.

    **Reserved.** ``broadcast`` is defined in the algebra
    (``Proposed_OSI_Semantics.md §4.8``) so scalar-per-row attach
    semantics have a stable operator, but today's planner never
    emits a ``BROADCAST`` step — cross-grain scalar attachment is
    expressed by a percent-of-total composite metric instead
    (``§5.4``). The operator and this payload are kept so a future
    sprint can turn it on without a SPEC change.
    """

    column: Column


PlanPayload = (
    SourcePayload
    | FilterPayload
    | EnrichPayload
    | EnrichDerivedPayload
    | AggregatePayload
    | ProjectPayload
    | AddColumnsPayload
    | MergePayload
    | FilteringJoinPayload
    | BroadcastPayload
)


# ---------------------------------------------------------------------------
# Step + plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One node in the plan DAG.

    ``step_id`` is assigned at construction time by the planner; callers
    must never re-number steps after the fact.
    """

    step_id: int
    operation: PlanOperation
    inputs: tuple[int, ...]
    state: CalculationState
    payload: PlanPayload


@dataclass(frozen=True, slots=True)
class OrderByEntry:
    """Output-side ordering, carried on :class:`QueryPlan`."""

    column: Identifier
    descending: bool = False


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Deterministic, ordered list of :class:`PlanStep` plus output metadata.

    ``steps`` is stored in topological order — the last entry is the
    root; all step IDs in ``inputs`` reference earlier entries.

    ``order_by`` and ``limit`` are carried outside the algebra because
    the algebra has no notion of row ordering.
    """

    steps: tuple[PlanStep, ...]
    root_step_id: int
    order_by: tuple[OrderByEntry, ...] = ()
    limit: Optional[int] = None
    output_columns: tuple[Identifier, ...] = field(default_factory=tuple)
    # S-7: optional output-column rename map. Codegen uses it to emit
    # ``column AS alias`` in the final ``SELECT``. The plan still
    # carries the internal column names everywhere upstream — aliases
    # only affect what the user sees.
    output_aliases: tuple[tuple[Identifier, Identifier], ...] = ()

    def __post_init__(self) -> None:
        """Verify topological ordering and root ID invariants.

        Violations here are not user-facing — they mean a planner pass
        produced an inconsistent ``QueryPlan``. We surface the failure
        through the typed-error channel (``E_INTERNAL_INVARIANT``) so
        the "every failure carries a code" property test still holds.
        """
        seen: set[int] = set()
        for step in self.steps:
            for dep in step.inputs:
                if dep not in seen:
                    raise OSIError(
                        ErrorCode.E_INTERNAL_INVARIANT,
                        f"step {step.step_id} references unplanned "
                        f"input {dep} (steps must be topologically "
                        "ordered)",
                        context={
                            "step_id": step.step_id,
                            "unplanned_input": dep,
                        },
                    )
            seen.add(step.step_id)
        if self.root_step_id not in seen:
            raise OSIError(
                ErrorCode.E_INTERNAL_INVARIANT,
                f"root_step_id {self.root_step_id} is not a step in " "this plan",
                context={
                    "root_step_id": self.root_step_id,
                    "step_ids": sorted(seen),
                },
            )

    @property
    def root(self) -> PlanStep:
        """Return the terminal step whose state matches the query output."""
        return next(s for s in self.steps if s.step_id == self.root_step_id)

    def to_json(self) -> Mapping[str, Any]:
        """Return a deterministic JSON-ready representation for goldens."""
        return {
            "root_step_id": self.root_step_id,
            "output_columns": [str(c) for c in self.output_columns],
            "order_by": [
                {"column": str(o.column), "descending": o.descending}
                for o in self.order_by
            ],
            "limit": self.limit,
            "steps": [_step_to_json(s) for s in self.steps],
        }


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _step_to_json(step: PlanStep) -> Mapping[str, Any]:
    return {
        "step_id": step.step_id,
        "operation": step.operation.value,
        "inputs": list(step.inputs),
        "grain": sorted(str(g) for g in step.state.grain),
        "columns": [_column_to_json(c) for c in step.state.columns],
        "payload": _payload_to_json(step.payload),
    }


def _column_to_json(col: Column) -> Mapping[str, Any]:
    agg: Mapping[str, Any] | None = None
    if col.aggregate is not None:
        agg = {
            "function": col.aggregate.function.name,
            "argument": col.aggregate.argument.canonical,
        }
    return {
        "name": str(col.name),
        "kind": col.kind.value,
        "expression": col.expression.canonical,
        "dependencies": sorted(str(d) for d in col.dependencies),
        "aggregate": agg,
        "is_single_valued": col.is_single_valued,
        "from_join_rhs": col.from_join_rhs,
    }


def _payload_to_json(payload: PlanPayload) -> Mapping[str, Any]:
    if isinstance(payload, SourcePayload):
        return {
            "kind": "source",
            "dataset": str(payload.dataset),
            "source": payload.source,
            "primary_key": sorted(str(p) for p in payload.primary_key),
        }
    if isinstance(payload, FilterPayload):
        return {
            "kind": "filter",
            "predicate": payload.predicate.canonical,
            "dependencies": sorted(str(d) for d in payload.dependencies),
            "post_aggregate": payload.is_post_aggregate,
        }
    if isinstance(payload, EnrichPayload):
        return {
            "kind": "enrich",
            "child_dataset": str(payload.child_dataset),
            "child_source": payload.child_source,
            "keys": sorted(str(k) for k in payload.keys),
            "parent_keys": [str(k) for k in payload.parent_keys],
            "child_keys": [str(k) for k in payload.child_keys],
            "join_type": payload.join_type.name,
            "child_columns": [_column_to_json(c) for c in payload.child_columns],
        }
    if isinstance(payload, EnrichDerivedPayload):
        return {
            "kind": "enrich_derived",
            "keys": sorted(str(k) for k in payload.keys),
            "parent_keys": [str(k) for k in payload.parent_keys],
            "child_keys": [str(k) for k in payload.child_keys],
            "join_type": payload.join_type.name,
            "child_columns": [_column_to_json(c) for c in payload.child_columns],
        }
    if isinstance(payload, AggregatePayload):
        return {
            "kind": "aggregate",
            "new_grain": sorted(str(g) for g in payload.new_grain),
            "aggregations": [_column_to_json(c) for c in payload.aggregations],
        }
    if isinstance(payload, ProjectPayload):
        return {
            "kind": "project",
            "columns": [str(c) for c in payload.columns],
        }
    if isinstance(payload, AddColumnsPayload):
        return {
            "kind": "add_columns",
            "definitions": [_column_to_json(c) for c in payload.definitions],
        }
    if isinstance(payload, MergePayload):
        return {
            "kind": "merge",
            "on": sorted(str(k) for k in payload.on),
        }
    if isinstance(payload, FilteringJoinPayload):
        return {
            "kind": "filtering_join",
            "lhs_keys": sorted(str(k) for k in payload.lhs_keys),
            "rhs_keys": sorted(str(k) for k in payload.rhs_keys),
            "mode": payload.mode.name,
        }
    if isinstance(payload, BroadcastPayload):
        return {
            "kind": "broadcast",
            "column": _column_to_json(payload.column),
        }
    raise OSIError(
        ErrorCode.E_INTERNAL_INVARIANT,
        f"unknown payload type: {type(payload).__name__} — every "
        "PlanPayload subclass must have a case in _payload_to_json",
        context={"payload_type": type(payload).__name__},
    )


__all__ = [
    "AddColumnsPayload",
    "AggregatePayload",
    "BroadcastPayload",
    "EnrichDerivedPayload",
    "EnrichPayload",
    "FilterPayload",
    "FilteringJoinPayload",
    "MergePayload",
    "OrderByEntry",
    "PlanOperation",
    "PlanPayload",
    "PlanStep",
    "ProjectPayload",
    "QueryPlan",
    "SourcePayload",
]
