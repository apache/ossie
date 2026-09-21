# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""A parser and SQL renderer for Sigma's spreadsheet-style formula language.

Sigma data model column and metric formulas look like ``Sum([Orders/Amount])`` or
``If([Status] = "closed", 1, 0)``. Sigma's formula language is not SQL, so — unlike
converters whose native expressions are already SQL (e.g. NVIDIA GSF, which parses
straight into sqlglot) — a real tokenizer and recursive-descent parser is needed to
get from formula text to a tree (:class:`FormulaNode`).

From there, though, this module does what the other converters do: it translates into
a **sqlglot expression tree** and lets sqlglot's generator emit the SQL. That means
identifier quoting, string escaping, and operator precedence/parenthesisation are the
library's job rather than hand-rolled string concatenation, and targeting a warehouse
dialect other than ANSI is a ``dialect=`` argument (see :func:`to_sql`) rather than a
second renderer. The reverse direction (:func:`sql_to_sigma_formula`) walks a sqlglot
tree back into formula text, so both directions share one intermediate representation.

Design principle (matching the rest of the Ossie converter ecosystem): never fail. A
formula that cannot be parsed, or that uses a function with no portable SQL
equivalent, is simply not translatable — callers fall back to carrying the original
Sigma formula text verbatim (see ``ossie_sigma.sigma_to_ossie``), rather than raising or
emitting an approximate/lossy translation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

import sqlglot
from sqlglot import expressions as exp


class FormulaParseError(Exception):
    """Raised internally when a formula cannot be parsed; callers should catch it."""


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnRef:
    """A ``[Column]`` or ``[Table/Column]`` reference."""

    table: Optional[str]
    column: str


@dataclass(frozen=True)
class Literal:
    """A string, number, or boolean literal."""

    value: Union[str, float, int, bool]
    kind: str  # "string" | "number" | "boolean"


@dataclass(frozen=True)
class FuncCall:
    """A function call, e.g. ``Sum(x)`` or nested ``If(IsNull([A]), 0, Sum([B]))``."""

    name: str
    args: tuple["FormulaNode", ...]


@dataclass(frozen=True)
class BinOp:
    """A binary operator expression, e.g. ``[A] + [B]`` or ``[A] & "x"``."""

    op: str
    left: "FormulaNode"
    right: "FormulaNode"


@dataclass(frozen=True)
class UnaryOp:
    """A unary operator expression, e.g. ``-[A]`` or ``NOT [A]``."""

    op: str
    operand: "FormulaNode"


FormulaNode = Union[ColumnRef, Literal, FuncCall, BinOp, UnaryOp]


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    |(?P<column>\[[^\[\]]+\])
    |(?P<string>"(?:[^"]|"")*")
    |(?P<number>\d+\.\d+|\d+)
    |(?P<le><=)|(?P<ge>>=)|(?P<ne><>)
    |(?P<lparen>\()|(?P<rparen>\))|(?P<comma>,)
    |(?P<op>[+\-*/&=<>^])
    |(?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)

_KEYWORDS = {"and", "or", "not", "true", "false"}


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str


def _tokenize(formula: str) -> list[_Token]:
    pos = 0
    tokens: list[_Token] = []
    while pos < len(formula):
        match = _TOKEN_RE.match(formula, pos)
        if not match or match.end() == pos:
            raise FormulaParseError(f"Unrecognized character at position {pos}: {formula[pos:pos + 20]!r}")
        pos = match.end()
        kind = match.lastgroup
        text = match.group()
        if kind == "ws":
            continue
        if kind == "ident" and text.lower() in _KEYWORDS:
            kind = text.lower()
        tokens.append(_Token(kind, text))
    return tokens


