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

"""Tables mapping AML vocabularies onto Ossie ones.

Every mapping here is a dict rather than a branch, so adding an AML type or an
aggregation is one row and the reverse direction reads the same table backwards.
"""
from __future__ import annotations

#: AML `type` to Ossie `datatype`. `number` is the lossy row: AML does not say
#: whether a number is exact or approximate, and `Decimal` is the closest Ossie
#: member because the specification leaves its precision and scale unspecified.
#: The original AML type goes to the stash either way, so the reverse path reads
#: it from there rather than from this table.
AML_TO_OSSIE_DATATYPE: dict[str, str] = {
    "text": "String",
    "truefalse": "Boolean",
    "date": "Date",
    "datetime": "DateTime",
    "number": "Decimal",
}

#: Ossie `datatype` to AML `type`, for a document with no stashed AML type.
#: `Decimal`, `Float` and `Integer` all collapse onto `number`, which is why the
#: forward path stashes the original rather than relying on this table.
OSSIE_TO_AML_TYPE: dict[str, str] = {
    "String": "text",
    "Boolean": "truefalse",
    "Date": "date",
    "Time": "datetime",
    "DateTime": "datetime",
    "DateTimeTz": "datetime",
    "Decimal": "number",
    "Float": "number",
    "Integer": "number",
    "Opaque": "text",
}

#: AML `aggregation_type` to the `(prefix, suffix)` that wraps the measure body.
#: Stored as a pair rather than a function name so the reverse path strips what
#: the forward path added, instead of re-deriving it from the Ossie SQL.
#: `custom` is absent on purpose: its body already aggregates, so the converter
#: wraps nothing. A key missing from this table is an aggregation this converter
#: has never seen, and `aml_to_ossie` reports it rather than guessing.
AGGREGATIONS: dict[str, tuple[str, str]] = {
    "sum": ("SUM(", ")"),
    "count": ("COUNT(", ")"),
    "count distinct": ("COUNT(DISTINCT ", ")"),
    "min": ("MIN(", ")"),
    "max": ("MAX(", ")"),
    "average": ("AVG(", ")"),
    "median": ("MEDIAN(", ")"),
}

CUSTOM_AGGREGATION = "custom"

#: Characters that open a quoted region in a SQL expression. A string literal
#: uses `'`, and a delimited identifier uses `"` or a backtick.
QUOTES = frozenset("'\"`")


def apply_aggregation(aggregation_type: str, body: str) -> str:
    prefix, suffix = AGGREGATIONS[aggregation_type]
    return f"{prefix}{body}{suffix}"


def strip_aggregation(aggregation_type: str, expression: str) -> str | None:
    """The body inside the aggregate, or None when `expression` is not wrapped.

    The reverse path calls this to recover a measure `definition` from an Ossie
    metric expression. A None result means the expression was edited after the
    forward path wrote it, so the reverse path writes `aggregation_type:
    'custom'` and keeps the expression whole rather than producing a measure
    that aggregates twice.
    """
    pair = AGGREGATIONS.get(aggregation_type)
    if pair is None:
        return None
    prefix, suffix = pair
    text = expression.strip()
    if not (text.startswith(prefix) and text.endswith(suffix)):
        return None
    if not _closes_at_end(text, prefix.index("(")):
        return None
    return text[len(prefix) : len(text) - len(suffix)]


def _closes_at_end(text: str, open_index: int) -> bool:
    """Whether the parenthesis at `open_index` is closed by the last character.

    Without this, `SUM(a) + SUM(b)` matches the `sum` prefix and suffix and
    strips to `a) + SUM(b`, which is not an expression.

    Quoted regions are skipped, so a parenthesis inside one does not move the
    depth. All three quoting characters count: a string literal uses `'`, and a
    delimited identifier uses `"` or, on BigQuery and Databricks, a backtick.
    """
    depth = 0
    index = open_index
    while index < len(text):
        character = text[index]
        if character in QUOTES:
            closer = character
            index += 1
            while index < len(text) and text[index] != closer:
                index += 2 if text[index] == "\\" else 1
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
        index += 1
    return False
