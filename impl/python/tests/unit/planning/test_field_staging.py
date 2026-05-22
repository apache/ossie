"""Unit tests for inter-field dependency staging in :func:`source_step`.

These tests pin the planner's contract for fields that reference other
fields on the same dataset (``Proposed_OSI_Semantics.md §4.3``):

* the planner must lower derived fields into a topologically ordered
  chain of ``ADD_COLUMNS`` CTE stages so the emitted SQL never relies
  on lateral aliasing within a single ``SELECT``,
* the chain must preserve declaration order *within* each level so
  golden SQL stays stable run-to-run,
* a model with no inter-field dependencies must still emit a single
  ``SOURCE`` step (no spurious extra CTEs),
* the same staging applies to enrich children — when a child dataset
  has derived fields the planner emits the child via ``SOURCE`` +
  ``ADD_COLUMNS`` and uses ``EnrichDerived`` so codegen reads the
  child columns by name.

The cycle case is verified by
``tests/unit/parsing/test_field_dependency_cycles.py`` because cycles
are rejected at parse time, not at planning time.
"""

from __future__ import annotations

import textwrap

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.parser import parse_semantic_model
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.algebra.state import ColumnKind
from osi.planning.plan import (
    AddColumnsPayload,
    EnrichDerivedPayload,
    EnrichPayload,
    PlanOperation,
    PlanStep,
)
from osi.planning.planner_context import PlannerContext


def _ctx_from_yaml(yaml_text: str) -> PlannerContext:
    """Parse ``yaml_text`` into a planner-ready context."""
    result = parse_semantic_model(textwrap.dedent(yaml_text))
    return PlannerContext(
        model=result.model, namespace=result.namespace, graph=result.graph
    )


def _ref(name: str, dataset: str | None = None) -> Reference:
    """Build a :class:`Reference` from raw strings (mypy-friendly).

    The :class:`Reference` constructor takes :class:`Identifier`
    instances; tests historically passed bare strings via the model
    parser. This helper centralises the normalisation so the test
    bodies stay readable.
    """
    dataset_id = normalize_identifier(dataset) if dataset is not None else None
    return Reference(dataset=dataset_id, name=normalize_identifier(name))


def _add_columns_levels(steps: tuple[PlanStep, ...]) -> list[set[str]]:
    """Return one set-of-names per ADD_COLUMNS step in order.

    Encapsulates the ``isinstance`` narrowing so per-test assertions
    can stay focused on the *shape* of the staged plan.
    """
    levels: list[set[str]] = []
    for step in steps:
        if step.operation is not PlanOperation.ADD_COLUMNS:
            continue
        payload = step.payload
        assert isinstance(payload, AddColumnsPayload)
        levels.append({str(d.name) for d in payload.definitions})
    return levels


def _add_columns_definitions_in_order(
    steps: tuple[PlanStep, ...],
) -> list[list[str]]:
    """Return the per-level definition order from each ADD_COLUMNS step."""
    out: list[list[str]] = []
    for step in steps:
        if step.operation is not PlanOperation.ADD_COLUMNS:
            continue
        payload = step.payload
        assert isinstance(payload, AddColumnsPayload)
        out.append([str(d.name) for d in payload.definitions])
    return out


# ---------------------------------------------------------------------------
# No inter-field dependencies — single SOURCE step (regression for the
# common case; staging must not introduce CTEs when none are needed)
# ---------------------------------------------------------------------------


