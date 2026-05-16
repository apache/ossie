"""Unit tests for each of the nine algebra operators.

One test class per operator. Each class covers:

1. The happy path: preconditions met, expected grain/columns returned.
2. Every precondition violation listed in ``JOIN_ALGEBRA.md §3``,
   asserting on a specific ``ErrorCode``.

Property-based invariants (totality, purity, determinism, ...) live in
``tests/properties/test_algebra_*.py``.
"""

from __future__ import annotations

import pytest
import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra import (
    AggregateFunction,
    CalculationState,
    Column,
    ColumnKind,
    FilterMode,
    JoinType,
    add_columns,
    aggregate,
    broadcast,
    enrich,
    filter_,
    filtering_join,
    merge,
    project,
    source,
)
from tests.properties.strategies import aggregate_column, dimension_column, fact_column


def I(s: str) -> str:  # noqa: E743  (helper for readable tests)
    return normalize_identifier(s)


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


class TestSource:
    def test_initial_grain_is_primary_key(self):
        state = source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[dimension_column(I("order_id"))],
            fact_columns=[fact_column(I("amount"))],
        )
        assert state.grain == frozenset({I("order_id")})
        assert state.column_names == {I("order_id"), I("amount")}

    def test_empty_primary_key_rejected(self):
        with pytest.raises(OSIError) as exc:
            source(
                primary_key=frozenset(),
                dimension_columns=[dimension_column(I("a"))],
            )
        assert exc.value.code == ErrorCode.E2007_MISSING_PRIMARY_KEY

    def test_duplicate_column_names_rejected(self):
        with pytest.raises(OSIError) as exc:
            source(
                primary_key=frozenset({I("a")}),
                dimension_columns=[dimension_column(I("a")), dimension_column(I("a"))],
            )
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION

    def test_primary_key_not_in_dimensions_rejected(self):
        with pytest.raises(OSIError) as exc:
            source(
                primary_key=frozenset({I("missing")}),
                dimension_columns=[dimension_column(I("a"))],
            )
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_aggregate_columns_rejected_at_source(self):
        with pytest.raises(OSIError) as exc:
            source(
                primary_key=frozenset({I("a")}),
                dimension_columns=[dimension_column(I("a"))],
                fact_columns=[aggregate_column(I("total"), over=I("a"))],
            )
        assert exc.value.code == ErrorCode.E4001_EXPLOSION_UNSAFE


# ---------------------------------------------------------------------------
# filter_
# ---------------------------------------------------------------------------


class TestFilter:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("a")}),
            dimension_columns=[dimension_column(I("a"))],
            fact_columns=[fact_column(I("x"))],
        )

    def test_preserves_state_structurally(self):
        state = self._base()
        pred = FrozenSQL.of(sqlglot.parse_one("x > 0"))
        out = filter_(state, pred, dependencies=frozenset({I("x")}))
        assert out.grain == state.grain
        assert out.column_names == state.column_names

    def test_unknown_dependency_rejected(self):
        state = self._base()
        pred = FrozenSQL.of(sqlglot.parse_one("missing > 0"))
        with pytest.raises(OSIError) as exc:
            filter_(state, pred, dependencies=frozenset({I("missing")}))
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------


