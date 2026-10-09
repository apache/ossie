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

"""Rendering an Ossie SQL expression as AQL.

An AQL expression can name fields from several models. A `@sql` expression can
name only fields of the model it sits in, and a dataset-level AML `metric`
accepts only `@aql`. So an Ossie metric naming two datasets has no AML home
unless its Ossie SQL becomes AQL.

What this translates is a name map and nothing else. `FUNCTIONS` pairs a sqlglot
node with the AQL function of the same meaning, references are already
`dataset.field` on both sides, and the operators match. Anything outside the
table raises `Untranslatable`, so the caller reports it rather than guessing.

Window functions are the deliberate omission. Even though AQL has `rank`,
`previous`, `window_sum` and the rest, this converter cannot output one.

Ossie SQL carries the partition and the ordering in the `OVER` clause, while AQL takes
both from the grain of the query being run. Translating one into the other means
deciding which query grain a given `OVER` clause corresponds to. The Ossie
specification says nothing about how a window function should behave when
queried, so nothing says which grain that is, and picking one would change what
the metric computes.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import sqlrefs

if sqlrefs.AVAILABLE:  # pragma: no branch - exercised by the packaging test
    import sqlglot
    from sqlglot import exp


#: The AQL cheatsheet, whose Window Function section lists `rank`, `previous`,
#: `next`, `window_sum` and the rest.
WINDOW_FUNCTION_DOCS = "https://docs.holistics.io/reference/aql/functions"

#: One window function in full, showing that the grain comes from the query.
RANK_DOCS = "https://docs.holistics.io/reference/aql/rank"

#: AQL's raw-SQL escape hatch, `sql_number(func_name, arg1, arg2, ...)`. The
#: family is typed by return value, and a metric body is numeric.
SQL_PASSTHROUGH = "sql_number"


@dataclass(frozen=True)
class _Context:
    """What every `_render` call needs: the source text and the caller's hooks."""

    source: str
    functions: dict
    reference: object
    numeric: set


class Untranslatable(Exception):
    """Raised when this converter will not write an expression as AQL.

    The expression may well have an AQL form. A window function does. This says
    the converter did not write one, not that AQL cannot express it.
    """


def _functions():
    """sqlglot node to the AQL function of the same meaning and argument order.

    Built lazily because the keys are sqlglot classes, and sqlglot is imported
    at module scope only when it is installed.

    Membership was derived by parsing each name AQL registers in
    `src/checker/row_level.ts` and reading which node sqlglot produced, rather
    than by matching names by eye. A name outside it falls to `_from_source`,
    which rebuilds the call from the source text when the offsets allow it:

    A name that is one sqlglot node with several spellings. `LPAD` and `RPAD`
    are both `Pad`, `LTRIM` and `RTRIM` are both `Trim`. The node's offset
    points at the name in the source, so the passthrough recovers it.

    A name whose arguments sqlglot rewrote. `LOG10(x)` gains a base the source
    never had, and `DATE_TRUNC('month', d)` has its unit normalized. Neither
    argument carries an offset, so neither call can be rebuilt faithfully.

    A name whose arguments mean something else in AQL. Ossie SQL `SUBSTRING(s, start,
    len)` and `STRPOS(s, sub)` do not line up with AQL's `mid` and `find`, so
    the passthrough carries them under their own names instead.
    """
    return {
        # Aggregates.
        exp.Sum: "sum",
        exp.Avg: "avg",
        exp.Min: "min",
        exp.Max: "max",
        exp.Stddev: "stdev",
        exp.StddevPop: "stdevp",
        exp.Variance: "var",
        exp.VariancePop: "varp",
        # Numeric.
        exp.Abs: "abs",
        exp.Ceil: "ceil",
        exp.Floor: "floor",
        exp.Round: "round",
        exp.Trunc: "trunc",
        exp.Sqrt: "sqrt",
        exp.Pow: "pow",
        exp.Exp: "exp",
        exp.Ln: "ln",
        exp.Mod: "mod",
        exp.Sign: "sign",
        exp.SafeDivide: "safe_divide",
        # Trigonometric.
        exp.Acos: "acos",
        exp.Asin: "asin",
        exp.Atan: "atan",
        exp.Atan2: "atan2",
        exp.Cos: "cos",
        exp.Cot: "cot",
        exp.Sin: "sin",
        exp.Tan: "tan",
        exp.Degrees: "degrees",
        exp.Radians: "radians",
        # Text.
        exp.Concat: "concat",
        exp.Lower: "lower",
        exp.Upper: "upper",
        exp.Length: "len",
        exp.Left: "left",
        exp.Right: "right",
        exp.Replace: "replace",
        exp.SplitPart: "split_part",
        exp.RegexpExtract: "regexp_extract",
        exp.RegexpLike: "regexp_like",
        exp.RegexpReplace: "regexp_replace",
        # Date parts, which take one date argument and so carry no order risk.
        exp.Year: "year",
        exp.Quarter: "quarter",
        exp.Month: "month",
        exp.Week: "week",
        exp.Day: "day",
        exp.Hour: "hour",
        exp.Minute: "minute",
        exp.LastDay: "last_day",
        # Conditional.
        exp.Nullif: "nullif",
        exp.Coalesce: "coalesce",
    }


