"""Thin wrappers over SQLGlot for frozen, comparable AST fragments.

Invariant 10 from ``ARCHITECTURE.md``: **SQL composition via AST only.**
Every SQL fragment that flows between layers travels as a SQLGlot
``Expression``, never as a string, and every comparison goes through
:func:`sql_expr_equal`.

The algebra and planner treat expressions as opaque values: they may
store, copy, and compare them; they do not rewrite them. Rewriting is
the job of :mod:`osi.codegen`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import sqlglot
from sqlglot import exp

from osi.errors import ErrorCode, OSIError

PARSE_DIALECT: Final[str] = ""
"""SQLGlot's default (ANSI-like) dialect identifier.

We intentionally parse scalar expressions with the dialect-neutral
grammar so dialect-specific surface syntax (BigQuery ``QUALIFY``,
Snowflake ``SQUARE_BRACKET_INDEXING``, ...) cannot leak into the
algebra. Dialect translation is codegen's job.
"""


def parse_sql_expr(source: str) -> exp.Expression:
    """Parse a single SQL scalar expression.

    Raises :class:`OSIError` with ``E1006_SQL_EXPRESSION_SYNTAX`` if
    ``source`` cannot be parsed. The code is in the ``E1xxx`` parsing
    family because *parsing user-supplied SQL is a layer-1 concern* —
    failures here are syntactic, not codegen failures. The previous
    ``E5002`` (a codegen "render failed" code) was misleading and broke
    the layer-to-error-prefix invariant in ``ARCHITECTURE.md``.
    """
    try:
        parsed = sqlglot.parse_one(source, read=PARSE_DIALECT or None)
    except sqlglot.errors.ParseError as err:
        raise OSIError(
            ErrorCode.E1006_SQL_EXPRESSION_SYNTAX,
            f"failed to parse SQL expression: {source!r}",
            context={"source": source, "sqlglot_error": str(err)},
        ) from err
    if parsed is None:
        raise OSIError(
            ErrorCode.E1006_SQL_EXPRESSION_SYNTAX,
            f"SQLGlot returned no AST for {source!r}",
            context={"source": source},
        )
    return parsed


def sql_expr_equal(a: exp.Expression, b: exp.Expression) -> bool:
    """Structural equality between SQLGlot expressions.

    Uses SQLGlot's canonical key so two expressions that render the same
    way compare equal regardless of incidental whitespace.
    """
    return bool(a == b)


@dataclass(frozen=True, slots=True)
class FrozenSQL:
    """A SQLGlot ``Expression`` wrapped so it can live inside frozen dataclasses.

    ``frozenset``/``tuple`` members require hashable elements. Rather
    than relying on SQLGlot's ``__hash__`` (which is present but walks
    the AST each call), we precompute a canonical string form.
    """

    expr: exp.Expression
    canonical: str

    @classmethod
    def of(cls, expr: exp.Expression) -> "FrozenSQL":
        """Build a ``FrozenSQL`` from a SQLGlot ``Expression``."""
        return cls(
            expr=expr,
            canonical=expr.sql(dialect=PARSE_DIALECT or None, normalize=True),
        )

    def __hash__(self) -> int:  # noqa: D105
        return hash(self.canonical)

    def __eq__(self, other: object) -> bool:  # noqa: D105
        if not isinstance(other, FrozenSQL):
            return NotImplemented
        return self.canonical == other.canonical


__all__ = [
    "PARSE_DIALECT",
    "FrozenSQL",
    "parse_sql_expr",
    "sql_expr_equal",
]
