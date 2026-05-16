"""Step-builder helpers that construct individual :class:`PlanStep` nodes.

These helpers are the direct bridge between the planner's topology
decisions and the closed algebra operators. Each helper:

* runs the corresponding algebra operator (so every ``E3xxx`` / ``E4xxx``
  safety check fires at plan time, not codegen time), and
* records the resulting state plus a :class:`PlanPayload` on a fresh
  :class:`PlanStep` via the :class:`PlanBuilder` accumulator.

Keeping these in their own module lets :mod:`osi.planning.planner` stay
at the *what-flows-where* level of abstraction, under the 600-LOC cap
mandated by ``ARCHITECTURE.md``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from osi.common.identifiers import Identifier
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.field_deps import field_inter_field_dependencies
from osi.parsing.models import Dataset, Field
from osi.planning.algebra.composition import add_columns
from osi.planning.algebra.joins import filtering_join, merge
from osi.planning.algebra.operations import enrich, filter_, source
from osi.planning.algebra.state import CalculationState, Column, ColumnKind
from osi.planning.classify import RowLevelPredicate, SemiJoinPredicate
from osi.planning.columns import field_to_column
from osi.planning.home_grain import rewrite_field_for_home_grain
from osi.planning.joins import JoinStep
from osi.planning.plan import (
    AddColumnsPayload,
    EnrichDerivedPayload,
    EnrichPayload,
    FilteringJoinPayload,
    FilterPayload,
    MergePayload,
    PlanOperation,
    PlanPayload,
    PlanStep,
    SourcePayload,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedDimension


class PlanBuilder:
    """Accumulates plan steps in topological order.

    Each :meth:`add` call returns the freshly minted step so callers can
    thread it into downstream ``inputs``. The accumulator guarantees
    each step's ``step_id`` equals its position in the final plan.
    """

    def __init__(self) -> None:
        self._steps: list[PlanStep] = []

    def add(
        self,
        operation: PlanOperation,
        *,
        inputs: tuple[int, ...],
        state: CalculationState,
        payload: PlanPayload,
    ) -> PlanStep:
        """Append a new step and return it.

        ``inputs`` is preserved in caller order. Symmetric operators
        (e.g. MERGE) sort their inputs at the call site; asymmetric
        operators (ENRICH, ENRICH_DERIVED, FILTERING_JOIN) carry
        positional meaning — input 0 is parent / left, input 1 is
        child / right — which sorting would silently scramble.
        """
        step_id = len(self._steps)
        step = PlanStep(
            step_id=step_id,
            operation=operation,
            inputs=tuple(inputs),
            state=state,
            payload=payload,
        )
        self._steps.append(step)
        return step

    @property
    def steps(self) -> tuple[PlanStep, ...]:
        """Return the accumulated steps in topological order."""
        return tuple(self._steps)


def _field_to_column_with_home_grain_rewrite(
    field: Field,
    *,
    home: Identifier,
    context: PlannerContext,
    datasets_by_name: dict[Identifier, Dataset],
    sibling_field_names: frozenset[Identifier],
) -> Column:
    """Convert ``field`` to a :class:`Column` after the home-grain rewrite.

    The rewrite handles implicit cross-grain aggregates per D-003 +
    D-015 first, then this helper builds the algebra column.

    Implicit home-grain aggregation is a parser-side concept (the
    field declares its home dataset by where it lives), but the
    rewrite needs the relationship graph to find the correlation
    edge. We do it here, at the SOURCE / ENRICH boundary, so the
    algebra never sees a cross-grain column expression.

    ``sibling_field_names`` is forwarded to :func:`field_to_column`
    so inter-field dependencies are recorded on the resulting
    :class:`Column`. The staging logic in :func:`_emit_dataset` reads
    those dependencies to decide whether a field belongs in the
    SOURCE step's projection or in a downstream ``ADD_COLUMNS``
    stage.
    """
    rewritten_expr = rewrite_field_for_home_grain(
        field,
        home=home,
        graph=context.graph,
        datasets_by_name=datasets_by_name,
    )
    if rewritten_expr is field.expression:
        target = field
    else:
        target = field.model_copy(update={"expression": rewritten_expr})
    return field_to_column(target, sibling_field_names=sibling_field_names)


def fact_dataset(name: Identifier, context: PlannerContext) -> Dataset:
    """Look up a :class:`Dataset` by identifier in the planner context."""
    for ds in context.model.datasets:
        if ds.name == name:
            return ds
    raise OSIPlanningError(
        ErrorCode.E2002_NAME_NOT_FOUND,
        f"dataset {name!r} not declared",
        context={"name": name},
    )


def _topo_levels_by_dependency(
    columns: tuple[Column, ...],
) -> tuple[tuple[Column, ...], ...]:
    """Group columns into topo levels by inter-column dependencies.

    Level 0 contains every column with no dependencies on other
    columns in the input set. Level k+1 contains every column whose
    dependencies are all satisfied by levels 0…k. This is Kahn's
    algorithm with explicit level construction so the planner can
    map level k → one CTE step.

    The order *within* a level is preserved from ``columns`` so the
    SQL output is stable across runs (deterministic for goldens).

    Cycles are not reachable here — :func:`field_inter_field_dependencies`
    is parsed-time-checked by :func:`_check_field_dependency_cycles`
    in :mod:`osi.parsing.foundation`. We still assert acyclicity
    defensively because the planner is not a trust boundary; an
    upstream regression that disabled the parser check would otherwise
    produce a silently wrong plan instead of a loud failure.
    """
    by_name: dict[Identifier, Column] = {col.name: col for col in columns}
    known_names = frozenset(by_name)
    pending = {col.name: col.dependencies & known_names for col in columns}
    levels: list[tuple[Column, ...]] = []
    placed: set[Identifier] = set()
    while pending:
        ready = [name for name, deps in pending.items() if deps.issubset(placed)]
        if not ready:
            raise OSIPlanningError(
                ErrorCode.E_FIELD_DEPENDENCY_CYCLE,
                "internal: unresolved field dependency cycle in planner "
                f"({sorted(str(n) for n in pending)})",
                context={"unresolved": sorted(str(n) for n in pending)},
            )
        ordered_level = tuple(
            by_name[name] for name in columns_order_filter(columns, ready)
        )
        levels.append(ordered_level)
        for name in ready:
            placed.add(name)
            del pending[name]
    return tuple(levels)


def columns_order_filter(
    columns: tuple[Column, ...], names: list[Identifier]
) -> list[Identifier]:
    """Return ``names`` reordered to match the original ``columns`` order.

    Stability of the per-level ordering matters because the SQL
    golden snapshots pin the exact projection order; if Kahn's
    algorithm reordered fields by hash insertion order the goldens
    would churn for non-semantic reasons.
    """
    name_set = set(names)
    return [col.name for col in columns if col.name in name_set]


def _emit_dataset(
    dataset: Dataset, builder: PlanBuilder, context: PlannerContext
) -> PlanStep:
    """Stage ``dataset``'s fields into SOURCE + ADD_COLUMNS levels.

    Strategy
    --------
    1. Build a :class:`Column` for every field (with inter-field
       dependencies recorded — see
       :func:`osi.parsing.field_deps.field_inter_field_dependencies`).
    2. Topologically partition the columns into levels by
       inter-field dependency.
    3. Emit the level-0 columns inside the SOURCE step (these have no
       sibling-field deps and so can be projected directly from the
       physical table).
    4. For each subsequent level, emit one ADD_COLUMNS step that adds
       that level's columns on top of the previous step.

    Returns the final step (either the SOURCE itself when no derived
    fields exist, or the deepest ADD_COLUMNS step).

    Why this matters
    ----------------
    Without staging the planner would inline every derived field's
    expression into a single ``SELECT``, producing
    ``SELECT amount - discount AS net_amount, net_amount * 2 AS net_doubled``.
    That relies on lateral column aliasing within a single ``SELECT``,
    which DuckDB and BigQuery accept but Snowflake, PostgreSQL, and
    SQLite reject. Staging emits one CTE per level so each derived
    field references a *committed* alias from the prior CTE — valid
    on every dialect.
    """
    pk: DimensionSet = frozenset(dataset.primary_key)
    if not pk:
        raise OSIPlanningError(
            ErrorCode.E2007_MISSING_PRIMARY_KEY,
            f"dataset {dataset.name!r} has no primary key; cannot be planned",
            context={"dataset": dataset.name},
        )
    datasets_by_name = {ds.name: ds for ds in context.model.datasets}
    sibling_names = frozenset(f.name for f in dataset.fields)
    columns_in_field_order: list[Column] = []
    for f in dataset.fields:
        columns_in_field_order.append(
            _field_to_column_with_home_grain_rewrite(
                f,
                home=dataset.name,
                context=context,
                datasets_by_name=datasets_by_name,
                sibling_field_names=sibling_names,
            )
        )
    columns_tuple = tuple(columns_in_field_order)
    levels = _topo_levels_by_dependency(columns_tuple)

    base_level = levels[0] if levels else ()
    base_dims = [c for c in base_level if c.kind is ColumnKind.DIMENSION]
    base_facts = [c for c in base_level if c.kind is ColumnKind.FACT]
    # Plumb declared UKs through to the algebra (INFRA.md I-16). The
    # graph layer already accepts UK matches for cardinality inference
    # (parsing/graph.py:_columns_match_any_key); without this line the
    # algebra would only know about the PK and reject N:1 enrichments
    # joined on a UK column with E3011 — see
    # tests/e2e/test_cardinality_safety.py for the regression pin.
    uks = tuple(frozenset(uk) for uk in dataset.unique_keys)
    base_state = source(
        primary_key=pk,
        dimension_columns=base_dims,
        fact_columns=base_facts,
        unique_keys=uks,
    )
    current = builder.add(
        PlanOperation.SOURCE,
        inputs=(),
        state=base_state,
        payload=SourcePayload(
            dataset=dataset.name,
            primary_key=pk,
            source=dataset.source,
        ),
    )
    for derived_level in levels[1:]:
        new_state = add_columns(current.state, derived_level)
        # Once a derived column is materialised in its CTE,
        # downstream operators (AGGREGATE, MERGE, ENRICH) see it as
        # an addressable name, not as an expression with sibling
        # dependencies. The algebra validator (I-6) requires every
        # column's dependencies to be a subset of the surrounding
        # state's column names — left intact, the deps would survive
        # past AGGREGATE (which prunes columns) and trip the
        # validator on the post-aggregate state. Stripping them here
        # is the in-state equivalent of "this column is now a leaf
        # reference, not a derived expression". See
        # tests/unit/planning/test_field_staging.py for the
        # post-aggregate regression pin.
        current = builder.add(
            PlanOperation.ADD_COLUMNS,
            inputs=(current.step_id,),
            state=_materialise_derived(new_state, derived_level),
            payload=AddColumnsPayload(definitions=derived_level),
        )
    return current


def _materialise_derived(
    state: CalculationState, derived: tuple[Column, ...]
) -> CalculationState:
    """Replace each ``derived`` column in ``state`` with a deps-cleared copy.

    Returns a new :class:`CalculationState` where every column listed
    in ``derived`` has its ``dependencies`` field set to the empty
    frozenset. Other state attributes (grain, UKs, provenance) are
    preserved verbatim.

    See :func:`_emit_dataset` for the rationale.
    """
    derived_names = {col.name for col in derived}
    rebuilt: list[Column] = []
    for col in state.columns:
        if col.name in derived_names and col.dependencies:
            rebuilt.append(replace(col, dependencies=frozenset()))
        else:
            rebuilt.append(col)
    return CalculationState(
        grain=state.grain,
        columns=tuple(rebuilt),
        provenance=state.provenance,
        unique_keys=state.unique_keys,
    )


def source_step(
    dataset: Dataset, builder: PlanBuilder, context: PlannerContext
) -> PlanStep:
    """Emit a SOURCE step (plus any staged ADD_COLUMNS) for ``dataset``.

    Returns the *final* step, which is either the SOURCE itself
    (when no field references another field on the same dataset) or
    the deepest ADD_COLUMNS step staged on top of it. Downstream
    callers should use the returned step's ``step_id`` and ``state``
    as the dataset's logical handle without caring how many CTEs
    were emitted underneath.
    """
    return _emit_dataset(dataset, builder, context)


def filter_step(
    parent: PlanStep, predicate: RowLevelPredicate, builder: PlanBuilder
) -> PlanStep:
    """Emit a pre-aggregate FILTER step against ``parent``."""
    return builder.add(
        PlanOperation.FILTER,
        inputs=(parent.step_id,),
        state=filter_(
            parent.state,
            predicate.expression,
            dependencies=predicate.columns,
        ),
        payload=FilterPayload(
            predicate=predicate.expression,
            dependencies=predicate.columns,
            is_post_aggregate=False,
        ),
    )


def _child_has_inter_field_deps(child_ds: Dataset) -> bool:
    """Return True iff any field on ``child_ds`` references another sibling.

    Used by :func:`enrich_step` to decide between the compact inline
    enrich path (no derived columns ⇒ render the child as
    ``JOIN raw_table``) and the staged path (some field references
    another ⇒ stage the child as SOURCE + ADD_COLUMNS and use
    ENRICH_DERIVED so codegen projects the staged columns by name).
    """
    sibling_names = frozenset(f.name for f in child_ds.fields)
    for f in child_ds.fields:
        if field_inter_field_dependencies(f, sibling_names):
            return True
    return False


def enrich_step(
    parent: PlanStep,
    join: JoinStep,
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    """Emit an ENRICH step that pulls ``join.child`` into ``parent``.

    Two emit shapes
    ---------------
    * **Inline enrich** (the common case): when the child dataset has
      no inter-field dependencies every column projects directly off
      the physical table, so we emit a single ENRICH step with
      ``EnrichPayload.child_source`` pointing at the underlying
      table. This is the historical shape and remains the default
      because it minimises CTE count for the typical model.

    * **Staged enrich** (when needed): when at least one child field
      references another field on the same dataset, inlining the
      expressions would force lateral column aliasing within the
      ENRICH ``SELECT`` — non-portable. We instead emit the child
      via :func:`_emit_dataset` (SOURCE + ADD_COLUMNS) and follow it
      with an ENRICH step that reads the staged child as a CTE
      input via :class:`EnrichDerivedPayload`. Codegen for derived
      enrich projects child columns *by name*, never by re-rendering
      the original expressions, so the staging guarantees portable
      SQL on every dialect.

    A child column whose name equals a child-side join key (e.g.
    ``customers.id`` when joining on ``customer_id == id``) and whose
    name also exists on the parent is dropped as redundant; any
    *other* name collision is raised as
    :attr:`ErrorCode.E3005_COLUMN_NAME_COLLISION` by :func:`enrich`.
    """
    child_ds = fact_dataset(join.child, context)
    if _child_has_inter_field_deps(child_ds):
        return _enrich_step_staged(parent, child_ds, join, builder, context)
    return _enrich_step_inline(parent, child_ds, join, builder, context)


def _enrich_step_inline(
    parent: PlanStep,
    child_ds: Dataset,
    join: JoinStep,
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    """Single-step ENRICH where the child renders as ``JOIN raw_table``.

    Used when the child dataset has no inter-field dependencies (so
    every child column is a direct projection over the physical
    table). Codegen will inline each column's expression in the
    enrich ``SELECT`` — safe here because no column references
    another sibling alias.
    """
    child_pk: DimensionSet = frozenset(child_ds.primary_key)
    if not child_pk:
        raise OSIPlanningError(
            ErrorCode.E2007_MISSING_PRIMARY_KEY,
            f"dataset {child_ds.name!r} has no primary key; cannot enrich",
            context={"dataset": child_ds.name},
        )
    child_datasets_by_name = {ds.name: ds for ds in context.model.datasets}
    sibling_names = frozenset(f.name for f in child_ds.fields)
    child_dims: list[Column] = []
    child_facts: list[Column] = []
    for f in child_ds.fields:
        col = _field_to_column_with_home_grain_rewrite(
            f,
            home=child_ds.name,
            context=context,
            datasets_by_name=child_datasets_by_name,
            sibling_field_names=sibling_names,
        )
        if col.kind is ColumnKind.FACT:
            child_facts.append(col)
        else:
            child_dims.append(col)
    child_uks = tuple(frozenset(uk) for uk in child_ds.unique_keys)
    child_state = source(
        primary_key=child_pk,
        dimension_columns=child_dims,
        fact_columns=child_facts,
        unique_keys=child_uks,
    )
    parent_names = parent.state.column_names
    drops = frozenset(k for k in join.child_keys if k in parent_names)
    new_state = enrich(
        parent.state,
        child_state,
        parent_keys=join.parent_keys,
        child_keys=join.child_keys,
        join_type=join.join_type,
        drop_child_columns=drops,
    )
    surfaced_children = tuple(c for c in child_state.columns if c.name not in drops)
    return builder.add(
        PlanOperation.ENRICH,
        inputs=(parent.step_id,),
        state=new_state,
        payload=EnrichPayload(
            child_dataset=join.child,
            child_columns=surfaced_children,
            keys=join.keys,
            join_type=join.join_type,
            child_source=child_ds.source,
            parent_keys=join.parent_keys,
            child_keys=join.child_keys,
        ),
    )


def _enrich_step_staged(
    parent: PlanStep,
    child_ds: Dataset,
    join: JoinStep,
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    """Staged ENRICH where the child is materialised as upstream CTEs.

    Used when the child dataset has at least one field that
    references another sibling field. We emit the child as a
    SOURCE + one or more ADD_COLUMNS steps via :func:`_emit_dataset`
    so each derived field is committed in its own CTE, then
    ENRICH-derived against the parent. Codegen reads child columns
    by name from the staged CTE (see
    :func:`osi.codegen.transpiler._render_enrich_derived`) — never
    by re-rendering the original expressions — so the resulting SQL
    is portable across dialects.
    """
    child_step = _emit_dataset(child_ds, builder, context)
    parent_names = parent.state.column_names
    drops = frozenset(k for k in join.child_keys if k in parent_names)
    new_state = enrich(
        parent.state,
        child_step.state,
        parent_keys=join.parent_keys,
        child_keys=join.child_keys,
        join_type=join.join_type,
        drop_child_columns=drops,
    )
    surfaced_children = tuple(
        c for c in child_step.state.columns if c.name not in drops
    )
    return builder.add(
        PlanOperation.ENRICH,
        inputs=(parent.step_id, child_step.step_id),
        state=new_state,
        payload=EnrichDerivedPayload(
            child_columns=surfaced_children,
            keys=join.keys,
            join_type=join.join_type,
            parent_keys=join.parent_keys,
            child_keys=join.child_keys,
        ),
    )


def semi_join_step(
    parent: PlanStep,
    sj: SemiJoinPredicate,
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    """Emit a FILTERING_JOIN step for an ``EXISTS_IN`` / ``NOT EXISTS_IN``."""
    rhs_datasets = {pair.rhs_dataset for pair in sj.pairs}
    if len(rhs_datasets) != 1:
        raise OSIPlanningError(
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            "EXISTS_IN pairs must all reference the same rhs dataset",
            context={"datasets": sorted(str(d) for d in rhs_datasets)},
        )
    rhs_name = next(iter(rhs_datasets))
    rhs_step = source_step(fact_dataset(rhs_name, context), builder, context)
    lhs_keys = frozenset(p.outer_column for p in sj.pairs)
    rhs_keys = frozenset(p.rhs_column for p in sj.pairs)
    return builder.add(
        PlanOperation.FILTERING_JOIN,
        inputs=(parent.step_id, rhs_step.step_id),
        state=filtering_join(
            parent.state,
            rhs_step.state,
            lhs_keys=lhs_keys,
            rhs_keys=rhs_keys,
            mode=sj.mode,
        ),
        payload=FilteringJoinPayload(
            lhs_keys=lhs_keys,
            rhs_keys=rhs_keys,
            mode=sj.mode,
        ),
    )


def merge_groups(
    groups: Sequence[PlanStep],
    dims: Sequence[ResolvedDimension],
    builder: PlanBuilder,
) -> PlanStep:
    """Chain MERGE steps across measure-group roots at the shared grain.

    Rejects empty input (``E3002``) and any grain mismatch (``E3008``)
    before the algebra can raise. The ``dims`` argument is reserved for
    output ordering; PROJECT surfaces dims explicitly later.
    """
    if not groups:
        raise OSIPlanningError(
            ErrorCode.E3002_UNSATISFIABLE_GRAIN,
            "planner produced no measure groups",
        )
    current = groups[0]
    for right in groups[1:]:
        if current.state.grain != right.state.grain:
            raise OSIPlanningError(
                ErrorCode.E3008_GRAIN_MISMATCH_MERGE,
                "measure groups must reach the same grain before merging",
                context={
                    "left": sorted(str(g) for g in current.state.grain),
                    "right": sorted(str(g) for g in right.state.grain),
                },
            )
        current = builder.add(
            PlanOperation.MERGE,
            inputs=tuple(sorted((current.step_id, right.step_id))),
            state=merge(current.state, right.state, on=current.state.grain),
            payload=MergePayload(on=current.state.grain),
        )
    _ = dims
    return current


__all__ = [
    "PlanBuilder",
    "enrich_step",
    "fact_dataset",
    "filter_step",
    "merge_groups",
    "semi_join_step",
    "source_step",
]
