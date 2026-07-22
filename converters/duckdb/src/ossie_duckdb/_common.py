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

"""Shared helpers for the DuckDB <-> Ossie converter."""

import re
import warnings

SPEC_VERSION = "0.2.0.dev0"

# Dialects the export accepts, in preference order.
PREFERRED_DIALECTS = ("DUCKDB", "ANSI_SQL")

_SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Keywords that must be quoted even though they look like simple identifiers.
# Deliberately small: DuckDB accepts most keywords as identifiers unquoted.
_RESERVED = {
    "all",
    "and",
    "any",
    "as",
    "asc",
    "between",
    "by",
    "case",
    "cast",
    "create",
    "cross",
    "current_date",
    "current_time",
    "current_timestamp",
    "default",
    "desc",
    "distinct",
    "drop",
    "else",
    "end",
    "except",
    "exists",
    "false",
    "from",
    "full",
    "group",
    "having",
    "in",
    "inner",
    "intersect",
    "into",
    "is",
    "join",
    "left",
    "like",
    "limit",
    "natural",
    "not",
    "null",
    "offset",
    "on",
    "or",
    "order",
    "outer",
    "right",
    "select",
    "table",
    "then",
    "true",
    "union",
    "unique",
    "using",
    "view",
    "when",
    "where",
    "with",
}


class ConversionError(Exception):
    """Raised when a model cannot be converted."""


def quote_identifier(name: str) -> str:
    """Quote an identifier only when necessary (non-simple or reserved)."""
    if _SIMPLE_IDENTIFIER.match(name) and name.lower() not in _RESERVED:
        return name
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def sql_string_literal(text: str) -> str:
    """Render text as a single-quoted SQL string literal."""
    return "'" + text.replace("'", "''") + "'"


def select_expression(expression: dict | None, context: str) -> str:
    """Pick the best expression for DuckDB from a multi-dialect expression object.

    Prefers the DUCKDB dialect, falls back to ANSI_SQL with a warning, and
    raises ConversionError when neither is available.
    """
    dialects = (expression or {}).get("dialects") or []
    by_dialect = {d.get("dialect"): d.get("expression") for d in dialects if d.get("expression")}
    for dialect in PREFERRED_DIALECTS:
        if dialect in by_dialect:
            if dialect != "DUCKDB":
                warnings.warn(
                    f"{context}: no DUCKDB dialect expression; falling back to {dialect}",
                    stacklevel=2,
                )
            return by_dialect[dialect]
    available = ", ".join(sorted(by_dialect)) or "none"
    raise ConversionError(f"{context}: no DUCKDB or ANSI_SQL expression available (has: {available})")
