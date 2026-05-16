"""Unit tests for :mod:`osi.planning.plan` value types.

Covers :class:`PlanStep`, :class:`QueryPlan` invariants, and JSON
serialisation for every payload variant. Snapshots are tested via the
goldens below; here we focus on type-level correctness.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.planning.algebra.operations import FilterMode, JoinType
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
)
from osi.planning.plan import (
    AddColumnsPayload,
    AggregatePayload,
    BroadcastPayload,
    EnrichPayload,
    FilteringJoinPayload,
    FilterPayload,
    MergePayload,
    OrderByEntry,
    PlanOperation,
    PlanStep,
    ProjectPayload,
    QueryPlan,
    SourcePayload,
)

# AST helpers — column references and aggregates are built
# programmatically rather than via ``sqlglot.parse_one(f"…{name}…")``
# because the latter feeds an unquoted user identifier through
# sqlglot's expression parser. When ``name`` happens to be a SQL
# reserved word (``in``, ``select``, …) the parser raises
# ``ParseError`` mid-fixture. The ``quoted=True`` form sidesteps
# parsing entirely and is keyword-safe by construction.


def _dim(name: str) -> Column:
    nid = normalize_identifier(name)
    return Column(
        name=nid,
        expression=FrozenSQL.of(exp.column(str(nid), quoted=True)),
        dependencies=frozenset(),
        kind=ColumnKind.DIMENSION,
    )


def _fact(name: str) -> Column:
    nid = normalize_identifier(name)
    return Column(
        name=nid,
        expression=FrozenSQL.of(exp.column(str(nid), quoted=True)),
        dependencies=frozenset(),
        kind=ColumnKind.FACT,
    )


def _agg(name: str, *, over: str) -> Column:
    over_id = normalize_identifier(over)
    return Column(
        name=normalize_identifier(name),
        expression=FrozenSQL.of(
            exp.Anonymous(this="SUM", expressions=[exp.column(str(over_id), quoted=True)])
        ),
        dependencies=frozenset({over_id}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(
            function=AggregateFunction.SUM,
            argument=FrozenSQL.of(exp.column(str(over_id), quoted=True)),
        ),
    )


def _source_state() -> CalculationState:
    dim = _dim("id")
    fact = _fact("amount")
    return CalculationState(
        grain=frozenset({dim.name}),
        columns=(dim, fact),
    )


# ---------------------------------------------------------------------------
# PlanStep
# ---------------------------------------------------------------------------


class TestPlanStep:
    def test_inputs_are_stored_as_tuple(self) -> None:
        step = PlanStep(
            step_id=0,
            operation=PlanOperation.SOURCE,
            inputs=(),
            state=_source_state(),
            payload=SourcePayload(
                dataset=normalize_identifier("orders"),
                primary_key=frozenset({normalize_identifier("id")}),
            ),
        )
        assert step.inputs == ()

    def test_step_is_frozen(self) -> None:
        step = PlanStep(
            step_id=0,
            operation=PlanOperation.SOURCE,
            inputs=(),
            state=_source_state(),
            payload=SourcePayload(
                dataset=normalize_identifier("orders"),
                primary_key=frozenset({normalize_identifier("id")}),
            ),
        )
        # Frozen dataclasses raise ``FrozenInstanceError``; narrow the
        # catch so a future refactor that makes ``step_id`` mutable
        # via ``object.__setattr__`` doesn't silently pass this test.
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            step.step_id = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QueryPlan invariants
# ---------------------------------------------------------------------------


class TestQueryPlanInvariants:
    def _source_step(self, step_id: int = 0) -> PlanStep:
        return PlanStep(
            step_id=step_id,
            operation=PlanOperation.SOURCE,
            inputs=(),
            state=_source_state(),
            payload=SourcePayload(
                dataset=normalize_identifier("orders"),
                primary_key=frozenset({normalize_identifier("id")}),
            ),
        )

    def test_dangling_input_rejected(self) -> None:
        s1 = self._source_step(step_id=0)
        s2 = PlanStep(
            step_id=1,
            operation=PlanOperation.PROJECT,
            inputs=(99,),  # unknown
            state=_source_state(),
            payload=ProjectPayload(columns=(normalize_identifier("id"),)),
        )
        with pytest.raises(ValueError, match="unplanned input"):
            QueryPlan(steps=(s1, s2), root_step_id=1)

    def test_root_must_be_a_declared_step(self) -> None:
        s1 = self._source_step(step_id=0)
        with pytest.raises(ValueError, match="is not a step"):
            QueryPlan(steps=(s1,), root_step_id=42)

    def test_root_property_returns_root_step(self) -> None:
        s1 = self._source_step(step_id=0)
        p = QueryPlan(steps=(s1,), root_step_id=0)
        assert p.root is s1

    def test_default_order_by_and_output_columns_are_empty(self) -> None:
        s1 = self._source_step(step_id=0)
        p = QueryPlan(steps=(s1,), root_step_id=0)
        assert p.order_by == ()
        assert p.output_columns == ()


# ---------------------------------------------------------------------------
# JSON serialisation (for goldens)
# ---------------------------------------------------------------------------


def _make_plan_with_every_payload() -> QueryPlan:
    src = PlanStep(
        step_id=0,
        operation=PlanOperation.SOURCE,
        inputs=(),
        state=_source_state(),
        payload=SourcePayload(
            dataset=normalize_identifier("orders"),
            primary_key=frozenset({normalize_identifier("id")}),
        ),
    )
    filt = PlanStep(
        step_id=1,
        operation=PlanOperation.FILTER,
        inputs=(0,),
        state=_source_state(),
        payload=FilterPayload(
            predicate=FrozenSQL.of(sqlglot.parse_one("amount > 0")),
            dependencies=frozenset({normalize_identifier("amount")}),
            is_post_aggregate=False,
        ),
    )
    add = PlanStep(
        step_id=2,
        operation=PlanOperation.ADD_COLUMNS,
        inputs=(1,),
        state=_source_state(),
        payload=AddColumnsPayload(definitions=(_dim("id"),)),
    )
    # After aggregate, the column's dependencies are sealed (all deps
    # live on the *upstream* state). Mirror that here to satisfy I-6.
    agg_col = _agg("total", over="amount")
    sealed_agg = Column(
        name=agg_col.name,
        expression=agg_col.expression,
        dependencies=frozenset(),
        kind=ColumnKind.AGGREGATE,
        aggregate=agg_col.aggregate,
    )
    agg_state = CalculationState(
        grain=frozenset(),
        columns=(sealed_agg,),
    )
    aggst = PlanStep(
        step_id=3,
        operation=PlanOperation.AGGREGATE,
        inputs=(2,),
        state=agg_state,
        payload=AggregatePayload(
            new_grain=frozenset(),
            aggregations=(sealed_agg,),
        ),
    )
    proj = PlanStep(
        step_id=4,
        operation=PlanOperation.PROJECT,
        inputs=(3,),
        state=agg_state,
        payload=ProjectPayload(columns=(normalize_identifier("total"),)),
    )
    bcast = PlanStep(
        step_id=5,
        operation=PlanOperation.BROADCAST,
        inputs=(4,),
        state=agg_state,
        payload=BroadcastPayload(column=sealed_agg),
    )
    enr = PlanStep(
        step_id=6,
        operation=PlanOperation.ENRICH,
        inputs=(5,),
        state=agg_state,
        payload=EnrichPayload(
            child_dataset=normalize_identifier("customers"),
            child_columns=(_dim("region"),),
            keys=frozenset({normalize_identifier("id")}),
            join_type=JoinType.LEFT,
        ),
    )
    mrg = PlanStep(
        step_id=7,
        operation=PlanOperation.MERGE,
        inputs=(6,),
        state=agg_state,
        payload=MergePayload(on=frozenset()),
    )
    fj = PlanStep(
        step_id=8,
        operation=PlanOperation.FILTERING_JOIN,
        inputs=(7,),
        state=agg_state,
        payload=FilteringJoinPayload(
            lhs_keys=frozenset({normalize_identifier("id")}),
            rhs_keys=frozenset({normalize_identifier("id")}),
            mode=FilterMode.SEMI,
        ),
    )
    return QueryPlan(
        steps=(src, filt, add, aggst, proj, bcast, enr, mrg, fj),
        root_step_id=8,
        order_by=(OrderByEntry(column=normalize_identifier("total"), descending=True),),
        limit=5,
        output_columns=(normalize_identifier("total"),),
    )


class TestQueryPlanToJson:
    def test_every_payload_kind_serializes(self) -> None:
        plan_obj = _make_plan_with_every_payload()
        js = plan_obj.to_json()
        kinds = {s["payload"]["kind"] for s in js["steps"]}
        assert kinds == {
            "source",
            "filter",
            "add_columns",
            "aggregate",
            "project",
            "broadcast",
            "enrich",
            "merge",
            "filtering_join",
        }

    def test_output_fields_are_present(self) -> None:
        plan_obj = _make_plan_with_every_payload()
        js = plan_obj.to_json()
        assert js["root_step_id"] == 8
        assert js["limit"] == 5
        assert js["order_by"] == [{"column": "total", "descending": True}]
        assert js["output_columns"] == ["total"]
