"""Canonical-form stability of :class:`FrozenSQL`.

``FrozenSQL.canonical`` is the *only* thing
:class:`FrozenSQL.__hash__` and :class:`FrozenSQL.__eq__` look at.
Three properties have to hold for the algebra's immutability story to
work:

1. **Reproducibility** — calling :meth:`FrozenSQL.of` twice on the same
   parsed expression yields the same canonical string.
2. **Copy-stability** — :meth:`exp.Expression.copy` produces an AST
   that, when wrapped, has the same canonical string. The transpiler
   and the algebra rely on this every time they pass an argument
   through ``.copy()`` defensively.
3. **Round-trip stability** — re-parsing the rendered SQL of an
   expression produces a wrapper with the same canonical string. This
   is what makes :meth:`FrozenSQL` safe to serialize via
   :attr:`canonical` and reconstruct.

If any of these fail, two structurally identical predicates can hash
differently and the algebra's "set of FrozenSQL columns is a value"
contract breaks silently.
"""

from __future__ import annotations

import sqlglot
from hypothesis import given
from hypothesis import strategies as st

from osi.common.sql_expr import FrozenSQL

_BASE_EXPRS: list[str] = [
    "x",
    "x + 1",
    "x * y",
    "(x + y) * z",
    "SUM(x)",
    "SUM(price * qty)",
    "COUNT(DISTINCT customer_id)",
    "CASE WHEN x > 0 THEN 1 ELSE 0 END",
    "lower(name)",
    "x = 1 AND y = 2",
    "x IN (1, 2, 3)",
    "COALESCE(x, 0) + COALESCE(y, 0)",
]


@st.composite
def parsed_exprs(draw: st.DrawFn) -> sqlglot.exp.Expression:
    """Pick one of the curated source strings and parse it fresh."""
    src = draw(st.sampled_from(_BASE_EXPRS))
    return sqlglot.parse_one(src)


@given(parsed_exprs())
def test_of_is_idempotent(expr: sqlglot.exp.Expression) -> None:
    """Wrapping the same AST twice gives the same canonical form."""
    a = FrozenSQL.of(expr)
    b = FrozenSQL.of(expr)
    assert a.canonical == b.canonical
    assert hash(a) == hash(b)
    assert a == b


@given(parsed_exprs())
def test_of_stable_under_copy(expr: sqlglot.exp.Expression) -> None:
    """``expr.copy()`` must not change the canonical form."""
    a = FrozenSQL.of(expr)
    b = FrozenSQL.of(expr.copy())
    assert a.canonical == b.canonical
    assert hash(a) == hash(b)
    assert a == b


@given(parsed_exprs())
def test_of_stable_under_reparse(expr: sqlglot.exp.Expression) -> None:
    """Round-tripping through SQL text preserves canonical equality."""
    a = FrozenSQL.of(expr)
    rendered = a.canonical
    b = FrozenSQL.of(sqlglot.parse_one(rendered))
    assert a.canonical == b.canonical
    assert hash(a) == hash(b)
    assert a == b