def test_no_inter_field_deps__emits_single_source_step() -> None:
    """A dataset whose fields project only physical columns yields one SOURCE.

    This is the common case for a star-schema fact table; staging must
    not add extra CTEs that would clutter the emitted SQL or churn
    every existing golden snapshot.
    """
    ctx = _ctx_from_yaml("""\
        name: identity_fields
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        metrics:
          - name: total_amount
            expression: SUM(orders.amount)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total_amount"),)),
        context=ctx,
    )
    operations = [step.operation for step in plan_result.steps]
    assert operations.count(PlanOperation.SOURCE) == 1
    assert PlanOperation.ADD_COLUMNS not in operations


# ---------------------------------------------------------------------------
# Identity projection (``expression == name``) is not a self-cycle
# ---------------------------------------------------------------------------


def test_identity_projection__is_not_self_cycle() -> None:
    """``{name: id, expression: id}`` is the canonical pass-through shape.

    The bare ``id`` reference resolves to the physical column at the
    SOURCE step; treating it as an inter-field dependency on itself
    would produce a spurious ``E_FIELD_DEPENDENCY_CYCLE`` rejection.
    """
    ctx = _ctx_from_yaml("""\
        name: identity
        datasets:
          - name: orders
            source: orders_table
            primary_key: [order_id]
            fields:
              - {name: order_id, expression: order_id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        metrics:
          - name: total
            expression: SUM(orders.amount)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total"),)),
        context=ctx,
    )
    source_steps = [
        step for step in plan_result.steps if step.operation is PlanOperation.SOURCE
    ]
    assert len(source_steps) == 1
    column_names = {str(col.name) for col in source_steps[0].state.columns}
    assert column_names == {"order_id", "amount"}


# ---------------------------------------------------------------------------
# Linear chain: a → b → c → d (one ADD_COLUMNS per level)
# ---------------------------------------------------------------------------


def test_linear_dependency_chain__one_add_columns_per_level() -> None:
    """Chained derived fields produce exactly one ADD_COLUMNS per level.

    Pins the staged shape for the canonical chain
    ``net = amount - discount`` ⇒ ``net_doubled = net * 2`` ⇒
    ``net_quadrupled = net_doubled * 2``. Each derived field gets
    its own CTE so each subsequent ``SELECT`` references a committed
    alias from the prior CTE — portable on every dialect.
    """
    ctx = _ctx_from_yaml("""\
        name: chain
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: discount, expression: discount, role: fact}
              - {name: net, expression: "amount - discount", role: fact}
              - {name: net_doubled, expression: "net * 2", role: fact}
              - {name: net_quadrupled, expression: "net_doubled * 2", role: fact}
        metrics:
          - name: total
            expression: SUM(orders.net_quadrupled)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total"),)),
        context=ctx,
    )
    levels = _add_columns_levels(plan_result.steps)
    assert levels == [{"net"}, {"net_doubled"}, {"net_quadrupled"}]


# ---------------------------------------------------------------------------
# Branching reuse: c depends on a and b — both placed at the same level
# ---------------------------------------------------------------------------


def test_branching_reuse__siblings_share_a_level() -> None:
    """Two derived fields at the same depth share one ADD_COLUMNS step.

    ``net = amount - discount`` and ``tax = amount * 0.1`` both
    depend only on physical columns and so go on the same level
    (level 1). ``total_billable = net + tax`` depends on both and
    sits one level deeper.

    Per-level batching matters: a chain of N independent derived
    fields should not emit N CTEs when 1 will do (CTE count is a
    proxy for query-planner cost on most dialects).
    """
    ctx = _ctx_from_yaml("""\
        name: branching
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: discount, expression: discount, role: fact}
              - {name: net, expression: "amount - discount", role: fact}
              - {name: tax, expression: "amount * 0.1", role: fact}
              - {name: total_billable, expression: "net + tax", role: fact}
        metrics:
          - name: total
            expression: SUM(orders.total_billable)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total"),)),
        context=ctx,
    )
    levels = _add_columns_levels(plan_result.steps)
    assert levels == [{"net", "tax"}, {"total_billable"}]