class TestEnrich:
    def _parent(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("customer_id")),
            ],
        )

    def _child(self, *, extra: list[Column] | None = None) -> CalculationState:
        cols = [dimension_column(I("id"))]
        if extra:
            cols.extend(extra)
        return source(
            primary_key=frozenset({I("id")}),
            dimension_columns=cols,
        )

    def test_appends_child_columns_with_rhs_flag(self):
        parent = self._parent()
        child = self._child(extra=[dimension_column(I("customer_name"))])
        out = enrich(
            parent,
            child,
            parent_keys=(I("customer_id"),),
            child_keys=(I("id"),),
            join_type=JoinType.INNER,
        )
        assert out.grain == parent.grain
        new_col = out.column(I("customer_name"))
        assert new_col.from_join_rhs is True
        assert new_col.is_single_valued is True

    def test_fan_trap_rejected_when_child_grain_not_in_keys(self):
        # Build a child whose grain is {a, b} but join keys are only {a};
        # joining replicates parent rows (1->N).
        child = source(
            primary_key=frozenset({I("a"), I("b")}),
            dimension_columns=[dimension_column(I("a")), dimension_column(I("b"))],
        )
        parent = self._parent()
        with pytest.raises(OSIError) as exc:
            enrich(
                parent,
                child,
                parent_keys=(I("customer_id"),),
                child_keys=(I("a"),),
                join_type=JoinType.INNER,
            )
        assert exc.value.code == ErrorCode.E3011_MN_AGGREGATION_REJECTED

    def test_keys_not_in_parent_columns_rejected(self):
        parent = self._parent()
        with pytest.raises(OSIError) as exc:
            enrich(
                parent,
                self._child(),
                parent_keys=(I("bogus"),),
                child_keys=(I("id"),),
                join_type=JoinType.INNER,
            )
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_child_column_name_collision_rejected(self):
        parent = self._parent()
        # Child has a column named ``customer_id`` that collides with parent.
        child = source(
            primary_key=frozenset({I("id")}),
            dimension_columns=[
                dimension_column(I("id")),
                dimension_column(I("customer_id")),
            ],
        )
        with pytest.raises(OSIError) as exc:
            enrich(
                parent,
                child,
                parent_keys=(I("customer_id"),),
                child_keys=(I("id"),),
                join_type=JoinType.INNER,
            )
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION

    def test_aggregate_child_column_reclassified_when_child_unique(self):
        """Pre-aggregated child surfaced as FACT through enrich (§6.5.1).

        When the child is unique on ``child_keys`` (its grain is a
        subset of the keys, or a UK is) each parent row joins to at
        most one child row, so an AGGREGATE column on the child can
        safely surface as a row-value FACT column on the result.
        Without this relaxation the bridge-resolution mid-pipeline
        plan in :mod:`osi.planning.planner_bridge` cannot run.
        """
        from dataclasses import replace as dc_replace

        parent = self._parent()
        agg_col = aggregate_column(I("count"), over=I("id"))
        id_col = dc_replace(dimension_column(I("id")), is_single_valued=True)
        child_with_agg = CalculationState(
            grain=frozenset({I("id")}),
            columns=(id_col, agg_col),
        )
        result = enrich(
            parent,
            child_with_agg,
            parent_keys=(I("customer_id"),),
            child_keys=(I("id"),),
            join_type=JoinType.INNER,
        )
        # The COUNT column is preserved by name but is now a FACT
        # column at the parent's grain — its aggregate metadata has
        # been discharged and downstream aggregates may re-aggregate
        # it explicitly.
        count_out = result.column(I("count"))
        assert count_out.kind is ColumnKind.FACT
        assert count_out.aggregate is None
        assert count_out.is_single_valued
        assert count_out.from_join_rhs

    def test_aggregate_child_column_rejected_when_child_fans_out(self):
        """The fan-trap check still fires when child isn't unique.

        Surfacing an AGGREGATE column through a fan-out join would
        be unsafe (the value would be repeated across parent rows
        with no obvious re-aggregation path), so the algebra still
        refuses with ``E3011_MN_AGGREGATION_REJECTED``.
        """
        from dataclasses import replace as dc_replace

        parent = self._parent()
        agg_col = aggregate_column(I("count"), over=I("id"))
        id_col = dc_replace(dimension_column(I("id")), is_single_valued=True)
        other_col = dimension_column(I("other"))
        # Child has a richer grain than child_keys → child is NOT
        # unique on the requested join keys.
        child_with_agg = CalculationState(
            grain=frozenset({I("id"), I("other")}),
            columns=(id_col, other_col, agg_col),
        )
        with pytest.raises(OSIError) as exc:
            enrich(
                parent,
                child_with_agg,
                parent_keys=(I("customer_id"),),
                child_keys=(I("id"),),
                join_type=JoinType.INNER,
            )
        assert exc.value.code == ErrorCode.E3011_MN_AGGREGATION_REJECTED


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


