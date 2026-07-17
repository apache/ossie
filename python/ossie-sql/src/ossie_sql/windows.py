"""Window-function helpers matching the spec's "Window Functions" section.

The spec documents exactly two frame modes -- ``ROWS`` and ``RANGE`` -- and
says window functions "should act consistently with window functions in
ANSI SQL", whose core (SQL:2003, the standard this whole language is based
on per the spec's "Standards Reference" section) only defines those two
frame modes; ``GROUPS`` is a later (SQL:2011) addition. So ``GROUPS`` frames
are treated as out of scope here.

Note: an earlier draft implementation (PR #125) also rejected *nested*
window functions and *parameterized* frame bounds. Neither restriction
appears anywhere in the committed spec text, so this module does not
enforce them -- per this package's policy of treating the committed spec as
authoritative over earlier drafts.
"""

from __future__ import annotations

from sqlglot import exp

_ACCEPTED_FRAME_KINDS = frozenset({"ROWS", "RANGE"})


def contains_window(expression: exp.Expr) -> bool:
    """Return whether ``expression``'s AST contains an ``OVER (...)`` window."""
    return any(isinstance(node, exp.Window) for node in expression.walk())


def first_unsupported_frame(expression: exp.Expr) -> exp.Window | None:
    """Return the first window whose frame clause uses an unsupported mode.

    Returns ``None`` if every ``OVER (...)`` in ``expression`` either omits a
    frame clause or uses ``ROWS``/``RANGE``.
    """
    for node in expression.walk():
        if not isinstance(node, exp.Window):
            continue
        spec = node.args.get("spec")
        if spec is None:
            continue
        kind = (spec.args.get("kind") or "").upper()
        if kind and kind not in _ACCEPTED_FRAME_KINDS:
            return node
    return None


__all__ = ["contains_window", "first_unsupported_frame"]
