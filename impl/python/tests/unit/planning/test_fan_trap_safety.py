"""TDD tests for fan-trap rejection (Issue 2.2).

The algebra contract states that ``enrich(parent, child, ...)``
preserves ``parent.grain``. That is true only when each parent row
matches *at most one* child row — equivalently, when ``child`` is
unique on the right-hand join keys (``child.grain ⊆ child_keys``).
If the planner asks for a 1→N traversal (e.g. enrich
``customers`` with ``orders`` joining on ``customer_id``), each parent
row matches multiple child rows and the result silently fans out.

These tests pin the closed-algebra rule that the fan trap is detected
*at the algebra level*, not at codegen, and not via a caller-asserted
boolean. The signature is symmetric with ``merge`` and
``filtering_join``: both sides are full :class:`CalculationState`
values, and the algebra derives safety from grain.

Each test is written so it fails today (the current ``enrich`` accepts
a ``cardinality_n_to_one`` flag and trusts it). After the refactor:

* ``enrich`` takes ``child: CalculationState``, ``parent_keys`` and
  ``child_keys`` (positional pairings), and a ``join_type``.
* ``enrich`` raises ``E3011_MN_AGGREGATION_REJECTED`` (specialised as
  fan-trap) when ``child.grain`` is not a subset of ``child_keys``.
* The planner's reverse-direction traversal of an N:1 edge produces
  the same error.
"""

from __future__ import annotations

import textwrap

import pytest

from osi.common.identifiers import normalize_identifier
from osi.config import FoundationFlags
from osi.errors import AlgebraError, ErrorCode, OSIError
from osi.parsing.parser import parse_semantic_model
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.algebra.operations import JoinType, enrich, source
from osi.planning.algebra.state import Column, ColumnKind
from osi.planning.planner_context import PlannerContext

# ---------------------------------------------------------------------------
# Algebra-level fan-trap detection
# ---------------------------------------------------------------------------