class TestAggregate:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("region")),
            ],
            fact_columns=[fact_column(I("amount"))],
        )

    def test_happy_path_groups_to_new_grain(self):
        state = self._base()
        out = aggregate(
            state,
            frozenset({I("region")}),
            [aggregate_column(I("total"), over=I("amount"))],
        )
        assert out.grain == frozenset({I("region")})
        assert out.column_names == {I("region"), I("total")}

    def test_new_grain_on_fact_column_rejected(self):
        state = self._base()
        # `amount` is a FACT column, not a dimension — illegal as grain.
        with pytest.raises(OSIError) as exc:
            aggregate(
                state,
                frozenset({I("amount")}),
                [aggregate_column(I("total"), over=I("amount"))],
            )
        assert exc.value.code == ErrorCode.E3004_GRAIN_NOT_SUBSET

    def test_new_grain_unknown_column_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            aggregate(
                state,
                frozenset({I("totally_bogus")}),
                [aggregate_column(I("total"), over=I("amount"))],
            )
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_same_grain_is_allowed(self):
        state = self._base()
        out = aggregate(
            state,
            state.grain,
            [aggregate_column(I("total"), over=I("amount"))],
        )
        assert out.grain == state.grain

    def test_non_aggregate_column_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            aggregate(
                state,
                frozenset({I("region")}),
                [fact_column(I("bogus"))],
            )
        assert exc.value.code == ErrorCode.E3007_AGGREGATE_IN_SCALAR_CONTEXT

    def test_unknown_dependency_rejected(self):
        state = self._base()
        bad = Column(
            name=I("total"),
            expression=FrozenSQL.of(sqlglot.parse_one("SUM(missing)")),
            dependencies=frozenset({I("missing")}),
            kind=ColumnKind.AGGREGATE,
            aggregate=aggregate_column(I("total"), over=I("amount")).aggregate,
        )
        with pytest.raises(OSIError) as exc:
            aggregate(state, frozenset({I("region")}), [bad])
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_holistic_over_join_rhs_rejected(self):
        parent = self._base()
        child = source(
            primary_key=frozenset({I("region")}),
            dimension_columns=[dimension_column(I("region"))],
            fact_columns=[fact_column(I("tax"))],
        )
        enriched = enrich(
            parent,
            child,
            parent_keys=(I("region"),),
            child_keys=(I("region"),),
            join_type=JoinType.INNER,
            drop_child_columns=frozenset({I("region")}),
        )
        holistic = aggregate_column(
            I("uniq_tax"),
            function=AggregateFunction.COUNT_DISTINCT,
            over=I("tax"),
        )
        with pytest.raises(OSIError) as exc:
            aggregate(enriched, frozenset({I("region")}), [holistic])
        assert exc.value.code == ErrorCode.E4001_EXPLOSION_UNSAFE

    def test_agg_name_collides_with_grain_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            aggregate(
                state,
                frozenset({I("region")}),
                [aggregate_column(I("region"), over=I("amount"))],
            )
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


class TestProject:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("a")}),
            dimension_columns=[dimension_column(I("a")), dimension_column(I("b"))],
            fact_columns=[fact_column(I("x"))],
        )

    def test_happy_path(self):
        state = self._base()
        out = project(state, [I("a"), I("x")])
        assert [c.name for c in out.columns] == [I("a"), I("x")]
        assert out.grain == frozenset({I("a")})

    def test_unknown_column_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            project(state, [I("missing")])
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_dropping_grain_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            project(state, [I("x")])
        assert exc.value.code == ErrorCode.E3004_GRAIN_NOT_SUBSET

    def test_duplicate_columns_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            project(state, [I("a"), I("a")])
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION


# ---------------------------------------------------------------------------
# add_columns
# ---------------------------------------------------------------------------