#: Aggregates whose argument must be numeric. A reference inside one of these
#: tells the caller the field's type, which matters when the Ossie document
#: declares no `datatype`: AQL answers `sum` over a text dimension with
#: "The `sum` function expects `Dimension(Number)`, but got `Text`".
def _numeric_aggregates():
    return (exp.Sum, exp.Avg, exp.Stddev, exp.StddevPop, exp.Variance, exp.VariancePop)


#: Ossie SQL binary operators that are spelled the same in AQL.
OPERATORS = {
    "Add": "+",
    "Sub": "-",
    "Mul": "*",
    "Div": "/",
    "EQ": "==",
    "NEQ": "!=",
    "GT": ">",
    "GTE": ">=",
    "LT": "<",
    "LTE": "<=",
    "And": "and",
    "Or": "or",
}


def translate(expression: str, dialect: str, reference) -> tuple[str, set[tuple[str, str]]]:
    """`expression` as AQL, with the columns it aggregates numerically.

    `reference(table, column)` returns the AQL spelling of one column, so the
    caller owns the mapping from Ossie dataset names to AML model names. Raises
    `Untranslatable` naming what stopped it.
    """
    if not sqlrefs.AVAILABLE:
        raise Untranslatable("sqlglot is not installed")
    try:
        tree = sqlglot.parse_one(expression, read=sqlrefs.SQLGLOT_DIALECT.get(dialect))
    except Exception as exc:  # noqa: BLE001 - sqlglot raises several error types
        raise Untranslatable(f"sqlglot cannot read it as {dialect}. {exc}") from exc
    numeric: set[tuple[str, str]] = set()
    context = _Context(source=expression, functions=_functions(), reference=reference, numeric=numeric)
    return _render(tree, context), numeric


def _render(node, ctx) -> str:
    if isinstance(node, exp.Window):
        raise Untranslatable(
            "It is a window function. Even though AQL has window functions, this converter "
            "cannot output one. Ossie SQL carries the partition and the ordering in the OVER "
            "clause, "
            "while AQL takes both from the grain of the query being run. Translating one "
            "into the other means deciding which query grain this OVER clause corresponds "
            "to, and the Ossie specification defines no semantics for how a window function "
            "behaves when queried, so nothing says which grain that is. See "
            f"{WINDOW_FUNCTION_DOCS} for AQL's window functions and {RANK_DOCS} for one in "
            "full, then write the AQL by hand."
        )
    if isinstance(node, exp.Column):
        return ctx.reference(node.table, node.name)
    if isinstance(node, exp.Literal):
        return node.sql()
    if isinstance(node, exp.Boolean):
        return "true" if node.this else "false"
    if isinstance(node, exp.Null):
        return "null"
    if isinstance(node, exp.Paren):
        return f"({_render(node.this, ctx)})"
    if isinstance(node, exp.Neg):
        return f"-{_render(node.this, ctx)}"
    if isinstance(node, exp.Count):
        # sqlglot holds the DISTINCT as a child node rather than a flag, so
        # `COUNT(DISTINCT x)` parses to Count(this=Distinct(expressions=[x])).
        name = "count_distinct" if isinstance(node.this, exp.Distinct) else "count"
        return f"{name}({', '.join(_arguments(node, ctx))})"
    if isinstance(node, exp.Distinct):
        inner = node.expressions
        if len(inner) != 1:
            raise Untranslatable(
            "It applies DISTINCT to more than one expression, which this converter does "
            "not write as AQL."
        )
        return _render(inner[0], ctx)

    for kind, aql_name in ctx.functions.items():
        if isinstance(node, kind):
            if isinstance(node, _numeric_aggregates()):
                ctx.numeric.update(
                    (c.table, c.name) for c in node.find_all(exp.Column)
                )
            return f"{aql_name}({', '.join(_arguments(node, ctx))})"

    operator = OPERATORS.get(type(node).__name__)
    if operator and node.this is not None and node.args.get("expression") is not None:
        left = _render(node.this, ctx)
        right = _render(node.args["expression"], ctx)
        return f"{left} {operator} {right}"

    if isinstance(node, exp.Anonymous):
        # AQL's escape hatch for a warehouse function it has no name of its own
        # for: `sql_number('APPROX_TOP_COUNT', x, 3)` calls it with AQL-resolved
        # arguments. The family is typed by return value, and a metric body is
        # numeric, so `sql_number` is the member to use.
        #
        # Only `Anonymous` qualifies, because only there do the name and the
        # argument order come from the source text. A function sqlglot models
        # carries sqlglot's own name and slot order instead, which produced
        # `sql_number('PAD', s, 5)` for `LPAD(s, 5)` and
        # `sql_number('LOG', 10, x)` for `LOG10(x)`. Neither is a call any
        # warehouse answers.
        arguments = [_render(a, ctx) for a in node.expressions]
        return f"{SQL_PASSTHROUGH}('{node.name}'{''.join(', ' + a for a in arguments)})"

    if isinstance(node, exp.Func):
        rebuilt = _from_source(node, ctx)
        if rebuilt is not None:
            return rebuilt
        raise Untranslatable(
            f"{node.sql_name()} is not in this converter's AQL function table, and its call "
            f"cannot be read back from the source text, so there is no faithful way to pass "
            f"it through."
        )

    raise Untranslatable(
        f"It contains a {type(node).__name__}, which this converter does not write as AQL."
    )


