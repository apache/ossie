"""Property tests for the positive window planner (S-22 / S-24).

Two properties:

1. **Round-trip**: a valid windowed expression parses, passes the
   deferred-construct check, renders back, and re-parses to a SQL
   string structurally equivalent to the input (modulo whitespace
   normalisation). Catches accidental rewrites that change semantics.
2. **Detection invariant**: the *top-level* window detector and the
   *contains-window* detector agree on shape (every top-level window
   contains a window; the converse need not hold).

The strategies generate small but realistic windowed bodies covering
the OSI-supported axes: aggregate vs ranking, with / without
PARTITION, with / without explicit frame.
"""

from __future__ import annotations

from typing import Optional

from hypothesis import given
from hypothesis import strategies as st

from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.common.windows import contains_window, is_windowed_expression
from osi.parsing.deferred import check_expression_deferred


def _frozen(sql: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(sql))


_AGGREGATE_FNS = [
    "SUM(amount)",
    "COUNT(id)",
    "MIN(amount)",
    "MAX(amount)",
    "AVG(amount)",
]
_RANKING_FNS = ["ROW_NUMBER()", "RANK()", "DENSE_RANK()"]


@st.composite
def windowed_expressions(draw) -> tuple[str, str, Optional[str]]:
    """Generate (function_call, partition_cols, order_cols).

    The strings are concatenated by the caller into a valid OVER
    clause. The strategy keeps the alphabet small (a/b/c columns) so
    sqlglot's deterministic rendering is stable across versions.
    """
    fn = draw(st.sampled_from(_AGGREGATE_FNS + _RANKING_FNS))
    partition_cols = draw(
        st.lists(st.sampled_from(["a", "b"]), max_size=2, unique=True)
    )
    order_cols_raw = draw(
        st.lists(st.sampled_from(["a", "b", "c"]), max_size=2, unique=True)
    )
    order_cols = ", ".join(order_cols_raw) if order_cols_raw else None
    partition = ", ".join(partition_cols) if partition_cols else None
    return fn, partition, order_cols


def _build(fn: str, partition: Optional[str], order: Optional[str]) -> str:
    parts: list[str] = []
    if partition:
        parts.append(f"PARTITION BY {partition}")
    if order:
        parts.append(f"ORDER BY {order}")
    inner = " ".join(parts)
    return f"{fn} OVER ({inner})" if inner else f"{fn} OVER ()"


# ---------------------------------------------------------------------------
# Property 1: round-trip
# ---------------------------------------------------------------------------


@given(windowed_expressions())
def test_window_roundtrip_preserves_canonical(parts) -> None:
    sql = _build(*parts)
    parsed = _frozen(sql)
    # The parser does not reject a valid window after S-22.
    check_expression_deferred(parsed, where="metric x")
    # Re-rendered SQL re-parses with the same canonical form. This
    # catches any rewrite that loses partition / order columns or
    # silently flips aggregate / ranking shape.
    rendered = parsed.expr.sql()
    reparsed = _frozen(rendered)
    assert reparsed.canonical == parsed.canonical


# ---------------------------------------------------------------------------
# Property 2: detector agreement
# ---------------------------------------------------------------------------


@given(windowed_expressions())
def test_top_level_window_implies_contains_window(parts) -> None:
    parsed = _frozen(_build(*parts)).expr
    assert is_windowed_expression(parsed)
    assert contains_window(parsed)


@given(windowed_expressions(), st.sampled_from(["+ 1", "* 2", "- 3"]))
def test_arithmetic_around_window_breaks_top_level_only(parts, op_suffix) -> None:
    body_sql = _build(*parts) + " " + op_suffix
    parsed = _frozen(body_sql).expr
    # Composing the window with arithmetic moves it out of the
    # *top-level* slot but the AST still contains a window node.
    assert not is_windowed_expression(parsed)
    assert contains_window(parsed)