class TestAddColumns:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("a")}),
            dimension_columns=[dimension_column(I("a"))],
            fact_columns=[fact_column(I("x"))],
        )

    def test_appends_derived_column(self):
        state = self._base()
        derived = Column(
            name=I("doubled"),
            expression=FrozenSQL.of(sqlglot.parse_one("x * 2")),
            dependencies=frozenset({I("x")}),
            kind=ColumnKind.FACT,
        )
        out = add_columns(state, [derived])
        assert out.column_names == state.column_names | {I("doubled")}
        assert out.grain == state.grain

    def test_aggregate_column_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            add_columns(state, [aggregate_column(I("total"), over=I("x"))])
        assert exc.value.code == ErrorCode.E3007_AGGREGATE_IN_SCALAR_CONTEXT

    def test_unknown_dependency_rejected(self):
        state = self._base()
        bad = Column(
            name=I("oops"),
            expression=FrozenSQL.of(sqlglot.parse_one("missing + 1")),
            dependencies=frozenset({I("missing")}),
            kind=ColumnKind.FACT,
        )
        with pytest.raises(OSIError) as exc:
            add_columns(state, [bad])
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_name_collision_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            add_columns(state, [fact_column(I("x"))])
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION

    def test_new_column_can_depend_on_earlier_new_column(self):
        state = self._base()
        first = Column(
            name=I("doubled"),
            expression=FrozenSQL.of(sqlglot.parse_one("x * 2")),
            dependencies=frozenset({I("x")}),
            kind=ColumnKind.FACT,
        )
        second = Column(
            name=I("quadrupled"),
            expression=FrozenSQL.of(sqlglot.parse_one("doubled * 2")),
            dependencies=frozenset({I("doubled")}),
            kind=ColumnKind.FACT,
        )
        out = add_columns(state, [first, second])
        assert I("quadrupled") in out.column_names


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


class TestMerge:
    def _base(self, facts: list[str]) -> CalculationState:
        state = source(
            primary_key=frozenset({I("region")}),
            dimension_columns=[dimension_column(I("region"))],
            fact_columns=[fact_column(I(n)) for n in facts],
        )
        return state

    def test_same_grain_disjoint_columns_merged(self):
        left = self._base(["sales"])
        right = self._base(["returns"])
        out = merge(left, right)
        assert out.grain == left.grain
        assert {c.name for c in out.columns} == {I("region"), I("sales"), I("returns")}

    def test_grain_mismatch_rejected(self):
        left = self._base(["sales"])
        right = source(
            primary_key=frozenset({I("store")}),
            dimension_columns=[dimension_column(I("store"))],
        )
        with pytest.raises(OSIError) as exc:
            merge(left, right)
        assert exc.value.code == ErrorCode.E3008_GRAIN_MISMATCH_MERGE

    def test_non_grain_column_overlap_rejected(self):
        left = self._base(["sales"])
        right = self._base(["sales"])
        with pytest.raises(OSIError) as exc:
            merge(left, right)
        assert exc.value.code == ErrorCode.E4003_MERGE_COLUMN_OVERLAP

    def test_on_must_match_shared_grain(self):
        left = self._base(["sales"])
        right = self._base(["returns"])
        with pytest.raises(OSIError) as exc:
            merge(left, right, on=frozenset({I("store")}))
        assert exc.value.code == ErrorCode.E3008_GRAIN_MISMATCH_MERGE


# ---------------------------------------------------------------------------
# filtering_join
# ---------------------------------------------------------------------------


