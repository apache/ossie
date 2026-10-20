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

"""Finding the column references in an Ossie SQL expression.

The reverse path has to turn `CAST(created_at AS DATE)` back into
`CAST({{ #SOURCE.created_at }} AS DATE)`. A bare column name does survive
`holistics aml validate`, but it is not equivalent: AQL reads the interpolation
to learn which field a body depends on, and a body with none loses the query
rewriting and the optimisation that depend on that link. So the reference has to
be restored, which means telling a column apart from a keyword, a function name
and a date part.

sqlglot already makes that distinction, and it reports the character offsets of
the identifiers it parsed. In BigQuery's `DATE_TRUNC(created_at, month)` it
reads `created_at` as a column and `month` as a date part, and in
`CASE WHEN status = 'created_at' THEN created_at END` it reads the second
`created_at` and not the one inside the string literal.

`rewrite` uses those offsets to splice replacement text into the original string.
The SQL is never re-rendered through sqlglot. `DATE_TRUNC(created_at, month)`
written back out through sqlglot's default dialect returns
`DATE_TRUNC(created_at, MONTH)`, which that same default dialect then re-reads
with the unit and the expression exchanged, so a round trip through the writer
silently changes the meaning.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError, TokenError

    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the packaging test
    AVAILABLE = False

#: Ossie dialect to the sqlglot dialect that parses it. Mirrors `DIALECT_MAP` in
#: `validation/validate.py`. None means sqlglot's default.
SQLGLOT_DIALECT: dict[str, str | None] = {
    "ANSI_SQL": None,
    "OSSIE_SQL_2026": None,
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
}

#: Dialects sqlglot cannot parse. A body in one of these is not SQL.
NOT_SQL = frozenset({"MDX", "TABLEAU", "MAQL", "SIGMA", "THOUGHTSPOT", "DAX", "HOLISTICS_AQL"})

#: Every expression is parsed as `SELECT <expression>`, because a bare column
#: name is not a statement on its own. Offsets shift back by this much.
_PREFIX = "SELECT "


class UnparseableSQL(Exception):
    """Raised when sqlglot cannot read an expression at the dialect given."""


@dataclass(frozen=True)
class ColumnReference:
    """One column in the expression, with the span it occupies in the text."""

    table: str
    name: str
    start: int
    #: Exclusive, so `text[start:end]` is the reference as written.
    end: int


def columns(expression: str, dialect: str) -> list[ColumnReference]:
    """Every column reference in `expression`, in the order it appears."""
    if not AVAILABLE:
        raise UnparseableSQL("sqlglot is not installed")
    try:
        tree = sqlglot.parse_one(_PREFIX + expression, read=SQLGLOT_DIALECT.get(dialect))
    except (ParseError, TokenError, RecursionError) as exc:
        raise UnparseableSQL(str(exc).split("\n")[0]) from exc

    found = []
    for column in tree.find_all(exp.Column):
        name_meta = column.this.meta
        table = column.args.get("table")
        if "start" not in name_meta:
            continue
        start = table.meta["start"] if table is not None and "start" in table.meta else name_meta["start"]
        found.append(
            ColumnReference(
                table=column.table,
                name=column.name,
                start=start - len(_PREFIX),
                end=name_meta["end"] + 1 - len(_PREFIX),
            )
        )
    return sorted(found, key=lambda c: c.start)


def transpile(expression: str, read: str, write: str) -> str:
    """`expression` read as `read` and written as `write`.

    Used only to render a portable body into the warehouse the AML output is
    aimed at. Both dialects are named members of `SQLGLOT_DIALECT`.
    """
    if not AVAILABLE:
        raise UnparseableSQL("sqlglot is not installed")
    try:
        tree = sqlglot.parse_one(_PREFIX + expression, read=SQLGLOT_DIALECT.get(read))
    except (ParseError, TokenError, RecursionError) as exc:
        raise UnparseableSQL(str(exc).split("\n")[0]) from exc
    rendered = tree.sql(dialect=SQLGLOT_DIALECT.get(write))
    if not rendered.upper().startswith(_PREFIX.strip()):
        raise UnparseableSQL(f"sqlglot wrote an unexpected shape: {rendered!r}")
    return rendered[len(_PREFIX) :]


def window_grouping(expression: str, dialect: str) -> list[str]:
    """The columns an OVER clause partitions or orders by, in order.

    A window function survives into an AML `@sql` body and compiles, and the
    warehouse SQL it produces is only valid when the query groups by these columns. Holistics
    does not check that, so the caller reports it.
    """
    if not AVAILABLE:
        return []
    try:
        tree = sqlglot.parse_one(_PREFIX + expression, read=SQLGLOT_DIALECT.get(dialect))
    except (ParseError, TokenError, RecursionError):
        return []
    seen: list[str] = []
    for window in tree.find_all(exp.Window):
        # `window.this` is the function being windowed, whose columns the
        # aggregate consumes rather than groups by. Only the frame decides the
        # grouping, so only PARTITION BY and ORDER BY are read.
        frame = [window.args.get("partition_by"), window.args.get("order")]
        for part in frame:
            for item in part if isinstance(part, list) else [part]:
                if item is None:
                    continue
                # A column inside an aggregate within the frame is consumed by
                # that aggregate, not grouped by. `ORDER BY SUM(t.v)` orders on
                # the aggregate and puts no requirement on `t.v`.
                aggregated = {
                    id(column)
                    for aggregate in item.find_all(exp.AggFunc)
                    for column in aggregate.find_all(exp.Column)
                }
                for column in item.find_all(exp.Column):
                    if id(column) in aggregated:
                        continue
                    name = f"{column.table}.{column.name}" if column.table else column.name
                    if name not in seen:
                        seen.append(name)
    return seen


def rewrite(expression: str, dialect: str, replace) -> str:
    """`expression` with each column replaced by `replace(column_reference)`.

    Every character outside a replaced span is kept exactly as it was.
    """
    out = expression
    for column in reversed(columns(expression, dialect)):
        out = out[: column.start] + replace(column) + out[column.end :]
    return out