def _span(node) -> int | None:
    """Where `node` starts in the source, or None when sqlglot made it up.

    sqlglot records an offset for a token it read. An argument it synthesized,
    such as the `10` in `LOG10(x)`, or normalized, such as `'month'` becoming
    `'MONTH'` in `DATE_TRUNC`, carries none.
    """
    meta = node.meta
    if not meta and isinstance(getattr(node, "this", None), exp.Expression):
        meta = node.this.meta
    return meta.get("start")


def _from_source(node, ctx) -> str | None:
    """A function sqlglot models, passed through under the name the source used.

    sqlglot names a node after the shape it parsed, not after the text. `LPAD`
    and `RPAD` are both `Pad`, `LTRIM` and `RTRIM` are both `Trim`, so rendering
    from the node loses which one was written. The node's own offset points at
    the name token in the source, so the original name is still there to read.

    Returns None when the call cannot be rebuilt faithfully, which happens when
    sqlglot synthesized or normalized an argument and so left it with no offset.
    `LOG10(x)` gains a base argument the source never had, and
    `DATE_TRUNC('month', d)` has its unit rewritten to `'MONTH'`. Dropping the
    first is right and dropping the second is not, and an absent offset does not
    say which case it is, so neither is passed through.
    """
    start = node.meta.get("start")
    if start is None:
        return None
    opening = ctx.source.find("(", start)
    if opening < 0:
        return None
    name = ctx.source[start:opening].strip()
    if not name.replace("_", "").isalnum():
        return None

    arguments = [
        value
        for slot in node.args.values()
        for value in (slot if isinstance(slot, list) else [slot])
        if isinstance(value, exp.Expression)
    ]
    offsets = [_span(argument) for argument in arguments]
    if any(offset is None for offset in offsets):
        return None

    in_source_order = [a for _, a in sorted(zip(offsets, arguments), key=lambda pair: pair[0])]
    rendered = [_render(argument, ctx) for argument in in_source_order]
    return f"{SQL_PASSTHROUGH}('{name}'{''.join(', ' + r for r in rendered)})"


def _arguments(node, ctx) -> list[str]:
    """Every argument of a function node, in the order sqlglot declares them.

    Each node names its own slots, and they are not the same from one function
    to the next: `NULLIF` uses `this` and `expression`, `COALESCE` uses `this`
    and `expressions`, `ROUND` uses `this` and `decimals`. Reading a fixed set
    of slot names drops the rest without saying so, which is how
    `ROUND(SUM(x), 2)` first rendered as `sql_number('ROUND', sum(x))`.
    `arg_types` gives the declared order, so no slot is missed.
    """
    rendered = []
    for slot in getattr(node, "arg_types", {"this": True}):
        value = node.args.get(slot)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, exp.Expression):
                rendered.append(_render(item, ctx))
    return rendered
