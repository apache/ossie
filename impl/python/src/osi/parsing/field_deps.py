"""Shared inter-field dependency analysis.

A field's expression may reference other fields on the same dataset by
bare name (e.g. ``net_amount = amount - discount`` where both ``amount``
and ``discount`` are sibling fields). The planner uses these
dependencies to topologically order ``ADD_COLUMNS`` stages so the
emitted SQL never relies on lateral aliasing within a single
``SELECT`` (``Proposed_OSI_Semantics.md §4.3``); the parser uses them
to reject cycles up front (``E_FIELD_DEPENDENCY_CYCLE``).

This module lives under ``osi.parsing`` so both the parser-side
strictness checks (``parsing.foundation``) and the planner-side
column builder (``planning.columns``) can share one implementation.
The function is pure and depends only on already-parsed data
(``Field`` AST + the set of sibling names), so importing it from
either side is layer-safe.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.parsing.models import Field


def field_inter_field_dependencies(
    field: Field, sibling_field_names: Iterable[Identifier]
) -> frozenset[Identifier]:
    """Return every sibling field that ``field`` references in its body.

    Detection rules
    ---------------
    A bare column reference (``exp.Column`` with ``table is None``)
    in ``field.expression`` is treated as a sibling-field dependency
    iff the bare name matches some entry in ``sibling_field_names``.

    The field's *own* name is included in this check with one
    exception: the **identity projection** ``{name: x, expression: x}``
    is treated as a pure pass-through to the physical column ``x`` and
    produces no self-dependency (this is the canonical shape for
    declaring a passthrough field whose name happens to match a
    physical column). Any *other* expression that mentions the
    field's own name (``a_plus_a = a + a_plus_a``,
    ``b = sin(b) + 1``) records a self-dependency and is rejected by
    the cycle check as ``E_FIELD_DEPENDENCY_CYCLE`` — there's no
    semantics under which a derived field references its own
    derived value before that value exists.

    Qualified references (``customers.region``) name a column on a
    *different* dataset and are resolved by the enrichment planner;
    they never contribute to same-dataset inter-field dependencies.

    Window expressions
    ------------------
    A window function's ``PARTITION BY`` and ``ORDER BY`` operands
    are walked the same way as the rest of the expression — a
    windowed field that partitions on a sibling-field name picks up
    that sibling as a dependency, which is what the staged-CTE
    planner needs to keep the SQL portable across dialects.
    """
    sibling_set = frozenset(sibling_field_names)
    is_identity = _is_identity_projection(field)
    deps: set[Identifier] = set()
    for col in field.expression.expr.find_all(exp.Column):
        if col.table:
            continue
        name = normalize_identifier(col.name)
        if name == field.name and is_identity:
            continue
        if name in sibling_set:
            deps.add(name)
    return frozenset(deps)


def _is_identity_projection(field: Field) -> bool:
    """Return True iff ``field``'s expression is exactly the bare field name.

    The identity projection ``{name: x, expression: x}`` declares
    "surface the physical column ``x`` as field ``x``". The AST for
    such an expression is a single :class:`exp.Column` whose name
    equals ``field.name`` and which carries no table qualifier. Any
    structure beyond that single column node (arithmetic, function
    calls, alternate names) means the expression is *derived* and
    a self-reference inside it must be treated as a cycle.
    """
    root = field.expression.expr
    if not isinstance(root, exp.Column):
        return False
    if root.table:
        return False
    return normalize_identifier(root.name) == field.name


__all__ = ["field_inter_field_dependencies"]


__all__ = ["field_inter_field_dependencies"]