def test_branching_reuse__preserves_declaration_order() -> None:
    """Siblings within a level keep their declaration order.

    Stable per-level ordering keeps SQL goldens deterministic across
    runs (Kahn's algorithm with hash-set iteration would otherwise
    reorder fields by dict insertion order, churning the snapshots
    for non-semantic reasons).
    """
    ctx = _ctx_from_yaml("""\
        name: ordering
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: zeta, expression: "amount * 1", role: fact}
              - {name: alpha, expression: "amount * 2", role: fact}
              - {name: middle, expression: "amount * 3", role: fact}
        metrics:
          - name: total
            expression: SUM(orders.alpha)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total"),)),
        context=ctx,
    )
    ordered = _add_columns_definitions_in_order(plan_result.steps)
    assert ordered == [["zeta", "alpha", "middle"]]


# ---------------------------------------------------------------------------
# Window-then-reference: a windowed field referenced by a downstream field
# ---------------------------------------------------------------------------


def test_window_field_referenced_downstream__staged_correctly() -> None:
    """A window function in field A, then field B references A.

    Window functions are evaluated per-row at the home grain
    (``§4.3.1``); a downstream field that uses A's windowed value
    in a CASE expression must read A from a committed CTE — embedding
    both inline would force ``ROW_NUMBER() OVER (...) AS rn, CASE
    WHEN rn = 1 THEN amount ELSE 0 END AS top_only`` which fails on
    Snowflake / Postgres / SQLite.
    """
    ctx = _ctx_from_yaml("""\
        name: windowed
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - name: rank_in_customer
                expression: "ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC)"
                role: fact
              - name: top_only
                expression: "CASE WHEN rank_in_customer = 1 THEN amount ELSE 0 END"
                role: fact
        metrics:
          - name: total_top
            expression: SUM(orders.top_only)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total_top"),)),
        context=ctx,
    )
    levels = _add_columns_levels(plan_result.steps)
    assert levels == [{"rank_in_customer"}, {"top_only"}]


# ---------------------------------------------------------------------------
# Materialised-derived deps: post-ADD_COLUMNS state must carry empty deps
# ---------------------------------------------------------------------------


def test_materialised_derived__has_empty_dependencies() -> None:
    """Once a derived field is materialised in a CTE, its deps are stripped.

    Downstream operators (AGGREGATE, MERGE, ENRICH) project columns
    by name from the prior CTE; the dependency list on the staged
    column is no longer needed and would trip the
    ``CalculationState`` validator (``E3006``) in any operator that
    drops the upstream physical columns.
    """
    ctx = _ctx_from_yaml("""\
        name: deps
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: doubled, expression: "amount * 2", role: fact}
        metrics:
          - name: total
            expression: SUM(orders.doubled)
        """)
    plan_result = plan(
        SemanticQuery(measures=(_ref("total"),)),
        context=ctx,
    )
    add_columns_step = next(
        s for s in plan_result.steps if s.operation is PlanOperation.ADD_COLUMNS
    )
    doubled = next(
        c for c in add_columns_step.state.columns if str(c.name) == "doubled"
    )
    assert doubled.dependencies == frozenset()


# ---------------------------------------------------------------------------
# Enrich child with derived fields → staged path (EnrichDerived)
# ---------------------------------------------------------------------------


def test_enrich_child_with_derived_fields__uses_enrich_derived() -> None:
    """A many-to-one child with derived fields stages via ENRICH_DERIVED.

    Inlining the child as ``JOIN raw_table`` would require lateral
    aliasing in the join's ``SELECT``. Staging the child as
    ``SOURCE`` + ``ADD_COLUMNS`` and using ``EnrichDerivedPayload``
    routes codegen to project child columns *by name* (see
    ``transpiler._render_enrich_derived``) — portable on every
    dialect.
    """
    ctx = _ctx_from_yaml("""\
        name: enrich_staged
        datasets:
          - name: customers
            source: customers_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: first_name, expression: first_name, role: dimension}
              - {name: last_name, expression: last_name, role: dimension}
              - name: full_name
                expression: "first_name || ' ' || last_name"
                role: dimension
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: total
            expression: SUM(orders.amount)
        """)
    plan_result = plan(
        SemanticQuery(
            dimensions=(_ref("full_name", dataset="customers"),),
            measures=(_ref("total"),),
        ),
        context=ctx,
    )
    enrich_steps = [
        step for step in plan_result.steps if step.operation is PlanOperation.ENRICH
    ]
    assert len(enrich_steps) == 1
    payload = enrich_steps[0].payload
    assert isinstance(payload, EnrichDerivedPayload)
    assert len(enrich_steps[0].inputs) == 2


