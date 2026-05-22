"""Tests for the parser-level field dependency cycle check.

A dataset's fields form a dependency graph; the planner lowers
derived fields into a topologically ordered chain of ``ADD_COLUMNS``
CTE stages so the emitted SQL never relies on lateral aliasing
(``Proposed_OSI_Semantics.md §4.3``). A cycle cannot be lowered to
a finite stage count and is rejected at parse time as
``E_FIELD_DEPENDENCY_CYCLE``.

Unlike the deferral checks in :mod:`osi.parsing.foundation` that are
gated by :class:`FoundationFlags`, the cycle check is structural — no
flag opts back into cyclic models because there is no portable SQL
shape that could compile them.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.parser import parse_semantic_model

# ---------------------------------------------------------------------------
# Direct two-field cycle (a → b → a)
# ---------------------------------------------------------------------------


def test_direct_two_field_cycle__rejected() -> None:
    """``a`` references ``b`` and ``b`` references ``a`` ⇒ cycle.

    The simplest cyclic shape; the parser must reject it before the
    planner ever sees the model so users get the structural error at
    its source rather than as a downstream "internal cycle" surprise.
    """
    yaml_text = dedent("""\
        name: cycle
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: a, expression: "b + 1", role: fact}
              - {name: b, expression: "a * 2", role: fact}
        """)
    with pytest.raises(OSIParseError) as excinfo:
        parse_semantic_model(yaml_text)
    assert excinfo.value.code is ErrorCode.E_FIELD_DEPENDENCY_CYCLE
    context = excinfo.value.context
    assert context["dataset"] == "orders"
    cycle = context["cycle"]
    assert isinstance(cycle, list)
    assert cycle[0] == cycle[-1]


# ---------------------------------------------------------------------------
# Longer cycle (a → b → c → a)
# ---------------------------------------------------------------------------


def test_three_field_cycle__rejected() -> None:
    """``a`` → ``b`` → ``c`` → ``a`` is rejected with the full chain in context.

    The cycle context payload should capture every node so users can
    see exactly which fields participate, not just one offender.
    """
    yaml_text = dedent("""\
        name: cycle3
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: a, expression: "b + 1", role: fact}
              - {name: b, expression: "c + 1", role: fact}
              - {name: c, expression: "a + 1", role: fact}
        """)
    with pytest.raises(OSIParseError) as excinfo:
        parse_semantic_model(yaml_text)
    assert excinfo.value.code is ErrorCode.E_FIELD_DEPENDENCY_CYCLE
    cycle = excinfo.value.context["cycle"]
    assert isinstance(cycle, list)
    assert set(cycle) == {"a", "b", "c"}
    assert cycle[0] == cycle[-1]


# ---------------------------------------------------------------------------
# Self-cycle (a depends on a, where a is *not* an identity projection)
# ---------------------------------------------------------------------------


def test_field_depends_on_self_via_other_field__rejected() -> None:
    """A field whose body references itself in a non-identity shape ⇒ cycle.

    ``a = a + 1`` is the trivial degenerate case — the parser must
    reject it because there's no semantics under which a field
    references its own derived value before that value exists.
    Distinguished from the legitimate identity projection
    ``a = a`` where ``a`` is a physical column name shared with the
    field name (see ``test_identity_projection__not_a_cycle``).
    """
    yaml_text = dedent("""\
        name: self_cycle
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: a, expression: "amount * 2", role: fact}
              - {name: a_plus_a, expression: "a + a_plus_a", role: fact}
        """)
    with pytest.raises(OSIParseError) as excinfo:
        parse_semantic_model(yaml_text)
    assert excinfo.value.code is ErrorCode.E_FIELD_DEPENDENCY_CYCLE


# ---------------------------------------------------------------------------
# Negative cases — must NOT trigger the cycle check
# ---------------------------------------------------------------------------


def test_acyclic_chain__accepted() -> None:
    """A linear chain ``a → b → c`` parses cleanly under strict defaults."""
    yaml_text = dedent("""\
        name: chain
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
              - {name: a, expression: "amount * 2", role: fact}
              - {name: b, expression: "a + 1", role: fact}
              - {name: c, expression: "b + 1", role: fact}
        metrics:
          - name: total
            expression: SUM(orders.c)
        """)
    parse_semantic_model(yaml_text)


def test_identity_projection__not_a_cycle() -> None:
    """``{name: id, expression: id}`` is not a self-cycle.

    The bare ``id`` reference resolves to the physical column at the
    SOURCE step; treating it as a same-dataset dependency on the
    field of the same name would force every identity projection to
    be rejected.
    """
    yaml_text = dedent("""\
        name: identity
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: amount, expression: amount, role: fact}
        metrics:
          - name: total
            expression: SUM(orders.amount)
        """)
    parse_semantic_model(yaml_text)


def test_qualified_cross_dataset_reference__not_a_cycle() -> None:
    """A qualified ``other.field`` reference is not an inter-field dep.

    Cross-dataset references resolve through the relationship graph
    (enrichment planner). They must never participate in the
    same-dataset cycle check or models with normal foreign-key joins
    would be rejected.
    """
    yaml_text = dedent("""\
        name: qualified
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
    parse_semantic_model(yaml_text)


# ---------------------------------------------------------------------------
# Cycle is structural — no flag should turn it off
# ---------------------------------------------------------------------------


def test_cycle_check__not_disabled_by_legacy_permissive() -> None:
    """``FoundationFlags.legacy_permissive()`` does not turn off the cycle check.

    The cycle check is structural, not a deferral; no portable SQL
    shape compiles a cyclic field graph, so no flag should opt back
    into accepting one.
    """
    yaml_text = dedent("""\
        name: cycle_legacy
        datasets:
          - name: orders
            source: orders_table
            primary_key: [id]
            fields:
              - {name: id, expression: id, role: dimension}
              - {name: a, expression: "b + 1", role: fact}
              - {name: b, expression: "a * 2", role: fact}
        """)
    with pytest.raises(OSIParseError) as excinfo:
        parse_semantic_model(yaml_text, flags=FoundationFlags.legacy_permissive())
    assert excinfo.value.code is ErrorCode.E_FIELD_DEPENDENCY_CYCLE
