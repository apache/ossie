"""Window-function placement and frame-mode rules for Foundation v0.1.

The Foundation accepts standard SQL window functions in
``Measures`` / ``Fields`` / ``Order By`` / ``Having`` slots, with a
small list of rejection rules that fire at parse / classify time. This
module is the single source of truth for those rules:

* :func:`first_nested_window` — detects a window function whose argument
  contains another window function. Caller maps the hit to
  :attr:`ErrorCode.E_NESTED_WINDOW` per Appendix B **D-028(c)**.
* :func:`first_deferred_frame_clause` — detects ``GROUPS`` frames and
  parameterised frame bounds (``D-032``).
* :func:`contains_window` — true iff any node in the AST is an
  ``exp.Window``.
* :func:`is_windowed_expression` — top-level shape check used by
  classification.

The actual *positive* window planner runs across two surfaces today:
:mod:`osi.planning.planner_scalar` (scalar branch — windowed metrics
become :class:`PlanOperation.ADD_COLUMNS` over the home dataset after
enrichment) and :mod:`osi.planning.classify` (Where-clause rejection
via :attr:`ErrorCode.E_WINDOW_IN_WHERE`). Windowed *measures* in the
aggregation branch are not yet supported — see ``INFRA.md`` I-43.

This module itself stays purely *rejection* logic: it lets the parser
promote windows out of ``E_DEFERRED_KEY_REJECTED`` and emit the named
window codes (``E_NESTED_WINDOW``, ``E_DEFERRED_FRAME_MODE``) without
committing to the full planner surface.
"""

from __future__ import annotations

from typing import Optional

from sqlglot import expressions as exp

# Frame modes the Foundation accepts; everything else (currently just
# ``GROUPS``) raises ``E_DEFERRED_FRAME_MODE``.
_ACCEPTED_FRAME_KINDS: frozenset[str] = frozenset({"ROWS", "RANGE"})


def contains_window(expression: exp.Expression) -> bool:
    """Return True iff the AST contains at least one ``exp.Window`` node."""
    for node in expression.walk():
        if isinstance(_unwrap(node), exp.Window):
            return True
    return False


def is_windowed_expression(expression: exp.Expression) -> bool:
    """Report whether the *top-level* expression is a window function.

    Used to decide whether an expression is a "windowed metric" — a
    metric whose body is ``f(...) OVER (...)``. A composite metric that
    *references* a windowed metric is detected separately by
    :func:`references_windowed_metric`.
    """
    return isinstance(expression, exp.Window) or (
        isinstance(expression, exp.Alias) and isinstance(expression.this, exp.Window)
    )


def first_nested_window(expression: exp.Expression) -> Optional[exp.Window]:
    """Return the first ``OVER`` whose subtree contains another ``OVER``.

    ``D-031``: ``SUM(SUM(x) OVER (...)) OVER (...)`` is structurally
    ambiguous — the Foundation has no rule for the outer window's
    grain when the inner already partitions, so we reject it up front.
    Returns the *outer* window node so the error message can point at
    the right span.
    """
    for node in expression.walk():
        outer = _unwrap(node)
        if not isinstance(outer, exp.Window):
            continue
        if _has_window_descendant(outer.this):
            return outer
        for part in outer.args.get("partition_by") or []:
            if _has_window_descendant(part):
                return outer
        for ordered in outer.args.get("order") or []:
            if _has_window_descendant(ordered):
                return outer
    return None


def first_deferred_frame_clause(
    expression: exp.Expression,
) -> Optional[tuple[exp.Window, str]]:
    """Return ``(window, reason)`` for the first deferred frame, if any.

    ``D-032`` defers two frame shapes from Foundation v0.1:

    * ``GROUPS`` frame mode (``OVER (... GROUPS BETWEEN ...)``).
    * Parameterised frame bounds (``OVER (... ROWS BETWEEN :n
      PRECEDING AND CURRENT ROW)``).

    Returns ``None`` if every window in the AST uses an accepted
    ``ROWS`` or ``RANGE`` frame with literal bounds.
    """
    for node in expression.walk():
        win = _unwrap(node)
        if not isinstance(win, exp.Window):
            continue
        spec = win.args.get("spec")
        if spec is None:
            continue
        kind = (spec.args.get("kind") or "").upper()
        if kind and kind not in _ACCEPTED_FRAME_KINDS:
            return win, f"frame mode {kind!r}"
        for bound_key in ("start", "end"):
            bound = spec.args.get(bound_key)
            if _is_parameterised(bound):
                return win, "parameterised frame bound"
    return None


def references_windowed_metric(
    expression: exp.Expression,
    *,
    windowed_metric_names: frozenset[str],
) -> Optional[str]:
    """Return the first windowed metric name referenced from ``expression``.

    A composite metric like ``running_total / SUM(orders.amount)``
    references ``running_total`` — and if that base metric's body is a
    window function (``running_total = SUM(x) OVER (...)``) the
    composite is structurally a "metric on top of a windowed metric"
    which D-031 forbids.

    Returns ``None`` if the expression references no windowed metric.
    """
    for node in expression.walk():
        col = _unwrap(node)
        if not isinstance(col, exp.Column):
            continue
        # Match either qualified (``orders.running_total``) or bare
        # (``running_total``) against the windowed-metric names set.
        bare = (col.name or "").lower()
        qualified = ""
        if col.table:
            qualified = f"{col.table.lower()}.{bare}"
        if bare in windowed_metric_names:
            return bare
        if qualified and qualified in windowed_metric_names:
            return qualified
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _has_window_descendant(node: object) -> bool:
    if not isinstance(node, exp.Expression):
        return False
    for child in node.walk():
        if isinstance(_unwrap(child), exp.Window):
            return True
    return False


def _is_parameterised(bound: object) -> bool:
    """Return True iff the frame bound references a placeholder.

    SQLGlot represents ``:n`` and ``?`` as ``exp.Parameter`` /
    ``exp.Placeholder`` nodes. Literal bounds (``1``, ``UNBOUNDED``,
    ``CURRENT ROW``) come back as plain strings ('UNBOUNDED' /
    'CURRENT ROW') or as ``exp.Literal`` for numeric literals — none
    of which the planner needs to reject.
    """
    if not isinstance(bound, exp.Expression):
        return False
    for child in bound.walk():
        ast = _unwrap(child)
        if isinstance(ast, (exp.Parameter, exp.Placeholder)):
            return True
    return False


def _unwrap(node: object) -> exp.Expression:
    """``walk()`` yields ``(node, parent, key)`` in newer sqlglot."""
    if isinstance(node, exp.Expression):
        return node
    if isinstance(node, tuple) and node and isinstance(node[0], exp.Expression):
        return node[0]
    return exp.Expression()


__all__ = [
    "contains_window",
    "is_windowed_expression",
    "first_nested_window",
    "first_deferred_frame_clause",
    "references_windowed_metric",
]