# --------------------------------------------------------------------------
# Recursive-descent / precedence-climbing parser
#
# Precedence (low to high): or -> and -> not -> comparison -> concat (&) ->
# additive (+ -) -> multiplicative (* /) -> power (^) -> unary (- +) -> primary
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Optional[_Token]:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, kind: str) -> _Token:
        token = self._peek()
        if token is None or token.kind != kind:
            raise FormulaParseError(f"Expected {kind!r} at position {self._pos}, got {token!r}")
        return self._advance()

    def parse(self) -> FormulaNode:
        node = self._parse_or()
        if self._peek() is not None:
            raise FormulaParseError(f"Unexpected trailing token {self._peek()!r}")
        return node

    def _parse_or(self) -> FormulaNode:
        node = self._parse_and()
        while (tok := self._peek()) and tok.kind == "or":
            self._advance()
            node = BinOp("OR", node, self._parse_and())
        return node

    def _parse_and(self) -> FormulaNode:
        node = self._parse_not()
        while (tok := self._peek()) and tok.kind == "and":
            self._advance()
            node = BinOp("AND", node, self._parse_not())
        return node

    def _parse_not(self) -> FormulaNode:
        if (tok := self._peek()) and tok.kind == "not":
            self._advance()
            return UnaryOp("NOT", self._parse_not())
        return self._parse_comparison()

    _COMPARISON_OPS = {"=", "<>", "<", "<=", ">", ">="}

    def _parse_comparison(self) -> FormulaNode:
        node = self._parse_concat()
        while (tok := self._peek()) and self._op_text(tok) in self._COMPARISON_OPS:
            op = self._op_text(self._advance())
            node = BinOp(op, node, self._parse_concat())
        return node

    @staticmethod
    def _op_text(tok: _Token) -> Optional[str]:
        if tok.kind in ("op", "le", "ge", "ne"):
            return tok.text
        return None

    def _parse_concat(self) -> FormulaNode:
        node = self._parse_additive()
        while (tok := self._peek()) and tok.kind == "op" and tok.text == "&":
            self._advance()
            node = BinOp("&", node, self._parse_additive())
        return node

    def _parse_additive(self) -> FormulaNode:
        node = self._parse_multiplicative()
        while (tok := self._peek()) and tok.kind == "op" and tok.text in ("+", "-"):
            op = self._advance().text
            node = BinOp(op, node, self._parse_multiplicative())
        return node

    def _parse_multiplicative(self) -> FormulaNode:
        node = self._parse_power()
        while (tok := self._peek()) and tok.kind == "op" and tok.text in ("*", "/"):
            op = self._advance().text
            node = BinOp(op, node, self._parse_power())
        return node

    def _parse_power(self) -> FormulaNode:
        node = self._parse_unary()
        if (tok := self._peek()) and tok.kind == "op" and tok.text == "^":
            self._advance()
            return BinOp("^", node, self._parse_power())
        return node

    def _parse_unary(self) -> FormulaNode:
        if (tok := self._peek()) and tok.kind == "op" and tok.text in ("-", "+"):
            op = self._advance().text
            return UnaryOp(op, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> FormulaNode:
        tok = self._peek()
        if tok is None:
            raise FormulaParseError("Unexpected end of formula")

        if tok.kind == "column":
            self._advance()
            inner = tok.text[1:-1]
            if "/" in inner:
                table, column = inner.split("/", 1)
                return ColumnRef(table, column)
            return ColumnRef(None, inner)

        if tok.kind == "string":
            self._advance()
            return Literal(tok.text[1:-1].replace('""', '"'), "string")

        if tok.kind == "number":
            self._advance()
            value: Union[int, float] = float(tok.text) if "." in tok.text else int(tok.text)
            return Literal(value, "number")

        if tok.kind in ("true", "false"):
            self._advance()
            return Literal(tok.kind == "true", "boolean")

        if tok.kind == "lparen":
            self._advance()
            node = self._parse_or()
            self._expect("rparen")
            return node

        if tok.kind == "ident":
            name = self._advance().text
            self._expect("lparen")
            args: list[FormulaNode] = []
            if not (self._peek() and self._peek().kind == "rparen"):
                args.append(self._parse_or())
                while self._peek() and self._peek().kind == "comma":
                    self._advance()
                    args.append(self._parse_or())
            self._expect("rparen")
            return FuncCall(name, tuple(args))

        raise FormulaParseError(f"Unexpected token {tok!r}")


def parse_formula(formula: str) -> FormulaNode:
    """Parse a Sigma formula string into a :class:`FormulaNode` AST.

    Raises :class:`FormulaParseError` on any formula this parser does not understand
    (e.g. functions/operators outside Sigma's grammar, or malformed input). Callers
    should treat that as "not translatable" rather than a hard failure.
    """
    tokens = _tokenize(formula.strip())
    if not tokens:
        raise FormulaParseError("Empty formula")
    return _Parser(tokens).parse()


def is_plain_column_ref(formula: str) -> Optional[ColumnRef]:
    """Return the :class:`ColumnRef` if *formula* is exactly a single bracket reference."""
    try:
        node = parse_formula(formula)
    except FormulaParseError:
        return None
    return node if isinstance(node, ColumnRef) else None


# --------------------------------------------------------------------------
# SQL rendering (Sigma AST -> sqlglot AST -> SQL text)
# --------------------------------------------------------------------------


class _NotTranslatable(Exception):
    pass


# Functions that map 1:1 onto a SQL function/aggregate of the same arity, keyed by
# lowercase Sigma name -> SQL name. ``exp.func`` resolves each name to sqlglot's typed
# node where one exists, so the generator can render it per target dialect.
_DIRECT_FUNCTIONS = {
    "sum": "SUM",
    "avg": "AVG",
    "average": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "upper": "UPPER",
    "lower": "LOWER",
    "trim": "TRIM",
    "abs": "ABS",
    "round": "ROUND",
    "ceiling": "CEIL",
    "floor": "FLOOR",
    "sqrt": "SQRT",
    "length": "LENGTH",
    "power": "POWER",
    "mod": "MOD",
    "coalesce": "COALESCE",
}

_EXTRACT_PARTS = {
    "year": "YEAR",
    "month": "MONTH",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
    "quarter": "QUARTER",
    "week": "WEEK",
    "dayofweek": "DOW",
}


_BINOP_NODES = {
    "+": exp.Add,
    "-": exp.Sub,
    "*": exp.Mul,
    "/": exp.Div,
    "&": exp.DPipe,
    "^": exp.Pow,
    "=": exp.EQ,
    "<>": exp.NEQ,
    "<": exp.LT,
    "<=": exp.LTE,
    ">": exp.GT,
    ">=": exp.GTE,
    "AND": exp.And,
    "OR": exp.Or,
}


# sqlglot's generator prints the tree it is given verbatim — it does not re-insert
# parentheses that the tree's shape implies — so a manually built tree has to carry
# its own ``exp.Paren`` nodes. Binding power per node type, lowest first; anything
# absent (function calls, CASE, literals, columns) is self-delimiting and never needs
# wrapping. Non-associative operators additionally parenthesise an equal-precedence
# right operand, so ``a - (b - c)`` does not collapse into ``a - b - c``.
_PRECEDENCE: tuple[tuple[tuple[type, ...], int], ...] = (
    ((exp.Or,), 1),
    ((exp.And,), 2),
    ((exp.Not,), 3),
    ((exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Is, exp.Like), 4),
    ((exp.DPipe,), 5),
    ((exp.Add, exp.Sub), 6),
    ((exp.Mul, exp.Div, exp.Mod), 7),
)
_NON_ASSOCIATIVE = (exp.Sub, exp.Div, exp.Mod, exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)


def _binding_power(node: exp.Expression) -> int:
    for types, power in _PRECEDENCE:
        if isinstance(node, types):
            return power
    return 100


def _maybe_paren(child: exp.Expression, parent_power: int, tighter: bool) -> exp.Expression:
    """Wrap *child* in parentheses when the parent operator binds at least as tightly.

    *tighter* asks for the strict comparison used on the right operand of a
    non-associative operator, where equal precedence still needs the parentheses.
    """
    power = _binding_power(child)
    if power < parent_power or (tighter and power == parent_power):
        return exp.Paren(this=child)
    return child


def _concat(*parts: exp.Expression) -> exp.Expression:
    node = parts[0]
    for part in parts[1:]:
        node = exp.DPipe(this=node, expression=part)
    return node


def _case(cond: exp.Expression, then: exp.Expression, otherwise: Optional[exp.Expression]) -> exp.Case:
    return exp.Case(ifs=[exp.If(this=cond, true=then)], default=otherwise)


def _within_group(percentile: exp.Expression, value: exp.Expression) -> exp.Expression:
    return exp.WithinGroup(
        this=exp.PercentileCont(this=percentile),
        expression=exp.Order(expressions=[value]),
    )


def _build_node(node: FormulaNode, dataset_alias: Optional[str]) -> exp.Expression:
    """Translate a Sigma AST node into an equivalent sqlglot expression node."""
    if isinstance(node, ColumnRef):
        column = exp.to_identifier(node.column, quoted=True)
        if node.table is not None and node.table != dataset_alias:
            return exp.column(column, exp.to_identifier(node.table, quoted=True))
        return exp.column(column)

    if isinstance(node, Literal):
        if node.kind == "string":
            return exp.Literal.string(str(node.value))
        if node.kind == "boolean":
            return exp.true() if node.value else exp.false()
        return exp.Literal.number(node.value)

    if isinstance(node, UnaryOp):
        inner = _build_node(node.operand, dataset_alias)
        if node.op == "NOT":
            return exp.Not(this=_maybe_paren(inner, _binding_power(exp.Not()), tighter=False))
        if node.op == "-":
            return exp.Neg(this=_maybe_paren(inner, 100, tighter=False))
        return inner  # unary plus is a no-op

    if isinstance(node, BinOp):
        builder = _BINOP_NODES.get(node.op)
        if builder is None:
            raise _NotTranslatable(f"No SQL mapping for operator {node.op!r}")
        # ``^`` and the boolean operators render as POWER(...)/AND/OR, which are either
        # self-delimiting or handled by the precedence table like any other operator.
        left = _build_node(node.left, dataset_alias)
        right = _build_node(node.right, dataset_alias)
        if builder is exp.Pow:
            return builder(this=left, expression=right)
        power = _binding_power(builder())
        return builder(
            this=_maybe_paren(left, power, tighter=False),
            expression=_maybe_paren(right, power, tighter=issubclass(builder, _NON_ASSOCIATIVE)),
        )

    if isinstance(node, FuncCall):
        return _build_call(node, dataset_alias)

    raise _NotTranslatable(f"Unknown node type: {node!r}")


def _build_string_slice(
    name: str, args: tuple[FormulaNode, ...], dataset_alias: Optional[str]
) -> exp.Expression:
    text = _build_node(args[0], dataset_alias)
    if name == "left" and len(args) == 2:
        return exp.Substring(this=text, start=exp.Literal.number(1), length=_build_node(args[1], dataset_alias))
    if name == "right" and len(args) == 2:
        length = _build_node(args[1], dataset_alias)
        start = exp.Add(
            this=exp.Sub(this=exp.func("LENGTH", text.copy()), expression=length.copy()),
            expression=exp.Literal.number(1),
        )
        return exp.Substring(this=text, start=start, length=length)
    if name in ("mid", "substring") and len(args) == 3:
        return exp.Substring(
            this=text,
            start=_build_node(args[1], dataset_alias),
            length=_build_node(args[2], dataset_alias),
        )
    if name in ("mid", "substring") and len(args) == 2:
        return exp.Substring(this=text, start=_build_node(args[1], dataset_alias))
    raise _NotTranslatable(f"Unsupported arity for {name}: {len(args)} args")


_DATE_PART_UNITS = frozenset(
    {"year", "quarter", "month", "week", "day", "hour", "minute", "second", "millisecond"}
)


def _date_part_unit(node: FormulaNode) -> exp.Expression:
    """Turn Sigma's trailing ``"day"``-style date-part argument into a SQL unit keyword."""
    if isinstance(node, Literal) and node.kind == "string":
        unit = str(node.value).lower().rstrip("s")
        if unit in _DATE_PART_UNITS:
            return exp.var(unit.upper())
    raise _NotTranslatable(f"Unsupported date part: {node!r}")


def _build_call(node: FuncCall, dataset_alias: Optional[str]) -> exp.Expression:
    name = node.name.lower()
    args = node.args
    built = [_build_node(a, dataset_alias) for a in args]

    if name == "countdistinct" and len(args) == 1:
        return exp.Count(this=exp.Distinct(expressions=[built[0]]))
    if name == "median" and len(args) == 1:
        return _within_group(exp.Literal.number(0.5), built[0])
    if name == "percentile" and len(args) == 2:
        return _within_group(built[1], built[0])
    if name in ("variance", "var") and len(args) == 1:
        return exp.func("VAR_SAMP", built[0])
    if name in ("stddev", "standarddeviation") and len(args) == 1:
        return exp.func("STDDEV_SAMP", built[0])

    if name == "if" and len(args) == 3:
        return _case(built[0], built[1], built[2])
    if name == "ifnull" and len(args) == 2:
        return exp.Coalesce(this=built[0], expressions=[built[1]])
    if name == "isnull" and len(args) == 1:
        return exp.Is(this=built[0], expression=exp.Null())
    if name == "isnotnull" and len(args) == 1:
        return exp.Not(this=exp.Is(this=built[0], expression=exp.Null()))

    if name == "sumif" and len(args) == 2:
        return exp.Sum(this=_case(built[0], built[1], exp.Literal.number(0)))
    if name == "countif" and len(args) == 1:
        return exp.Count(this=_case(built[0], exp.Literal.number(1), None))
    if name == "countdistinctif" and len(args) == 2:
        return exp.Count(this=exp.Distinct(expressions=[_case(built[0], built[1], None)]))
    if name == "averageif" and len(args) == 2:
        return exp.Avg(this=_case(built[0], built[1], None))

    if name in ("left", "right", "mid", "substring") and args:
        return _build_string_slice(name, args, dataset_alias)

    if name == "concat" and built:
        return _concat(*built)

    if name == "contains" and len(args) == 2:
        pattern = _concat(exp.Literal.string("%"), built[1], exp.Literal.string("%"))
        return exp.Like(this=built[0], expression=pattern)
    if name == "startswith" and len(args) == 2:
        return exp.Like(this=built[0], expression=_concat(built[1], exp.Literal.string("%")))
    if name == "endswith" and len(args) == 2:
        return exp.Like(this=built[0], expression=_concat(exp.Literal.string("%"), built[1]))
    if name == "replace" and len(args) == 3:
        return exp.func("REPLACE", *built)

    if name == "today" and not args:
        return exp.CurrentDate()
    if name == "now" and not args:
        return exp.CurrentTimestamp()
    if name == "null" and not args:
        return exp.Null()
    if name in _EXTRACT_PARTS and len(args) == 1:
        return exp.Extract(this=exp.var(_EXTRACT_PARTS[name]), expression=built[0])

    # Sigma passes the date part as a trailing string literal: DateAdd(d, n, "day"),
    # DateDiff(start, end, "day"). SQL wants it as an unquoted unit keyword.
    if name in ("dateadd", "datediff") and len(args) == 3:
        unit = _date_part_unit(args[2])
        if name == "dateadd":
            return exp.DateAdd(this=built[0], expression=built[1], unit=unit)
        return exp.DateDiff(this=built[1], expression=built[0], unit=unit)

    if name in _DIRECT_FUNCTIONS:
        return exp.func(_DIRECT_FUNCTIONS[name], *built)

    raise _NotTranslatable(f"No SQL mapping for Sigma function {node.name!r}")


_REVERSE_AGG_FUNCTIONS = {
    "SUM": "Sum",
    "AVG": "Avg",
    "MIN": "Min",
    "MAX": "Max",
    "UPPER": "Upper",
    "LOWER": "Lower",
    "TRIM": "Trim",
    "ABS": "Abs",
    "ROUND": "Round",
    "CEIL": "Ceiling",
    "FLOOR": "Floor",
    "SQRT": "Sqrt",
    "COALESCE": "IfNull",
    "POWER": "Power",
}

_REVERSE_EXTRACT_PARTS = {v: k.capitalize() for k, v in _EXTRACT_PARTS.items()}


def sql_to_sigma_formula(sql: str, dataset_alias: Optional[str] = None) -> Optional[str]:
    """Best-effort reverse translation of an ANSI SQL expression into Sigma formula syntax.

    Used only for fields/metrics that did not originate in Sigma (i.e. carry no
    ``SIGMA``-dialect expression to reuse verbatim). Returns ``None`` if *sql* cannot
    be parsed, or uses a SQL construct with no Sigma formula-language equivalent —
    callers should treat that as "not translatable", not fail the conversion.
    """
    try:
        tree = sqlglot.parse_one(sql)
    except Exception:  # noqa: BLE001 - sqlglot raises several internal error types
        return None

    try:
        return _render_sql_node(tree, dataset_alias)
    except _NotTranslatable:
        return None


def _render_sql_node(node: exp.Expression, dataset_alias: Optional[str]) -> str:
    if isinstance(node, exp.Column):
        parts = [p.name for p in node.parts]
        if len(parts) == 2:
            table, column = parts
            if table == dataset_alias:
                return f"[{column}]"
            return f"[{table}/{column}]"
        return f"[{parts[-1]}]"

    if isinstance(node, exp.Paren):
        return _render_sql_node(node.this, dataset_alias)

    if isinstance(node, exp.Literal):
        if node.is_string:
            return '"' + node.this.replace('"', '""') + '"'
        return node.this

    if isinstance(node, exp.Boolean):
        return "TRUE" if node.this else "FALSE"

    if isinstance(node, exp.Count):
        inner = node.this
        if isinstance(inner, exp.Distinct) and len(inner.expressions) == 1:
            return f"CountDistinct({_render_sql_node(inner.expressions[0], dataset_alias)})"
        if isinstance(inner, exp.Star):
            raise _NotTranslatable("COUNT(*) has no unambiguous Sigma column-based equivalent")
        return f"Count({_render_sql_node(inner, dataset_alias)})"

    if isinstance(node, exp.Div):
        left = _render_sql_node(node.this, dataset_alias)
        right = _render_sql_node(node.expression, dataset_alias)
        return f"({left} / {right})"
    if isinstance(node, exp.Mul):
        return f"({_render_sql_node(node.this, dataset_alias)} * {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.Add):
        return f"({_render_sql_node(node.this, dataset_alias)} + {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.Sub):
        return f"({_render_sql_node(node.this, dataset_alias)} - {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.DPipe) or isinstance(node, exp.Concat):
        parts = node.flatten() if hasattr(node, "flatten") else [node.this, node.expression]
        return " & ".join(_render_sql_node(p, dataset_alias) for p in parts)

    if isinstance(node, exp.EQ):
        return f"({_render_sql_node(node.this, dataset_alias)} = {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.NEQ):
        return f"({_render_sql_node(node.this, dataset_alias)} <> {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.GT):
        return f"({_render_sql_node(node.this, dataset_alias)} > {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.GTE):
        return f"({_render_sql_node(node.this, dataset_alias)} >= {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.LT):
        return f"({_render_sql_node(node.this, dataset_alias)} < {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.LTE):
        return f"({_render_sql_node(node.this, dataset_alias)} <= {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.And):
        return f"({_render_sql_node(node.this, dataset_alias)} AND {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.Or):
        return f"({_render_sql_node(node.this, dataset_alias)} OR {_render_sql_node(node.expression, dataset_alias)})"
    if isinstance(node, exp.Not):
        return f"NOT ({_render_sql_node(node.this, dataset_alias)})"

    if isinstance(node, exp.Is):
        inner = _render_sql_node(node.this, dataset_alias)
        if isinstance(node.expression, exp.Null):
            return f"IsNull({inner})"
        raise _NotTranslatable("IS <non-null> has no Sigma equivalent")

    if isinstance(node, exp.Case):
        ifs = node.args.get("ifs", [])
        default = node.args.get("default")
        if len(ifs) == 1 and default is not None:
            cond = _render_sql_node(ifs[0].this, dataset_alias)
            then = _render_sql_node(ifs[0].args["true"], dataset_alias)
            otherwise = _render_sql_node(default, dataset_alias)
            return f"If({cond}, {then}, {otherwise})"
        raise _NotTranslatable("Multi-branch CASE has no single Sigma If() equivalent")

    if isinstance(node, exp.Coalesce) and len(node.expressions) == 1:
        return f"IfNull({_render_sql_node(node.this, dataset_alias)}, {_render_sql_node(node.expressions[0], dataset_alias)})"

    if isinstance(node, exp.CurrentDate):
        return "Today()"
    if isinstance(node, exp.CurrentTimestamp):
        return "Now()"

    if isinstance(node, exp.Extract):
        part = node.this.name.upper() if hasattr(node.this, "name") else str(node.this).upper()
        sigma_part = _REVERSE_EXTRACT_PARTS.get(part)
        if sigma_part is not None:
            return f"{sigma_part}({_render_sql_node(node.expression, dataset_alias)})"
        raise _NotTranslatable(f"Unsupported EXTRACT part: {part}")

    func_name = node.__class__.__name__.upper()
    if func_name in _REVERSE_AGG_FUNCTIONS and hasattr(node, "this"):
        sigma_name = _REVERSE_AGG_FUNCTIONS[func_name]
        args = [node.this] + list(getattr(node, "expressions", []) or [])
        rendered = [_render_sql_node(a, dataset_alias) for a in args if a is not None]
        return f"{sigma_name}({', '.join(rendered)})"

    raise _NotTranslatable(f"No Sigma formula equivalent for SQL node {node.__class__.__name__}")


def to_sqlglot(node: FormulaNode, dataset_alias: Optional[str] = None) -> Optional[exp.Expression]:
    """Translate *node* into a sqlglot expression tree, or ``None`` if untranslatable."""
    try:
        return _build_node(node, dataset_alias)
    except _NotTranslatable:
        return None


def to_sql(node: FormulaNode, dataset_alias: Optional[str] = None, dialect: str = "") -> Optional[str]:
    """Render *node* as SQL in *dialect* (sqlglot's ANSI-closest generator by default),
    or return ``None`` if it uses a construct with no portable SQL equivalent (e.g. a
    table-calculation function like ``RunningSum`` that depends on UI-configured
    partition/order context Sigma does not pass as formula arguments).

    *dataset_alias* is the name of the dataset the expression is being rendered for;
    column references qualified with that same table name are rendered unqualified
    (since the expression lives inside that dataset's own scope), while references
    to any other table are rendered as ``"other_table"."column"``.
    """
    tree = to_sqlglot(node, dataset_alias)
    return None if tree is None else tree.sql(dialect=dialect)


def to_ansi_sql(node: FormulaNode, dataset_alias: Optional[str] = None) -> Optional[str]:
    """Render *node* as ANSI SQL. See :func:`to_sql`."""
    return to_sql(node, dataset_alias=dataset_alias)
