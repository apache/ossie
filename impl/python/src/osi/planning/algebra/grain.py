"""Symbolic grain helpers used by tests and diagnostics.

**Audience.** This module is internal to the algebra package: the
property tests in ``tests/properties/test_grain_closure.py`` use
:func:`simulate` / :func:`simulate_grain` to compare symbolic
computation against the concrete algebra, and
``osi.diagnostics.explain`` uses :func:`combine_grains` to summarise
plan steps. External callers (adapters, alternative planners) should
construct real :class:`CalculationState` instances and use the
algebra operators rather than reaching into the simulator. The
``__all__`` at the bottom of this module is the supported surface.

Per ``../../../../docs/JOIN_ALGEBRA.md §4.4``
the resulting grain of any operator chain is a pure function of the
argument sequence. This module exposes that function without needing
to construct real states — the property test ``test_grain_closure.py``
uses it to compare symbolic computation to the concrete algebra.

The simulator tracks **two** pieces of grain-relevant state:

* ``grain`` — the dimensions that uniquely identify a row in the
  current state. This matches ``CalculationState.grain``.
* ``single_valued`` — extra columns proven single-valued over the
  current grain (typically dimensions brought in via N:1 ``enrich`` or
  scalars attached via ``broadcast``). ``aggregate`` may coarsen *to*
  any subset of ``grain ∪ single_valued``; tracking the second set
  lets the simulator accept the hot star-schema path of
  *enrich-then-aggregate-by-RHS-dim* without falsely rejecting it.

Promoting ``single_valued`` to first-class state in the simulator
mirrors the per-column ``Column.is_single_valued`` flag in the
concrete algebra (see :mod:`osi.planning.algebra.state`). When the
Foundation grows grain operations or filter-context manipulation, the
extra book-keeping is already there for them to use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto

from osi.common.identifiers import Identifier
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIError


class OperatorTag(StrEnum):
    """Enum identifying which operator a step represents.

    Kept as an enum (not the callable) so a grain simulation can run
    purely over data without importing :mod:`operations`.
    """

    SOURCE = auto()
    FILTER = auto()
    ENRICH = auto()
    AGGREGATE = auto()
    PROJECT = auto()
    ADD_COLUMNS = auto()
    MERGE = auto()
    FILTERING_JOIN = auto()
    BROADCAST = auto()


@dataclass(frozen=True, slots=True)
class SourceStep:
    """Start a chain from a dataset primary key."""

    tag: OperatorTag
    primary_key: DimensionSet


@dataclass(frozen=True, slots=True)
class AggregateStep:
    """Coarsen to ``target_grain``."""

    tag: OperatorTag
    target_grain: DimensionSet


@dataclass(frozen=True, slots=True)
class MergeStep:
    """Merge with another chain; both sides must share grain."""

    tag: OperatorTag
    right_grain: DimensionSet


@dataclass(frozen=True, slots=True)
class EnrichStep:
    """N:1 enrich; preserves grain and extends single-valued vocabulary.

    ``adds`` is the set of RHS column names that flow in. They become
    valid coarsening targets for a subsequent ``aggregate`` because an
    N:1 join makes them single-valued over the parent grain.
    """

    tag: OperatorTag
    adds: frozenset[Identifier] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class BroadcastStep:
    """Attach a scalar; preserves grain, extends single-valued vocabulary."""

    tag: OperatorTag
    adds: Identifier


@dataclass(frozen=True, slots=True)
class SimpleStep:
    """A grain-preserving operator with no single-valued vocabulary effect.

    Covers ``filter``, ``project``, ``add_columns``, and
    ``filtering_join`` — all of which preserve the row multiset's grain
    and do not extend the set of columns the next ``aggregate`` may
    coarsen to.
    """

    tag: OperatorTag


Step = SourceStep | AggregateStep | MergeStep | EnrichStep | BroadcastStep | SimpleStep


@dataclass(frozen=True, slots=True)
class SimState:
    """First-class symbolic shadow of :class:`CalculationState`.

    Only carries grain-relevant information (no expressions, kinds, or
    provenance). Constructing one outside this module is unusual; use
    :func:`simulate` to build one from a step sequence.
    """

    grain: DimensionSet
    single_valued: frozenset[Identifier] = field(default_factory=frozenset)


_PRESERVING_TAGS: frozenset[OperatorTag] = frozenset(
    {
        OperatorTag.FILTER,
        OperatorTag.PROJECT,
        OperatorTag.ADD_COLUMNS,
        OperatorTag.FILTERING_JOIN,
    }
)


class GrainSimulationError(OSIError):
    """Raised when a step sequence is malformed (e.g. no leading SOURCE).

    Carries :data:`ErrorCode.E_INTERNAL_INVARIANT` because the failure
    means the symbolic simulator was driven with a malformed input —
    the real compiler flows always start with ``source`` and stitch
    operators in valid order. Keeping the simulator inside the typed
    ``OSIError`` hierarchy means the "every failure carries a code"
    invariant the architecture tests rely on holds for this path too.
    """

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.E_INTERNAL_INVARIANT, message)


def simulate(steps: tuple[Step, ...]) -> SimState:
    """Compute the resulting :class:`SimState` of a step sequence.

    Carries both ``grain`` and ``single_valued``. Use this when you
    care about which columns can serve as ``aggregate`` targets;
    use :func:`simulate_grain` when only the grain matters.
    """
    if not steps:
        raise GrainSimulationError("step sequence is empty")
    if not isinstance(steps[0], SourceStep):
        raise GrainSimulationError(
            f"first step must be SOURCE, got {type(steps[0]).__name__}"
        )
    state = SimState(grain=steps[0].primary_key, single_valued=frozenset())
    for step in steps[1:]:
        state = _step(step, state)
    return state


def simulate_grain(steps: tuple[Step, ...]) -> DimensionSet:
    """Compute the resulting grain of a step sequence.

    Backward-compatible wrapper around :func:`simulate` for callers
    that only care about the grain dimension set.
    """
    return simulate(steps).grain


def _step(step: Step, current: SimState) -> SimState:
    if isinstance(step, SourceStep):
        raise GrainSimulationError("SOURCE may only appear as the first step")
    if isinstance(step, AggregateStep):
        # Aggregate can coarsen to any subset of (grain ∪ single_valued).
        permitted = current.grain | current.single_valued
        if not step.target_grain.issubset(permitted):
            raise GrainSimulationError(
                f"aggregate target {sorted(step.target_grain)} is not a "
                f"subset of grain ∪ single_valued {sorted(permitted)}"
            )
        # After aggregation the single-valued extras are gone — anything
        # not in the new grain is no longer addressable.
        return SimState(grain=step.target_grain, single_valued=frozenset())
    if isinstance(step, MergeStep):
        if step.right_grain != current.grain:
            raise GrainSimulationError(
                f"merge grains disagree: left={sorted(current.grain)} "
                f"right={sorted(step.right_grain)}"
            )
        # Merge preserves grain. The right side's single-valued extras
        # would need to be modeled if callers cared; the Foundation
        # leaves merge unchanged here (mirrors §3.7 of the algebra).
        return current
    if isinstance(step, EnrichStep):
        # Enrich preserves grain and adds new single-valued columns.
        return SimState(
            grain=current.grain,
            single_valued=current.single_valued | step.adds,
        )
    if isinstance(step, BroadcastStep):
        return SimState(
            grain=current.grain,
            single_valued=current.single_valued | frozenset({step.adds}),
        )
    # SimpleStep — preservation family only (filter/project/etc).
    if step.tag not in _PRESERVING_TAGS:
        raise GrainSimulationError(
            f"simple step with tag {step.tag!r} is invalid; use a "
            f"dedicated step type for {step.tag.name.lower()}"
        )
    return current


def is_coarser(child: DimensionSet, parent: DimensionSet) -> bool:
    """Return ``True`` iff ``child`` grain is (weakly) coarser than ``parent``.

    ``coarser`` means "fewer dimensions"; by set-subset that is
    ``child ⊆ parent``.
    """
    return child.issubset(parent)


def combine_grains(*grains: frozenset[Identifier]) -> DimensionSet:
    """Union of several grains. Used by the reference interpreter."""
    result: set[Identifier] = set()
    for g in grains:
        result.update(g)
    return frozenset(result)


__all__ = [
    "AggregateStep",
    "BroadcastStep",
    "EnrichStep",
    "GrainSimulationError",
    "MergeStep",
    "OperatorTag",
    "SimState",
    "SimpleStep",
    "SourceStep",
    "Step",
    "combine_grains",
    "is_coarser",
    "simulate",
    "simulate_grain",
]
