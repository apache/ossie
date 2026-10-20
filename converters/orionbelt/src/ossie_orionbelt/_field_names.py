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

"""Field names in what the converter keeps verbatim.

An Ossie field is named by its OBML column name. Earlier exports named it by
the column ``code``, so the unique keys and the unconverted metrics
an import keeps verbatim (Ossie-vendor extensions) can name fields by code.

The import renames them with the exact mapping an older export left in
``obml_column_name`` and marks each payload with :data:`FIELD_NAMES_KEY`. The
export renames an unmarked payload, from OBML saved by an older version, from
the column codes; a code that is also another column's name cannot be told
apart and is kept with a warning.

In a metric's SQL only the characters of each ``<dataset>.<field>`` field are
replaced: regenerating the SQL from a parsed tree would drop what sqlglot does
not keep, such as a weighted percentile's third argument. The parsed tree
tells whether a query in the SQL declares the qualifier as its own table or
alias.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.tokens import Token, TokenType

from ossie_orionbelt._common import _OSSIE_DIALECT_TO_SQLGLOT, _SQL_PARSEABLE_DIALECTS
from ossie_orionbelt._portable import case_collisions, quoted_name

# Marks a payload whose field references are column names.
FIELD_NAMES_KEY = "obml_field_names"
FIELD_NAMES_COLUMN = "column"

# data object -> old field name -> column name
Renames = dict[str, dict[str, str]]
# data object -> column names that equal another of its columns regardless of case
Collisions = dict[str, frozenset[str]]
Warn = Callable[[str], None]


def code_renames(data_objects: dict[str, Any]) -> tuple[Renames, dict[str, set[str]]]:
    """Column ``code`` -> column name per data object, and the ambiguous codes.

    A code is ambiguous when it is also the name of another column, or the
    code of more than one column: an older reference to it cannot be resolved.
    """
    renames: Renames = {}
    ambiguous: dict[str, set[str]] = {}
    for do_name, do_obj in data_objects.items():
        columns = do_obj.get("columns") or {}
        seen: dict[str, str] = {}
        for col_name, col in columns.items():
            code = col.get("code") if isinstance(col, dict) else None
            if not isinstance(code, str) or code == col_name:
                continue
            if code in columns or code in seen:
                ambiguous.setdefault(do_name, set()).add(code)
            seen[code] = col_name
        mapping = {c: n for c, n in seen.items() if c not in ambiguous.get(do_name, set())}
        if mapping:
            renames[do_name] = mapping
    return renames, ambiguous


def rename_keys(
    groups: list[Any],
    renames: dict[str, str],
    ambiguous: Collection[str],
    warn: Warn,
    where: str,
) -> list[Any]:
    """Unique-key column groups with each old field name renamed."""
    for name in {c for g in groups for c in g if c in ambiguous}:
        warn(
            f"{where}: unique key column '{name}' is both a column name and another "
            f"column's code; kept as the column name."
        )
    return [[renames.get(c, c) for c in group] for group in groups]


def field_collisions(data_objects: dict[str, Any]) -> Collisions:
    """Per data object, the column names a bare reference could not tell apart."""
    return {
        name: case_collisions(obj.get("columns") or {})
        for name, obj in data_objects.items()
        if isinstance(obj, dict)
    }


def rename_metric(
    metric: dict[str, Any],
    renames: Renames,
    ambiguous: dict[str, set[str]],
    collisions: Collisions,
    warn: Warn,
) -> None:
    """Rename the field references in each SQL expression of *metric*, in place.

    A preserved metric is one the converter could not handle, so anything not
    shaped like a metric expression is left as it is.
    """
    expression = metric.get("expression")
    if not isinstance(expression, dict):
        return
    for entry in expression.get("dialects") or []:
        if not isinstance(entry, dict):
            continue
        dialect = entry.get("dialect")
        sql = entry.get("expression")
        if dialect not in _SQL_PARSEABLE_DIALECTS or not isinstance(sql, str):
            continue
        where = f"Preserved metric '{metric.get('name')}' ({dialect})"
        entry["expression"] = rename_sql(sql, dialect, renames, ambiguous, collisions, warn, where)


def rename_sql(
    sql: str,
    dialect: str,
    renames: Renames,
    ambiguous: dict[str, set[str]],
    collisions: Collisions,
    warn: Warn,
    where: str,
) -> str:
    """*sql* with each ``<dataset>.<old field>`` reference renamed.

    The references are found on the tokens, which keep what the parsed tree
    drops; the tree tells whether a query in the SQL declares the qualifier
    itself. A quoted old name matches exactly, as the older exports that wrote
    it quoted every name as written; an unquoted one matches regardless of
    case. A new name is written as :func:`quoted_name` says, *collisions*
    being the names of its data object that differ only by case. SQL that
    does not parse is kept.
    """
    datasets = set(renames) | set(ambiguous)
    if not datasets:
        return sql
    read = _OSSIE_DIALECT_TO_SQLGLOT.get(dialect)
    parsed = parse_expression(sql, read)
    if parsed is None:
        warn(f"{where} does not parse; its field references were not renamed.")
        return sql
    text, tree = parsed
    columns = {
        c.this.meta["start"]: c
        for c in tree.find_all(exp.Column)
        if isinstance(c.this, exp.Identifier) and "start" in c.this.meta
    }
    has_queries = tree.find(exp.Select) is not None
    tokens = sqlglot.tokenize(text, read=read)
    edits: list[tuple[int, int, str, str]] = []
    for i in range(len(tokens) - 2):
        table, dot, field = tokens[i : i + 3]
        if dot.token_type is not TokenType.DOT:
            continue
        # Only a two-part reference: ``a.b.c`` is not ``<dataset>.<field>``.
        if i > 0 and tokens[i - 1].token_type is TokenType.DOT:
            continue
        if i + 3 < len(tokens) and tokens[i + 3].token_type is TokenType.DOT:
            continue
        do_name = _match(table, datasets, warn, where)
        if do_name is None:
            continue
        if _match(field, ambiguous.get(do_name, set()), warn, where) is not None:
            warn(
                f"{where}: '{field.text}' is both a column name and another column's "
                f"code; the reference was not renamed."
            )
            continue
        old = _match(field, renames.get(do_name, {}), warn, where)
        if old is None:
            continue
        column = columns.get(field.start)
        if column is None and has_queries:
            warn(f"{where}: the scope of '{table.text}.{field.text}' is unknown; not renamed.")
            continue
        if column is not None and _declared_in_scope(column, table.text):
            continue
        edits.append((field.start, field.end, renames[do_name][old], do_name))
    for start, end, name, do_name in sorted(edits, reverse=True):
        quoted = quoted_name(name, collisions.get(do_name, frozenset()))
        if quoted is None:
            ident = name
        elif sql[start] == "[":
            ident = f"[{quoted}]"
        else:
            ident = exp.to_identifier(quoted, quoted=True).sql(dialect=read)
        sql = sql[:start] + ident + sql[end + 1 :]
    return sql


def parse_expression(sql: str, read: str | None) -> tuple[str, exp.Expr] | None:
    """The text parsed and its tree, retrying with ``[name]`` read as ``"name"``
    like the import.

    Both spellings have the same length (a name holding a double quote stays
    bracketed), so positions in the parsed text address the original.
    """
    try:
        return sql, sqlglot.parse_one(sql, read=read)
    except sqlglot.errors.SqlglotError:
        pass
    normalized = re.sub(r"\[([^\]\"]+)\]", r'"\1"', sql)
    if normalized == sql:
        return None
    try:
        return normalized, sqlglot.parse_one(normalized, read=read)
    except sqlglot.errors.SqlglotError:
        return None


def _match(token: Token, names: Iterable[str], warn: Warn, where: str) -> str | None:
    """The name *token* refers to: exactly when quoted, else ignoring case."""
    if token.token_type is TokenType.IDENTIFIER:
        return token.text if token.text in names else None
    hits = [n for n in names if n.lower() == token.text.lower()]
    if len(hits) > 1:
        warn(f"{where}: unquoted '{token.text}' matches {sorted(hits)}; not renamed.")
    return hits[0] if len(hits) == 1 else None


def _declared_in_scope(node: exp.Expr, name: str) -> bool:
    """Whether a query around *node* declares a table, alias or CTE *name*."""
    lowered = name.lower()
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            sources: list[exp.Expr] = []
            if parent.args.get("from_") is not None:
                sources.append(parent.args["from_"].this)
            sources.extend(join.this for join in parent.args.get("joins") or [])
            with_ = parent.args.get("with_")
            if with_ is not None:
                sources.extend(with_.expressions)
            if any(s.alias_or_name.lower() == lowered for s in sources):
                return True
        parent = parent.parent
    return False
