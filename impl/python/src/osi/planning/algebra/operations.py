"""Five of the nine operators of the closed algebra.

This module hosts ``source``, ``filter_``, ``enrich``, ``aggregate``,
and ``project``. The two grain-matching joins — ``merge`` and
``filtering_join`` — live in :mod:`osi.planning.algebra.joins`; the
two scalar-composition operators — ``add_columns`` and ``broadcast``
— live in :mod:`osi.planning.algebra.composition`. The split keeps
each file inside the 600-line per-file budget (``INFRA.md §1.2``);
all nine operators share the same contract and are re-exported
through :mod:`osi.planning.algebra`.

Every compiler transformation is expressed as a composition of these
nine operators. Adding a tenth is a SPEC change (see
``../../../../docs/JOIN_ALGEBRA.md §3``).

Mutation-score target: **≥ 90%** for this module (``INFRA.md §1.1``). A
surviving mutation here is a P0 bug — it means at least one property or
unit test is weaker than it looks.

Convention: every operator returns a *new* :class:`CalculationState`;
the input is never mutated. Preconditions are checked before any work;
failures raise :class:`AlgebraError` with a specific :class:`ErrorCode`
(``E3xxx`` for shape/grain contract violations, ``E4xxx`` for
algebra-only safety failures — see :class:`AlgebraError`).
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum, auto
from typing import Sequence

from osi.common.identifiers import Identifier
from osi.common.sql_expr import FrozenSQL
from osi.common.types import DimensionSet
from osi.errors import AlgebraError, ErrorCode
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
    Decomposability,
)


class JoinType(StrEnum):
    """Foundation join types for :func:`enrich`.

    Only inner and left outer are supported; right/full live outside the
    Foundation and would need a SPEC update + decision-log entry.
    """

    INNER = auto()
    LEFT = auto()


class FilterMode(StrEnum):
    """Mode for :func:`filtering_join` (semi-join / anti-semi-join).

    Lives next to :class:`JoinType` because both are join-shape enums
    consumed by the same operator family. The actual ``filtering_join``
    operator is defined in :mod:`osi.planning.algebra.joins` to keep
    that file's surface area cohesive (semi-joins, anti-semi-joins,
    and the helpers they share).
    """

    SEMI = auto()
    ANTI = auto()


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


def source(
    primary_key: DimensionSet,
    dimension_columns: Sequence[Column],
    fact_columns: Sequence[Column] = (),
    *,
    unique_keys: Sequence[DimensionSet] = (),
) -> CalculationState:
    """Initialize a state from a dataset's declared columns.

    Parsing is responsible for declaring the primary key and the field
    roles; this operator is the sole entry point into the algebra.

    Preconditions
    -------------
    * ``primary_key`` is non-empty
    * every name in ``primary_key`` is a dimension column
    * all provided columns have kind ``DIMENSION`` or ``FACT`` (not
      ``AGGREGATE`` — aggregates are produced by :func:`aggregate`)
    * provided column names are unique
    * every set in ``unique_keys`` is non-empty and references
      dimension columns (validated by ``CalculationState.__post_init__``
      under I-9). Each UK is an *alternative* minimum key at the
      dataset grain, used by :meth:`CalculationState.is_unique_on`
      when a downstream :func:`enrich` joins on a column that is
      unique-but-not-the-PK. ``Proposed_OSI_Semantics.md §4.2`` and
      ``§6.1`` mandate symmetric treatment of PK and UKs.
    """
    if not primary_key:
        raise AlgebraError(
            ErrorCode.E2007_MISSING_PRIMARY_KEY,
            "source requires a non-empty primary_key",
        )
    columns = tuple(dimension_columns) + tuple(fact_columns)
    names = [c.name for c in columns]
    if len(names) != len(set(names)):
        raise AlgebraError(
            ErrorCode.E3005_COLUMN_NAME_COLLISION,
            "source received duplicate column names",
            context={"columns": names},
        )
    for col in columns:
        if col.kind is ColumnKind.AGGREGATE:
            raise AlgebraError(
                ErrorCode.E4001_EXPLOSION_UNSAFE,
                f"source column {col.name!r} cannot be AGGREGATE",
                context={"column": col.name},
            )
    dim_names = {c.name for c in dimension_columns if c.kind is ColumnKind.DIMENSION}
    missing_pk = primary_key - dim_names
    if missing_pk:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "primary_key references non-dimension columns",
            context={"missing": sorted(missing_pk)},
        )
    # Every column declared on a dataset is trivially single-valued over
    # the dataset's primary key (each row has exactly one value). This
    # flag lets the aggregator later group by any of these columns
    # without re-proving functional dependency.
    tagged = tuple(replace(c, is_single_valued=True) for c in columns)
    return CalculationState(
        grain=primary_key,
        columns=tagged,
        unique_keys=frozenset(frozenset(uk) for uk in unique_keys),
    )


# ---------------------------------------------------------------------------
# filter
# ---------------------------------------------------------------------------


def filter_(
    state: CalculationState,
    predicate: FrozenSQL,
    *,
    dependencies: frozenset[Identifier] = frozenset(),
) -> CalculationState:
    """Validate a row-level predicate against ``state``.

    The Foundation algebra keeps predicates *off* the
    :class:`CalculationState` (``JOIN_ALGEBRA.md §3.2``); a predicate
    is metadata of the enclosing :class:`PlanStep`, not of the
    relational shape. ``filter_`` is therefore intentionally an
    **identity on the state**: it returns ``state`` itself after
    proving that every column the predicate reads is addressable.

    The function is still part of the closed algebra because:

    * its precondition (``dependencies ⊆ state.column_names``) is the
      same kind of safety check the other operators enforce, and
    * having a callable here lets plan composition walk the same
      operator-application protocol for every step (uniformly
      "construct → check → return new state-or-same-state").

    Preconditions
    -------------
    * ``dependencies ⊆ state.column_names``

    Grain effect: **preserved** (filtering removes rows, not dimensions).
    Columns effect: **preserved structurally** (the predicate lives on
    the plan step, not on the returned state).

    Notes
    -----
    Renamed to ``filter_`` because ``filter`` is a Python builtin.
    Re-exported at the package level as ``filter_`` only; users must
    use the module-qualified name.
    """
    unknown = dependencies - state.column_names
    if unknown:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"filter predicate depends on unknown columns: {sorted(unknown)}",
            context={"missing": sorted(unknown)},
        )
    _ = predicate  # retained for caller introspection, not stored
    return state


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------


def enrich(
    parent: CalculationState,
    child: CalculationState,
    *,
    parent_keys: tuple[Identifier, ...],
    child_keys: tuple[Identifier, ...],
    join_type: JoinType,
    drop_child_columns: frozenset[Identifier] = frozenset(),
) -> CalculationState:
    """N:1 join — bring the one-side's columns into the many-side state.

    The contract is symmetric with :func:`merge` and
    :func:`filtering_join`: both sides are full
    :class:`CalculationState` values. The algebra derives fan-out
    safety from grain, *not* from a caller-asserted boolean.

    Preconditions
    -------------
    * ``parent_keys`` and ``child_keys`` have the same arity (they are
      the positional pairing of the equi-join condition)
    * every name in ``parent_keys`` is addressable on ``parent``
    * every name in ``child_keys`` is addressable on ``child``
    * ``child.is_unique_on(child_keys)`` — i.e. ``child`` is *unique
      on the join keys*. This is the **fan-trap rule**: if ``child``
      can have multiple rows per join key, joining duplicates
      ``parent`` rows and silently destroys ``parent.grain``. The
      check delegates to
      :meth:`CalculationState.is_unique_on`, which accepts any
      ``child_keys`` set that is a superset of either the child's
      grain or any declared :attr:`unique_key`.       ``Proposed_OSI_Semantics.md
      §6.1`` mandates this symmetric treatment so authors can recover
      from a wider-than-necessary PK declaration with an explicit UK
      on the join column. Failures raise
      :attr:`ErrorCode.E3011_MN_AGGREGATION_REJECTED` — the algebra
      precondition signal for any fan-out edge. Declared N:N edges
      are normally rejected earlier in
      :func:`osi.planning.joins.find_enrichment_path` with
      :attr:`ErrorCode.E3012_MN_NO_SAFE_REWRITE` or
      :attr:`ErrorCode.E3013_NO_STITCHING_DIMENSION`; the planner
      translates this E3011 precondition signal into the user-facing
      :attr:`ErrorCode.E_UNSAFE_REAGGREGATION` (per Appendix C / D-022)
      for 1:N fan-trap cases that reach the algebra layer.
    * no child column (after the optional ``drop_child_columns``
      reduction) collides with a parent column
    * no child column is an aggregate (aggregates are built by
      :func:`aggregate` after the join, not before)

    ``drop_child_columns`` lets the planner skip child columns that are
    redundant — typically the child-side join keys when they share a
    name with the parent-side keys. The Foundation algebra does not
    rename: collisions outside this drop set surface as
    :attr:`ErrorCode.E3005_COLUMN_NAME_COLLISION`.

    Grain effect: **preserved** (``parent.grain``).
    """
    if len(parent_keys) != len(child_keys):
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "enrich requires parent_keys and child_keys to have the same arity",
            context={
                "parent_keys": list(parent_keys),
                "child_keys": list(child_keys),
            },
        )
    if not parent_keys:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "enrich requires at least one join key pair",
        )
    missing_parent = frozenset(parent_keys) - parent.column_names
    if missing_parent:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "enrich parent_keys are not addressable columns on parent",
            context={
                "missing": sorted(missing_parent),
                "parent_columns": sorted(parent.column_names),
            },
        )
    missing_child = frozenset(child_keys) - child.column_names
    if missing_child:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "enrich child_keys are not addressable columns on child",
            context={
                "missing": sorted(missing_child),
                "child_columns": sorted(child.column_names),
            },
        )
    # Fan-trap rule: child must be unique on the join keys. Accepts
    # either the grain (PK) OR any declared UK as proof of uniqueness
    # — see CalculationState.is_unique_on for the symmetry rationale.
    child_key_set = frozenset(child_keys)
    if not child.is_unique_on(child_key_set):
        raise AlgebraError(
            ErrorCode.E3011_MN_AGGREGATION_REJECTED,
            "enrich would fan out: child is not unique on the join keys "
            f"(child.grain={sorted(child.grain)}, "
            f"child.unique_keys={sorted(map(sorted, child.unique_keys))}, "
            f"child_keys={sorted(child_key_set)}). "
            "Aggregate the child to the join key before enriching, declare "
            "a unique_key covering the join column, or use the relationship "
            "in the opposite direction.",
            context={
                "child_grain": sorted(child.grain),
                "child_unique_keys": sorted(map(sorted, child.unique_keys)),
                "child_keys": sorted(child_key_set),
            },
        )
    # AGGREGATE columns from the child are surfaced as scalar values on
    # the parent only when the join cannot fan out the parent — i.e.
    # ``child`` is unique on ``child_keys``. The fan-trap check above
    # already guarantees that condition. The result column is reclassified
    # as ``FACT`` because the aggregation has been discharged at the
    # child's grain; downstream aggregates can re-aggregate it explicitly
    # (the bridge-resolution mid-pipeline plan, ``§6.5.1``, depends on
    # this). Without the join uniqueness, the original rejection still
    # applies — see the fan-trap check above which raises before we
    # reach this point.
    incoming_raw = tuple(c for c in child.columns if c.name not in drop_child_columns)
    incoming = tuple(
        (
            replace(
                c,
                kind=ColumnKind.FACT,
                aggregate=None,
                dependencies=frozenset(),
                is_single_valued=True,
            )
            if c.kind is ColumnKind.AGGREGATE
            else c
        )
        for c in incoming_raw
    )
    parent_names = parent.column_names
    overlap = {c.name for c in incoming} & parent_names
    if overlap:
        raise AlgebraError(
            ErrorCode.E3005_COLUMN_NAME_COLLISION,
            f"enrich child columns collide with parent: {sorted(overlap)}. "
            "Rename the colliding fields in the model, or drop them from the "
            "child via drop_child_columns when bringing them in.",
            context={"columns": sorted(overlap)},
        )
    _ = join_type  # recorded by the plan step; here for type clarity
    tagged_children = tuple(
        replace(col, from_join_rhs=True, is_single_valued=True) for col in incoming
    )
    return CalculationState(
        grain=parent.grain,
        columns=parent.columns + tagged_children,
        provenance=parent.provenance | child.provenance,
        # Grain preserved → parent's UKs still hold. The child's UKs
        # describe the child's grain, which is gone from the post-enrich
        # state, so we drop them.
        unique_keys=parent.unique_keys,
    )


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def aggregate(
    state: CalculationState,
    new_grain: DimensionSet,
    aggregations: Sequence[Column],
) -> CalculationState:
    """Reduce to a coarser grain, emitting one aggregate column per aggregation.

    Preconditions
    -------------
    * every ``new_grain`` name is a ``DIMENSION`` column of ``state``
    * every ``new_grain`` column is **either** a member of
      ``state.grain`` **or** single-valued over it (introduced by
      :func:`enrich` on the one-side, or by :func:`broadcast`). This is
      the spec's "grain coarsening" rule generalised to handle the
      common case of aggregating by a dimension brought in through an
      N:1 join (see the planner pseudocode in ``JOIN_ALGEBRA.md §7``).
    * every aggregation column has kind ``AGGREGATE`` with populated
      ``AggregateInfo``
    * every aggregation's dependency set is a subset of ``state.column_names``
    * if an aggregation reads a ``from_join_rhs`` column, it is not a
      ``HOLISTIC`` aggregate (``COUNT DISTINCT`` etc.) — such
      aggregations must run at the finer grain first and be merged
      afterwards (``E4001 EXPLOSION_UNSAFE``)
    """
    dimension_names = {c.name for c in state.columns if c.kind is ColumnKind.DIMENSION}
    unknown = new_grain - state.column_names
    if unknown:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            "aggregate new_grain references unknown columns",
            context={"new_grain": sorted(new_grain), "missing": sorted(unknown)},
        )
    non_dim = new_grain - dimension_names
    if non_dim:
        raise AlgebraError(
            ErrorCode.E3004_GRAIN_NOT_SUBSET,
            "aggregate new_grain includes non-dimension columns",
            context={"non_dimension": sorted(non_dim)},
        )
    # Every new_grain member must be single-valued over ``state.grain``.
    # Membership in ``state.grain`` counts trivially; otherwise the column
    # must have been tagged ``is_single_valued`` when introduced (source
    # dimensions, enrich RHS columns, broadcast scalars).
    for name in new_grain:
        col = state.column(name)
        if name not in state.grain and not col.is_single_valued:
            raise AlgebraError(
                ErrorCode.E3004_GRAIN_NOT_SUBSET,
                f"aggregate cannot group by {name!r}: not in state.grain and "
                "not known to be single-valued over it",
                context={
                    "column": name,
                    "state_grain": sorted(state.grain),
                },
            )
    for agg in aggregations:
        if agg.kind is not ColumnKind.AGGREGATE or agg.aggregate is None:
            raise AlgebraError(
                ErrorCode.E3007_AGGREGATE_IN_SCALAR_CONTEXT,
                f"aggregate received non-AGGREGATE column {agg.name!r}",
                context={"column": agg.name, "kind": agg.kind},
            )
        unknown = agg.dependencies - state.column_names
        if unknown:
            raise AlgebraError(
                ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                f"aggregation {agg.name!r} depends on unknown columns: "
                f"{sorted(unknown)}",
                context={"column": agg.name, "missing": sorted(unknown)},
            )
        if agg.aggregate.function.decomposability is Decomposability.HOLISTIC:
            for dep in agg.dependencies:
                src = state.column(dep)
                if src.from_join_rhs:
                    raise AlgebraError(
                        ErrorCode.E4001_EXPLOSION_UNSAFE,
                        f"holistic aggregation {agg.name!r} reads "
                        f"join-RHS column {dep!r}; pre-aggregate first",
                        context={"column": agg.name, "source": dep},
                    )

    dim_by_name = {c.name: c for c in state.columns if c.kind is ColumnKind.DIMENSION}
    kept_dims = tuple(dim_by_name[name] for name in sorted(new_grain))
    agg_names = {a.name for a in aggregations}
    overlap = agg_names & new_grain
    if overlap:
        raise AlgebraError(
            ErrorCode.E3005_COLUMN_NAME_COLLISION,
            f"aggregation names collide with grain dimensions: {sorted(overlap)}",
            context={"columns": sorted(overlap)},
        )
    # After aggregation, each output aggregate column is a fresh scalar
    # at ``new_grain``. Its input-side dependencies are no longer
    # addressable in the output state, so strip them to satisfy I-3
    # (every dep must resolve in the current state). Mark it
    # single-valued: an aggregate is by definition one value per grain
    # key.
    sealed_aggregations = tuple(
        replace(a, dependencies=frozenset(), is_single_valued=True)
        for a in aggregations
    )
    return CalculationState(
        grain=new_grain,
        columns=kept_dims + sealed_aggregations,
        provenance=state.provenance,
        # A UK that is a subset of new_grain remains an alternative
        # minimum key after aggregation (each new-grain row contains
        # exactly one UK value, and distinct UK values stayed distinct
        # because the UK was distinct at the old grain). UKs that
        # straddle out of new_grain are dropped — proving they remain
        # unique would require re-deriving functional dependencies the
        # algebra does not track.
        unique_keys=frozenset(uk for uk in state.unique_keys if uk.issubset(new_grain)),
    )


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


def project(state: CalculationState, columns: Sequence[Identifier]) -> CalculationState:
    """Keep only ``columns``, in the order given.

    Preconditions
    -------------
    * ``columns ⊆ state.column_names``
    * ``state.grain ⊆ columns`` (dropping grain dimensions is forbidden —
      that would violate I-1)
    """
    requested = tuple(columns)
    requested_set = set(requested)
    unknown = requested_set - state.column_names
    if unknown:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"project references unknown columns: {sorted(unknown)}",
            context={"missing": sorted(unknown)},
        )
    missing_grain = state.grain - requested_set
    if missing_grain:
        raise AlgebraError(
            ErrorCode.E3004_GRAIN_NOT_SUBSET,
            f"project would drop grain dimensions: {sorted(missing_grain)}",
            context={"missing": sorted(missing_grain)},
        )
    if len(requested_set) != len(requested):
        raise AlgebraError(
            ErrorCode.E3005_COLUMN_NAME_COLLISION,
            "project received duplicate column names",
            context={"columns": list(requested)},
        )
    by_name = {c.name: c for c in state.columns}
    retained_names = frozenset(requested)
    # After projection, retained columns stand alone: any
    # ``dependencies`` that were pruned by the project become
    # unresolvable in the output state (they name columns no longer
    # present). Stripping them is semantically safe — a post-project
    # column is materialised and no longer a lazy view over its
    # inputs, same way AGGREGATE seals its outputs. Preserving deps
    # would force every callers to also retain every transitive input,
    # defeating the point of PROJECT.
    retained_columns = tuple(
        replace(
            by_name[n],
            dependencies=by_name[n].dependencies & retained_names,
        )
        for n in requested
    )
    return CalculationState(
        grain=state.grain,
        columns=retained_columns,
        provenance=state.provenance,
        # Grain preserved (project may not drop grain dims), so any UK
        # whose columns survived projection is still a valid key.
        unique_keys=frozenset(
            uk for uk in state.unique_keys if uk.issubset(retained_names)
        ),
    )


__all__ = [
    "AggregateFunction",
    "AggregateInfo",
    "FilterMode",
    "JoinType",
    "aggregate",
    "enrich",
    "filter_",
    "project",
    "source",
]