class TestFilteringJoin:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("customer_id")),
            ],
        )

    def _rhs(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("customer_id")}),
            dimension_columns=[dimension_column(I("customer_id"))],
        )

    def test_semi_join_preserves_state_shape(self):
        state = self._base()
        rhs = self._rhs()
        out = filtering_join(
            state,
            rhs,
            lhs_keys=frozenset({I("customer_id")}),
            rhs_keys=frozenset({I("customer_id")}),
            mode=FilterMode.SEMI,
        )
        assert out.column_names == state.column_names
        assert out.grain == state.grain

    def test_anti_mode_accepted(self):
        state = self._base()
        rhs = self._rhs()
        out = filtering_join(
            state,
            rhs,
            lhs_keys=frozenset({I("customer_id")}),
            rhs_keys=frozenset({I("customer_id")}),
            mode=FilterMode.ANTI,
        )
        assert out.column_names == state.column_names

    def test_key_arity_mismatch_rejected(self):
        state = self._base()
        rhs = self._rhs()
        with pytest.raises(OSIError) as exc:
            filtering_join(
                state,
                rhs,
                lhs_keys=frozenset({I("order_id"), I("customer_id")}),
                rhs_keys=frozenset({I("customer_id")}),
                mode=FilterMode.SEMI,
            )
        assert exc.value.code == ErrorCode.E4005_FILTERING_JOIN_ADDS_COLUMNS

    def test_lhs_key_missing_rejected(self):
        state = self._base()
        rhs = self._rhs()
        with pytest.raises(OSIError) as exc:
            filtering_join(
                state,
                rhs,
                lhs_keys=frozenset({I("bogus")}),
                rhs_keys=frozenset({I("customer_id")}),
                mode=FilterMode.SEMI,
            )
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_rhs_key_missing_rejected(self):
        state = self._base()
        rhs = self._rhs()
        with pytest.raises(OSIError) as exc:
            filtering_join(
                state,
                rhs,
                lhs_keys=frozenset({I("customer_id")}),
                rhs_keys=frozenset({I("bogus")}),
                mode=FilterMode.SEMI,
            )
        assert exc.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    def _base(self) -> CalculationState:
        return source(
            primary_key=frozenset({I("a")}),
            dimension_columns=[dimension_column(I("a"))],
        )

    def _scalar(self, name: str = "total") -> CalculationState:
        from osi.planning.algebra.state import Column  # local to avoid unused

        scalar_col = Column(
            name=I(name),
            expression=FrozenSQL.of(sqlglot.parse_one("42")),
            dependencies=frozenset(),
            kind=ColumnKind.FACT,
        )
        return CalculationState(grain=frozenset(), columns=(scalar_col,))

    def test_happy_path(self):
        state = self._base()
        scalar = self._scalar()
        out = broadcast(state, scalar)
        assert out.grain == state.grain
        assert out.column(I("total")).is_single_valued is True

    def test_non_scalar_rejected(self):
        state = self._base()
        with pytest.raises(OSIError) as exc:
            broadcast(state, state)
        assert exc.value.code == ErrorCode.E4004_BROADCAST_NOT_SCALAR

    def test_multi_column_scalar_rejected(self):
        state = self._base()
        cols = (
            Column(
                name=I("x"),
                expression=FrozenSQL.of(sqlglot.parse_one("1")),
                dependencies=frozenset(),
                kind=ColumnKind.FACT,
            ),
            Column(
                name=I("y"),
                expression=FrozenSQL.of(sqlglot.parse_one("2")),
                dependencies=frozenset(),
                kind=ColumnKind.FACT,
            ),
        )
        bad_scalar = CalculationState(grain=frozenset(), columns=cols)
        with pytest.raises(OSIError) as exc:
            broadcast(state, bad_scalar)
        assert exc.value.code == ErrorCode.E4004_BROADCAST_NOT_SCALAR

    def test_name_collision_rejected(self):
        state = self._base()
        scalar = self._scalar(name="a")
        with pytest.raises(OSIError) as exc:
            broadcast(state, scalar)
        assert exc.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION


# ---------------------------------------------------------------------------
# unique_keys propagation across operators (INFRA.md I-16)
# ---------------------------------------------------------------------------