def test_enrich_child_without_derived_fields__uses_inline_enrich() -> None:
    """A many-to-one child whose fields are all identity stays inline.

    Pins the no-staging-needed regression: enriches that *don't* need
    extra CTEs must still emit the compact single-step ``ENRICH``
    shape (``EnrichPayload`` with ``child_source`` set), preserving
    the historical golden output on the common case.
    """
    ctx = _ctx_from_yaml("""\
        name: enrich_inline
        datasets:
          - name: customers
            source: customers_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: name, expression: name, role: dimension}
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: total
            expression: SUM(orders.amount)
        """)
    plan_result = plan(
        SemanticQuery(
            dimensions=(_ref("name", dataset="customers"),),
            measures=(_ref("total"),),
        ),
        context=ctx,
    )
    enrich_steps = [
        step for step in plan_result.steps if step.operation is PlanOperation.ENRICH
    ]
    assert len(enrich_steps) == 1
    payload = enrich_steps[0].payload
    assert isinstance(payload, EnrichPayload)
    assert payload.child_source == "customers_table"


# ---------------------------------------------------------------------------
# Cross-dataset (qualified) field references do *not* count as same-dataset
# inter-field deps (regression: don't accidentally treat ``customers.region``
# as a sibling-of-``orders`` reference and create an impossible dependency)
# ---------------------------------------------------------------------------


def test_qualified_cross_dataset_reference__not_treated_as_inter_field_dep() -> None:
    """A qualified ref names a column on another dataset, not a sibling.

    The enrichment planner resolves qualified references through the
    relationship graph. Mistakenly treating
    ``customers.region`` as a same-dataset dep on
    ``orders`` would either force a spurious ``ADD_COLUMNS`` stage
    on ``orders`` or trip the parser-side cycle check when the
    referenced name isn't defined locally.
    """
    ctx = _ctx_from_yaml("""\
        name: qualified_ref
        datasets:
          - name: customers
            source: customers_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: region, expression: region, role: dimension}
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: total
            expression: SUM(orders.amount)
        """)
    plan_result = plan(
        SemanticQuery(
            dimensions=(_ref("region", dataset="customers"),),
            measures=(_ref("total"),),
        ),
        context=ctx,
    )
    levels = _add_columns_levels(plan_result.steps)
    assert levels == []


# ---------------------------------------------------------------------------
# Defensive: planner-internal cycle check (parser should catch it first)
# ---------------------------------------------------------------------------


def test_planner_topo_sort__defends_against_cycles() -> None:
    """Direct unit test for ``_topo_levels_by_dependency`` cycle handling.

    Cycles are rejected at parse time so this code path is normally
    unreachable, but we keep the defensive check because the planner
    is not a trust boundary; an upstream regression that disabled the
    parser check should produce a loud failure rather than a silently
    wrong plan.
    """
    import sqlglot

    from osi.common.identifiers import normalize_identifier
    from osi.common.sql_expr import FrozenSQL
    from osi.planning.algebra.state import Column
    from osi.planning.steps import _topo_levels_by_dependency

    name_a = normalize_identifier("a")
    name_b = normalize_identifier("b")
    expr = FrozenSQL.of(sqlglot.parse_one("1"))
    a = Column(
        name=name_a,
        expression=expr,
        dependencies=frozenset({name_b}),
        kind=ColumnKind.FACT,
    )
    b = Column(
        name=name_b,
        expression=expr,
        dependencies=frozenset({name_a}),
        kind=ColumnKind.FACT,
    )
    with pytest.raises(OSIPlanningError) as excinfo:
        _topo_levels_by_dependency((a, b))
    assert excinfo.value.code is ErrorCode.E_FIELD_DEPENDENCY_CYCLE
