"""SQLGlot dialect for the Ossie expression language.

Implements the grammar defined in ``core-spec/expression_language.md``
("Ossie_SQL_2026"). Registers with SQLGlot as ``"ossie"``, so
``sqlglot.parse_one(sql, read="ossie")`` parses the spec's SQL subset and
``expression.sql(dialect="ossie")`` renders it back.

The spec is explicitly an ANSI SQL:2003 subset, so this dialect starts from
SQLGlot's default (ANSI-like) Tokenizer/Parser/Generator and only overrides
the handful of spec constructs that the default dialect either can't parse
in the spec's exact shape (``DATEADD``/``DATEDIFF``/``DATE_PART`` with a
bare, leading date-part argument) or renders back in a spelling the spec
doesn't define (e.g. ``STR_POSITION`` instead of ``POSITION ... IN``,
``APPROX_DISTINCT`` instead of ``APPROX_COUNT_DISTINCT``). Functions the spec
lists that SQLGlot has no dedicated AST node for (``IFF``, ``ZEROIFNULL``,
``NULLIFZERO``, ...) already round-trip correctly as-is via SQLGlot's generic
``exp.Anonymous`` fallback and need no customization here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from sqlglot import TokenType, exp
from sqlglot.dialects.dialect import Dialect, unit_to_var
from sqlglot.generator import Generator
from sqlglot.helper import seq_get
from sqlglot.parser import Parser
from sqlglot.tokens import Tokenizer

_FuncBuilder = Callable[[Sequence[exp.Expression]], exp.Expression]


def _build_date_delta(exp_class: type[exp.DateAdd] | type[exp.DateDiff]) -> _FuncBuilder:
    """Build a parser for ``FUNC(part, amount_or_start, date_or_end)``.

    The spec puts the date-part identifier FIRST (``DATEADD(day, 7, d)``,
    ``DATEDIFF(day, d1, d2)``). SQLGlot's generic positional ``Func``
    construction instead treats the *last* argument as the unit, so this
    needs a dedicated builder rather than relying on the default mapping.
    """

    def _builder(args: Sequence[exp.Expression]) -> exp.Expression:
        return exp_class(this=seq_get(args, 2), expression=seq_get(args, 1), unit=seq_get(args, 0))

    return _builder


def _build_date_part(args: Sequence[exp.Expression]) -> exp.Expression:
    """``DATE_PART(part, expr)`` is the spec's alternative spelling of
    ``EXTRACT(part FROM expr)``; unify both into the same ``exp.Extract``
    node so callers see one canonical AST shape regardless of which
    surface syntax was used.

    ``DATE_PART`` takes its part name as a quoted string (``'year'``) while
    ``EXTRACT`` takes a bare keyword (``YEAR``); normalize to the latter so
    the merged AST always renders as valid ``EXTRACT(... FROM ...)`` syntax.
    """
    part = seq_get(args, 0)
    part_name = part.name if part is not None else ""
    return exp.Extract(this=exp.var(part_name.upper()), expression=seq_get(args, 1))


def _build_dpipe(
    *, this: exp.Expression | None = None, expression: exp.Expression | None = None
) -> exp.DPipe:
    """Give ``||`` the same precedence tier as binary ``+``/``-``.

    SQLGlot's default parser groups ``||`` with the bitwise operators, one
    tier looser than ``+``/``-``. The spec instead follows the common convention,
    which both put ``||`` at the *same* tier as ``+``/``-``
    (left-to-right, like the arithmetic operators around it) -- see
    ``_OssieParser.TERM`` below, which is what actually makes that happen;
    this just reproduces the ``safe=True`` default SQLGlot's own DPipe
    construction uses so behavior is otherwise unchanged.
    """
    return exp.DPipe(this=this, expression=expression, safe=True)


class _OssieTokenizer(Tokenizer):
    pass


class _OssieParser(Parser):
    FUNCTIONS = {
        **Parser.FUNCTIONS,
        "DATEADD": _build_date_delta(exp.DateAdd),
        "DATEDIFF": _build_date_delta(exp.DateDiff),
        "DATE_PART": _build_date_part,
        "APPROX_PERCENTILE": exp.ApproxQuantile.from_arg_list,
    }
    # SQLGlot infers Parser.TERM's type from its literal (dict[TokenType,
    # type[Binary]]), so mypy sees adding a plain builder function as an
    # incompatible override -- it isn't, at runtime _parse_term only ever
    # calls `klass(this=..., expression=...)` generically. Silence the two
    # resulting checks rather than fight SQLGlot's own inferred type.
    TERM: ClassVar[dict[TokenType, Callable[..., exp.Expression]]] = {  # type: ignore[assignment]
        **Parser.TERM,  # type: ignore[dict-item]
        TokenType.DPIPE: _build_dpipe,
    }


class _OssieGenerator(Generator):
    TYPE_MAPPING = {
        **Generator.TYPE_MAPPING,
        exp.DataType.Type.TIMESTAMPNTZ: "TIMESTAMP_NTZ",
    }

    def dateadd_sql(self, expression: exp.DateAdd) -> str:
        return self.func("DATEADD", unit_to_var(expression), expression.expression, expression.this)

    def datediff_sql(self, expression: exp.DateDiff) -> str:
        return self.func(
            "DATEDIFF", unit_to_var(expression), expression.expression, expression.this
        )

    def strposition_sql(self, expression: exp.StrPosition) -> str:
        this = self.sql(expression, "this")
        substr = self.sql(expression, "substr")
        return f"POSITION({substr} IN {this})"

    def startswith_sql(self, expression: exp.StartsWith) -> str:
        return self.func("STARTSWITH", expression.this, expression.expression)

    def endswith_sql(self, expression: exp.EndsWith) -> str:
        return self.func("ENDSWITH", expression.this, expression.expression)

    def approxdistinct_sql(self, expression: exp.ApproxDistinct) -> str:
        return self.func("APPROX_COUNT_DISTINCT", expression.this, expression.args.get("accuracy"))

    def variancepop_sql(self, expression: exp.VariancePop) -> str:
        # SQLGlot's own canonical spelling ("VARIANCE_POP") isn't a name the
        # spec recognizes at all; the spec only defines "VAR_POP".
        return self.func("VAR_POP", expression.this)

    def dayofyear_sql(self, expression: exp.DayOfYear) -> str:
        # Ditto: the spec defines "DAYOFYEAR", not SQLGlot's "DAY_OF_YEAR".
        return self.func("DAYOFYEAR", expression.this)

    def approxquantile_sql(self, expression: exp.ApproxQuantile) -> str:
        # The spec calls this "APPROX_PERCENTILE"; SQLGlot's canonical name
        # ("APPROX_QUANTILE") isn't a spelling the spec defines.
        return self.func("APPROX_PERCENTILE", expression.this, expression.args.get("quantile"))

    def not_sql(self, expression: exp.Not) -> str:
        # Render the compact `NOT IN`/`IS NOT` forms the spec documents,
        # instead of SQLGlot's generic `NOT (x IN (...))` / `NOT (x IS NULL)`.
        this = expression.this
        if isinstance(this, exp.In):
            return self.in_sql(this).replace(" IN ", " NOT IN ", 1)
        if isinstance(this, exp.Is):
            return self.binary(this, "IS NOT")
        return super().not_sql(expression)

    def tochar_sql(self, expression: exp.ToChar) -> str:
        return self.func("TO_CHAR", expression.this, expression.args.get("format"))

    def cast_sql(self, expression: exp.Cast, safe_prefix: str | None = None) -> str:
        # Prefer the compact typed-literal form (`DATE '...'`) for a string
        # literal cast to DATE/TIME/TIMESTAMP/TIMESTAMP_NTZ -- the spec's
        # primary documented construction syntax for these types.
        to = expression.to
        this = expression.this
        if (
            not safe_prefix
            and isinstance(this, exp.Literal)
            and this.is_string
            and to.is_type(
                exp.DataType.Type.DATE,
                exp.DataType.Type.TIME,
                exp.DataType.Type.TIMESTAMP,
                exp.DataType.Type.TIMESTAMPNTZ,
            )
        ):
            return f"{self.sql(to)} {self.sql(this)}"
        return super().cast_sql(expression, safe_prefix=safe_prefix)


class Ossie(Dialect):
    """The Ossie_SQL_2026 expression dialect."""

    Tokenizer = _OssieTokenizer
    Parser = _OssieParser
    Generator = _OssieGenerator


__all__ = ["Ossie"]