class TestUniqueKeysPropagation:
    """Pin per-operator UK behaviour.

    The algebra is the load-bearing module (``INFRA.md §1.1.1``);
    every operator's UK transformation needs an explicit unit test.
    These tests cover the propagation rules documented in each
    operator's docstring:

    * ``source``      → declared UKs land on the state.
    * ``filter_``     → identity on the state, including UKs.
    * ``enrich``      → parent's UKs preserved; child's UKs dropped.
    * ``aggregate``   → keep UKs that are subsets of ``new_grain``.
    * ``project``     → keep UKs that are subsets of retained columns.
    * ``add_columns`` → preserved (only adds columns).
    * ``broadcast``   → preserved (only adds a scalar column).
    * ``merge``       → intersect (only UKs holding on both sides).
    * ``filtering_join`` → identity on the state, including UKs.
    """

    def _customers_with_uk(self) -> CalculationState:
        """Mismarked-PK + UK customers state — the I-16 acceptance shape."""
        return source(
            primary_key=frozenset({I("id"), I("region")}),
            dimension_columns=[
                dimension_column(I("id")),
                dimension_column(I("region")),
            ],
            unique_keys=[frozenset({I("id")})],
        )

    def test_source_records_declared_uks(self):
        state = self._customers_with_uk()
        assert frozenset({I("id")}) in state.unique_keys
        assert state.grain == frozenset({I("id"), I("region")})

    def test_source_with_no_uks_has_empty_uk_set(self):
        state = source(
            primary_key=frozenset({I("id")}),
            dimension_columns=[dimension_column(I("id"))],
        )
        assert state.unique_keys == frozenset()

    def test_enrich_admits_uk_match_as_proof_of_uniqueness(self):
        """The acceptance test at the algebra layer.

        Without UK awareness, enriching a (PK={id, region}) child on
        ``[id]`` would raise ``E3011`` — that's exactly the bug the
        sprint fixed. Now the UK ``[id]`` discharges the fan-trap rule.
        """
        parent = source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("customer_id")),
            ],
        )
        child = self._customers_with_uk()
        out = enrich(
            parent,
            child,
            parent_keys=(I("customer_id"),),
            child_keys=(I("id"),),
            join_type=JoinType.INNER,
        )
        assert out.grain == parent.grain
        assert out.column(I("region")).from_join_rhs is True

    def test_enrich_still_rejects_when_no_pk_or_uk_covers_join_keys(self):
        parent = source(
            primary_key=frozenset({I("order_id")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("customer_id")),
            ],
        )
        # Child has neither a PK nor a UK matching [id] — only [other].
        child = source(
            primary_key=frozenset({I("id"), I("region")}),
            dimension_columns=[
                dimension_column(I("id")),
                dimension_column(I("region")),
                dimension_column(I("other")),
            ],
            unique_keys=[frozenset({I("other")})],
        )
        with pytest.raises(OSIError) as exc:
            enrich(
                parent,
                child,
                parent_keys=(I("customer_id"),),
                child_keys=(I("id"),),
                join_type=JoinType.INNER,
            )
        assert exc.value.code == ErrorCode.E3011_MN_AGGREGATION_REJECTED

    def test_enrich_preserves_parent_uks_drops_child_uks(self):
        parent = source(
            primary_key=frozenset({I("order_id"), I("line_no")}),
            dimension_columns=[
                dimension_column(I("order_id")),
                dimension_column(I("line_no")),
                dimension_column(I("customer_id")),
            ],
            unique_keys=[frozenset({I("order_id")})],
        )
        child = self._customers_with_uk()
        out = enrich(
            parent,
            child,
            parent_keys=(I("customer_id"),),
            child_keys=(I("id"),),
            join_type=JoinType.INNER,
        )
        assert out.unique_keys == parent.unique_keys

    def test_filter_preserves_uks(self):
        state = self._customers_with_uk()
        out = filter_(
            state,
            FrozenSQL.of(sqlglot.parse_one("region = 'NA'")),
            dependencies=frozenset({I("region")}),
        )
        assert out.unique_keys == state.unique_keys

    def test_aggregate_keeps_uks_inside_new_grain(self):
        state = self._customers_with_uk()
        out = aggregate(
            state,
            new_grain=frozenset({I("id")}),
            aggregations=(),
        )
        assert frozenset({I("id")}) in out.unique_keys

    def test_aggregate_drops_uks_straddling_new_grain(self):
        state = source(
            primary_key=frozenset({I("a"), I("b")}),
            dimension_columns=[
                dimension_column(I("a")),
                dimension_column(I("b")),
                dimension_column(I("c")),
            ],
            unique_keys=[frozenset({I("a"), I("c")})],
        )
        # New grain is {a} — UK {a, c} is no longer a subset, so dropped.
        out = aggregate(
            state,
            new_grain=frozenset({I("a")}),
            aggregations=(),
        )
        assert out.unique_keys == frozenset()

    def test_project_keeps_uks_whose_columns_survive(self):
        state = source(
            primary_key=frozenset({I("a")}),
            dimension_columns=[
                dimension_column(I("a")),
                dimension_column(I("b")),
                dimension_column(I("c")),
            ],
            unique_keys=[frozenset({I("b")}), frozenset({I("c")})],
        )
        out = project(state, columns=[I("a"), I("b")])
        assert out.unique_keys == frozenset({frozenset({I("b")})})

    def test_add_columns_preserves_uks(self):
        state = self._customers_with_uk()
        new_col = Column(
            name=I("region_upper"),
            expression=FrozenSQL.of(sqlglot.parse_one("UPPER(region)")),
            dependencies=frozenset({I("region")}),
            kind=ColumnKind.DIMENSION,
        )
        out = add_columns(state, definitions=(new_col,))
        assert out.unique_keys == state.unique_keys

    def test_broadcast_preserves_uks(self):
        from dataclasses import replace as dc_replace

        state = self._customers_with_uk()
        # Sealed aggregate column (no deps) so the scalar state stands
        # alone — same shape ``aggregate`` produces post-reduction.
        scalar_col = dc_replace(
            aggregate_column(I("total"), over=I("a")),
            dependencies=frozenset(),
            is_single_valued=True,
        )
        scalar = CalculationState(grain=frozenset(), columns=(scalar_col,))
        out = broadcast(state, scalar)
        assert out.unique_keys == state.unique_keys

    def test_merge_intersects_uks(self):
        a_col = dimension_column(I("a"))
        left = CalculationState(
            grain=frozenset({I("a")}),
            columns=(a_col, dimension_column(I("x"))),
            unique_keys=frozenset({frozenset({I("x")})}),
        )
        right = CalculationState(
            grain=frozenset({I("a")}),
            columns=(a_col, dimension_column(I("y"))),
            unique_keys=frozenset({frozenset({I("y")})}),
        )
        # Disjoint UKs on each side → intersection is empty post-merge,
        # which is the conservative, correctness-preserving answer for
        # FULL OUTER joins (see joins.py).
        out = merge(left, right)
        assert out.unique_keys == frozenset()

    def test_merge_keeps_uks_present_on_both_sides(self):
        # The shared UK must reference a *grain* column, since merge
        # rejects overlap on non-grain columns. Both sides declare
        # a UK at {a} (= the shared grain) — trivially preserved.
        a_col = dimension_column(I("a"))
        shared_uk = frozenset({frozenset({I("a")})})
        left = CalculationState(
            grain=frozenset({I("a")}),
            columns=(a_col, dimension_column(I("y"))),
            unique_keys=shared_uk,
        )
        right = CalculationState(
            grain=frozenset({I("a")}),
            columns=(a_col, dimension_column(I("z"))),
            unique_keys=shared_uk,
        )
        out = merge(left, right)
        assert out.unique_keys == shared_uk

    def test_filtering_join_preserves_uks(self):
        state = self._customers_with_uk()
        rhs = source(
            primary_key=frozenset({I("rid")}),
            dimension_columns=[dimension_column(I("rid"))],
        )
        out = filtering_join(
            state,
            rhs,
            lhs_keys=frozenset({I("id")}),
            rhs_keys=frozenset({I("rid")}),
            mode=FilterMode.SEMI,
        )
        assert out.unique_keys == state.unique_keys