def _customers_state():
    return source(
        primary_key=frozenset({normalize_identifier("id")}),
        dimension_columns=[
            Column(
                name=normalize_identifier("id"),
                expression=__sql("id"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
            Column(
                name=normalize_identifier("region"),
                expression=__sql("region"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
        ],
    )


def _orders_state():
    return source(
        primary_key=frozenset({normalize_identifier("order_id")}),
        dimension_columns=[
            Column(
                name=normalize_identifier("order_id"),
                expression=__sql("order_id"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
            Column(
                name=normalize_identifier("customer_id"),
                expression=__sql("customer_id"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
        ],
        fact_columns=[
            Column(
                name=normalize_identifier("amount"),
                expression=__sql("amount"),
                dependencies=frozenset(),
                kind=ColumnKind.FACT,
            ),
        ],
    )


def __sql(s: str):
    from osi.common.sql_expr import FrozenSQL, parse_sql_expr

    return FrozenSQL.of(parse_sql_expr(s))


# Forward (safe) direction: orders (N) -> customers (1), join on customer_id=id
def test_enrich_n_to_one_succeeds() -> None:
    parent = _orders_state()
    child = _customers_state()
    out = enrich(
        parent,
        child,
        parent_keys=(normalize_identifier("customer_id"),),
        child_keys=(normalize_identifier("id"),),
        join_type=JoinType.LEFT,
    )
    assert out.grain == parent.grain
    assert normalize_identifier("region") in out.column_names


# Reverse (fan-trap) direction: customers (1) -> orders (N), join on id=customer_id
def test_enrich_one_to_many_reverse_is_fan_trap() -> None:
    parent = _customers_state()
    child = _orders_state()
    with pytest.raises(AlgebraError) as exc:
        enrich(
            parent,
            child,
            parent_keys=(normalize_identifier("id"),),
            child_keys=(normalize_identifier("customer_id"),),
            join_type=JoinType.LEFT,
        )
    assert exc.value.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED


def test_enrich_rejects_when_child_keys_dont_cover_child_grain() -> None:
    """Even with non-PK join keys, child must be unique on them."""
    # Build a child whose grain is {a, b} but join keys are only {a}.
    child = source(
        primary_key=frozenset({normalize_identifier("a"), normalize_identifier("b")}),
        dimension_columns=[
            Column(
                name=normalize_identifier("a"),
                expression=__sql("a"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
            Column(
                name=normalize_identifier("b"),
                expression=__sql("b"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
            Column(
                name=normalize_identifier("v"),
                expression=__sql("v"),
                dependencies=frozenset(),
                kind=ColumnKind.DIMENSION,
            ),
        ],
    )
    parent = _orders_state()
    with pytest.raises(AlgebraError) as exc:
        enrich(
            parent,
            child,
            parent_keys=(normalize_identifier("customer_id"),),
            child_keys=(normalize_identifier("a"),),
            join_type=JoinType.LEFT,
        )
    assert exc.value.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED


def test_enrich_rejects_unknown_parent_key() -> None:
    parent = _orders_state()
    child = _customers_state()
    with pytest.raises(AlgebraError) as exc:
        enrich(
            parent,
            child,
            parent_keys=(normalize_identifier("nonexistent"),),
            child_keys=(normalize_identifier("id"),),
            join_type=JoinType.LEFT,
        )
    assert exc.value.code is ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


def test_enrich_rejects_unknown_child_key() -> None:
    parent = _orders_state()
    child = _customers_state()
    with pytest.raises(AlgebraError) as exc:
        enrich(
            parent,
            child,
            parent_keys=(normalize_identifier("customer_id"),),
            child_keys=(normalize_identifier("nonexistent"),),
            join_type=JoinType.LEFT,
        )
    assert exc.value.code is ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


def test_enrich_rejects_mismatched_arity() -> None:
    parent = _orders_state()
    child = _customers_state()
    with pytest.raises(AlgebraError) as exc:
        enrich(
            parent,
            child,
            parent_keys=(
                normalize_identifier("customer_id"),
                normalize_identifier("order_id"),
            ),
            child_keys=(normalize_identifier("id"),),
            join_type=JoinType.LEFT,
        )
    assert exc.value.code is ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


# ---------------------------------------------------------------------------
# Planner-level fan-trap detection
# ---------------------------------------------------------------------------


_FAN_TRAP_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: fan_trap_demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [order_id]
            fields:
              - name: order_id
                expression: order_id
                role: dimension
              - name: customer_id
                expression: customer_id
                role: dimension
              - name: amount
                expression: amount
                role: fact
            metrics:
              - name: total
                expression: SUM(amount)
          - name: customers
            source: sales.customers
            primary_key: [id]
            fields:
              - name: id
                expression: id
                role: dimension
              - name: region
                expression: region
                role: dimension
            metrics:
              - name: customer_count
                expression: COUNT(id)
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
    """)


def _fan_trap_context() -> PlannerContext:
    # Per-dataset ``metrics:`` blocks in the fixture are deferred
    # under the strict Foundation; opt back in via the legacy-
    # permissive flag set so the planner-side fan-trap safety
    # contract stays exercised.
    result = parse_semantic_model(
        _FAN_TRAP_MODEL, flags=FoundationFlags.legacy_permissive()
    )
    return PlannerContext(
        model=result.model,
        namespace=result.namespace,
        graph=result.graph,
    )


def test_planner_rejects_one_to_many_traversal() -> None:
    """Enriching customers with orders (1->N) must error before SQL.

    In TPC-DS terms: ``SELECT region, COUNT(orders.order_id) FROM
    customers GROUP BY region`` requires aggregating orders FIRST then
    enriching, not enriching customers with orders.
    """
    ctx = _fan_trap_context()
    # Force the planner into the reverse direction: a query whose
    # measure is on customers (so customers becomes the fact root) but
    # which references a dimension on orders. Orders is the N-side, so
    # bringing it into a customers-rooted state is a fan trap.
    query = SemanticQuery(
        dimensions=(
            Reference(
                dataset=normalize_identifier("orders"),
                name=normalize_identifier("customer_id"),
            ),
        ),
        measures=(
            Reference(
                dataset=normalize_identifier("customers"),
                name=normalize_identifier("customer_count"),
            ),
        ),
    )
    with pytest.raises(OSIError) as exc:
        plan(query, ctx)
    # S-9 / D-022: the algebra layer is conservative and raises the
    # internal E3011 precondition signal on any fan-trap or N:N edge.
    # The planner reclassifies that signal at the user-facing surface:
    # for a fan-trap on a 1:N edge (this case) it surfaces as
    # E_UNSAFE_REAGGREGATION (plan-shape decomposition failure); for a
    # true N:N edge it surfaces as E3012 / E3013 (per-query M:N).
    # Neither path raises E3011 user-facing — that code is reserved
    # for the engine-capability opt-out (Proposed_OSI_Semantics.md §6.8
    # *Semantic guarantee*).
    assert exc.value.code is ErrorCode.E_UNSAFE_REAGGREGATION


def test_planner_accepts_n_to_one_traversal() -> None:
    """Orders -> customers (N->1) must plan cleanly."""
    ctx = _fan_trap_context()
    query = SemanticQuery(
        dimensions=(
            Reference(
                dataset=normalize_identifier("customers"),
                name=normalize_identifier("region"),
            ),
        ),
        measures=(
            Reference(
                dataset=normalize_identifier("orders"),
                name=normalize_identifier("total"),
            ),
        ),
    )
    p = plan(query, ctx)
    ops = [s.operation.value for s in p.steps]
    assert "enrich" in ops
    assert "aggregate" in ops
