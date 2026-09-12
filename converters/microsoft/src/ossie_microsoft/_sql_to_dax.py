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

"""A deliberately narrow SQL-to-DAX translator for Apache Ossie expressions.

Apache Ossie fields and metrics can carry SQL expressions; Power BI evaluates
calculated columns and measures only as DAX. Rather than attempt a general translation,
this module recognises a closed set of shapes whose DAX equivalent is unambiguous, and
refuses everything else.

The governing rule is the one stated in `_common`: never emit a plausible-looking
expression that was not authored for the target engine. A metric this module declines
is reported and skipped, which a modeller notices; a metric it mistranslates would
deploy cleanly and quietly return a wrong number.

What is translated
------------------
A single aggregate call over one column, `COUNT(*)`, division between supported
aggregates, division by ``NULLIF(aggregate, 0)``, and string concatenations made only
from columns and string literals. Concretely::

    SUM(amount)            ->  SUM('Sales'[Amount])
    COUNT(DISTINCT cust)   ->  DISTINCTCOUNTNOBLANK('Sales'[Customer])
    COUNT(*)               ->  COUNTROWS('Sales')
    SUM(s.sales) / COUNT(*) -> DIVIDE(SUM('s'[sales]), COUNTROWS('s'))
    first || ' ' || last   ->  'Customer'[First] & " " & 'Customer'[Last]

What is refused
---------------
Everything else, including non-division arithmetic, aggregates over expressions
(`SUM(a + b)`), `CASE`, `DISTINCT` outside `COUNT`, `FILTER`/`OVER` clauses, and any
column that cannot be resolved to exactly one dataset. Ambiguity is treated as failure,
never as a guess.

Why the column must resolve
---------------------------
DAX has no bare column reference: `SUM(amount)` is not valid DAX, only
`SUM('Sales'[Amount])` is. The physical name in the SQL (`amount`) is also not
necessarily the Power BI column name -- it is the field's `sourceColumn`, while DAX
addresses the column by its model `name`. So a translation is only possible when the
column resolves to exactly one field in exactly one dataset.

Differences that are left in place
----------------------------------
Two divergences from SQL are deliberate, because both fail *visibly* rather than
returning a quietly wrong number:

* SQL `COUNT` returns 0 over an empty set, whereas the DAX count functions return
  BLANK. Coercing to 0 would need a wrapper on every measure and would defeat Power
  BI's convention of hiding empty rows in a visual, so BLANK is kept.
* DAX `STDEV.S`, `STDEV.P`, `VAR.S` and `VAR.P` raise an error when fewer than two
  non-blank rows remain, where SQL yields NULL (sample) or 0 (population). That
  surfaces as a visible error rather than a plausible wrong figure.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from ._common import DIALECT_ANSI

#: Apache Ossie dialects this module can parse, mapped to their sqlglot reader.
#: Dialects absent from this map (`MDX`, `TABLEAU`, `MAQL`) are not SQL and are refused.
READABLE_DIALECTS = {
    DIALECT_ANSI: None,  # sqlglot's dialect-neutral reader
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
}

#: Aggregates whose DAX equivalent takes a single column and means the same thing.
#: Cross-checked against `core-spec/expression_language.md`; every emitted form has
#: been verified to parse against the Power BI DAX grammar.
#:
#: Two entries are not the same-named DAX function, because DAX treats BLANK
#: differently from SQL's NULL:
#:
#: * SQL `COUNT(x)` counts every non-NULL value whatever its type, but DAX `COUNT`
#:   documents `TRUE`/`FALSE` values as unsupported. `COUNTA` counts non-blank values
#:   of any type, so it -- not `COUNT` -- is the equivalent for an arbitrary column.
#: * SQL `COUNT(DISTINCT x)` excludes NULL, but DAX `DISTINCTCOUNT` counts BLANK as a
#:   distinct value, which is off by one on any nullable column. `DISTINCTCOUNTNOBLANK`
#:   is the equivalent.
_AGGREGATES = {
    exp.Sum: "SUM",
    exp.Min: "MIN",
    exp.Max: "MAX",
    exp.Count: "COUNTA",
    exp.Avg: "AVERAGE",
    exp.Median: "MEDIAN",
    exp.Stddev: "STDEV.S",
    exp.StddevSamp: "STDEV.S",
    exp.StddevPop: "STDEV.P",
    exp.Variance: "VAR.S",
    exp.VariancePop: "VAR.P",
}


def quote_table(name):
    """Quote a DAX table reference.

    Single quotes are always accepted, including around a name that would not need
    them, so quoting unconditionally avoids a rule that could be applied wrongly.
    """
    escaped = name.replace("'", "''")
    return f"'{escaped}'"


def quote_column(name):
    """Quote a DAX column reference. `]` is escaped by doubling."""
    escaped = name.replace("]", "]]")
    return f"[{escaped}]"


def quote_string(value):
    """Quote a DAX string literal. Double quotes are escaped by doubling."""
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _parse(expression, dialect):
    if dialect not in READABLE_DIALECTS:
        return None, f"'{dialect}' is not a SQL dialect this converter can parse"

    try:
        tree = sqlglot.parse_one(sql=expression, read=READABLE_DIALECTS[dialect])
    except Exception:
        return None, "the expression could not be parsed as SQL"

    return tree.this if isinstance(tree, exp.Alias) else tree, None


def _resolve_column(column, resolve_column):
    if column.args.get("db") or column.args.get("catalog"):
        return None
    key = f"{column.table}.{column.name}" if column.table else column.name
    return resolve_column(key)


def _translate_concatenation(tree, resolve_column):
    if isinstance(tree, exp.DPipe):
        left, reason = _translate_concatenation(tree.this, resolve_column)
        if left is None:
            return None, reason
        right, reason = _translate_concatenation(tree.expression, resolve_column)
        if right is None:
            return None, reason
        return f"{left} & {right}", None

    if isinstance(tree, exp.Literal) and tree.is_string:
        return quote_string(tree.this), None

    if not isinstance(tree, exp.Column):
        return None, "a concatenation may contain only columns and string literals"
    resolved = _resolve_column(tree, resolve_column)
    if resolved is None:
        return None, f"column '{tree.sql()}' does not resolve to exactly one dataset field"
    table, column = resolved
    return f"{quote_table(table)}{quote_column(column)}", None


def translate_concatenation(expression, dialect, resolve_column):
    """Translate a SQL string concatenation for a calculated column."""
    tree, reason = _parse(expression, dialect)
    if tree is None:
        return None, reason
    if not isinstance(tree, exp.DPipe):
        return None, "only string concatenation is translated for a calculated column"
    return _translate_concatenation(tree, resolve_column)


def _translate_aggregate(tree, resolve_column, resolve_table):
    dax_function = _AGGREGATES.get(type(tree))
    if dax_function is None:
        return None, "only supported aggregate expressions can be used in a metric"

    if tree.args.get("expressions"):
        return None, "an aggregate over multiple arguments has no DAX equivalent"

    argument = tree.this
    distinct = False
    if isinstance(argument, exp.Distinct):
        operands = argument.args.get("expressions") or []
        if len(operands) != 1:
            return None, "'DISTINCT' over multiple columns has no DAX equivalent"
        distinct = True
        argument = operands[0]

    if isinstance(tree, exp.Count):
        if isinstance(argument, exp.Star):
            if distinct:
                return None, "'COUNT(DISTINCT *)' has no DAX equivalent"
            table = resolve_table()
            if table is None:
                return None, "'COUNT(*)' needs exactly one dataset to count rows of"
            return f"COUNTROWS({quote_table(table)})", None
        dax_function = "DISTINCTCOUNTNOBLANK" if distinct else "COUNTA"
    elif distinct:
        return None, f"'DISTINCT' inside '{dax_function}' has no DAX equivalent"

    if not isinstance(argument, exp.Column):
        return None, "only an aggregate over a single column reference is translated"

    resolved = _resolve_column(argument, resolve_column)
    if resolved is None:
        return None, f"column '{argument.sql()}' does not resolve to exactly one dataset field"
    table, column = resolved
    return f"{dax_function}({quote_table(table)}{quote_column(column)})", None


def _translate_division(tree, resolve_column, resolve_table):
    denominator = tree.expression
    if isinstance(denominator, exp.Nullif):
        zero = denominator.expression
        if not isinstance(zero, exp.Literal) or zero.is_string or zero.this != "0":
            return None, "only NULLIF(aggregate, 0) is translated in a denominator"
        denominator = denominator.this

    numerator_dax, reason = _translate_aggregate(
        tree.this, resolve_column, resolve_table
    )
    if numerator_dax is None:
        return None, reason
    denominator_dax, reason = _translate_aggregate(
        denominator, resolve_column, resolve_table
    )
    if denominator_dax is None:
        return None, reason
    return f"DIVIDE({numerator_dax}, {denominator_dax})", None


def translate(expression, dialect, resolve_column, resolve_table):
    """Translate a supported SQL expression to DAX, or return ``(None, reason)``.

    ``resolve_column`` takes a SQL identifier and returns ``(table, column)`` naming
    the Power BI table and column, or ``None`` when the name is unknown or ambiguous.
    ``resolve_table`` returns the sole table name for a ``COUNT(*)``, or ``None``.

    Returns ``(dax, None)`` on success and ``(None, reason)`` otherwise, where
    ``reason`` explains the refusal in terms a modeller can act on.
    """
    tree, reason = _parse(expression, dialect)
    if tree is None:
        return None, reason

    if isinstance(tree, exp.DPipe):
        return _translate_concatenation(tree, resolve_column)

    if isinstance(tree, exp.Div):
        return _translate_division(tree, resolve_column, resolve_table)

    if type(tree) not in _AGGREGATES:
        return None, (
            "only a single aggregate over one column translates unambiguously to DAX"
        )
    return _translate_aggregate(tree, resolve_column, resolve_table)
