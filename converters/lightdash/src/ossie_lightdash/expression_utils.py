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
"""Expression helpers shared by both conversion directions.

Lightdash SQL snippets reference the current model as ``${TABLE}.column``,
sibling fields as ``${column}`` or ``${metric}``, joined models as
``${other_table.column}`` and project parameters or user attributes as
``${lightdash.…}`` / ``${ld.…}``. Ossie expressions reference columns as
``dataset.column``. These helpers translate between the two spellings and
recognise the aggregation shapes that map onto Lightdash's typed metrics.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

_REFERENCE_RE = re.compile(r"\$\{([^}]+)\}")
_NON_PORTABLE_REFERENCE_RE = re.compile(r"\$\{(?:lightdash|ld)\.[^}]*\}")
# Liquid tags and bare `ld.parameters.x` / `ld.query.filters` / `ld.user.x`
# references inside them are evaluated by Lightdash at query time.
_NON_PORTABLE_SYNTAX_RE = re.compile(
    r"\{%|\{\{|(?<![\w.])(?:lightdash|ld)\.(?:parameters|query|user)\."
)
_COLUMN_REFERENCE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)?$")

# Lightdash metric types that are encoded as an aggregation in the Ossie
# expression. Anything else is exported as a `number` metric with raw SQL.
_AGGREGATE_FUNCTIONS = {
    "sum": "SUM",
    "min": "MIN",
    "max": "MAX",
    "average": "AVG",
    "median": "MEDIAN",
    "count": "COUNT",
}
_DISTINCT_AGGREGATE_FUNCTIONS = {
    "count_distinct": "COUNT",
    "sum_distinct": "SUM",
    "average_distinct": "AVG",
}
AGGREGATE_TYPES = frozenset(
    {*_AGGREGATE_FUNCTIONS, *_DISTINCT_AGGREGATE_FUNCTIONS, "percentile"}
)

_FUNCTION_TO_TYPE = {
    "SUM": "sum",
    "MIN": "min",
    "MAX": "max",
    "AVG": "average",
    "AVERAGE": "average",
    "MEDIAN": "median",
    "COUNT": "count",
}
_DISTINCT_FUNCTION_TO_TYPE = {
    "COUNT": "count_distinct",
    "SUM": "sum_distinct",
    "AVG": "average_distinct",
    "AVERAGE": "average_distinct",
}

_CALL_RE = re.compile(r"^\s*(?P<func>[A-Za-z_]+)\s*\((?P<body>.*)\)\s*$", re.DOTALL)
_DISTINCT_RE = re.compile(r"^DISTINCT\s+(?P<inner>.+)$", re.IGNORECASE | re.DOTALL)
_PERCENTILE_RE = re.compile(
    r"^\s*PERCENTILE_CONT\s*\(\s*(?P<fraction>[0-9]*\.?[0-9]+)\s*\)\s*"
    r"WITHIN\s+GROUP\s*\(\s*ORDER\s+BY\s+(?P<inner>.+?)\s*\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ParsedAggregation:
    lightdash_type: str
    inner: str
    percentile: Optional[float] = None


def _is_single_call_body(body: str) -> bool:
    """True when the parentheses in ``body`` are balanced, i.e. the outer
    parentheses around it belong to one function call."""
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def parse_aggregation(expression: str) -> Optional[ParsedAggregation]:
    """Recognise an expression that is exactly one aggregation over an operand.

    ``SUM(x)``, ``COUNT(DISTINCT x)``, ``SUM(DISTINCT x)`` and
    ``PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY x)`` map onto Lightdash's typed
    metrics; the operand may be a bare column reference or any expression.
    Returns None for anything else (``COUNT(*)``, arithmetic between
    aggregations, unknown functions).
    """
    percentile_match = _PERCENTILE_RE.match(expression)
    if percentile_match:
        percentile = float(percentile_match.group("fraction")) * 100
        if percentile.is_integer():
            percentile = int(percentile)
        return ParsedAggregation(
            "percentile", percentile_match.group("inner").strip(), percentile
        )

    match = _CALL_RE.match(expression)
    if match is None or not _is_single_call_body(match.group("body")):
        return None
    function = match.group("func").upper()
    inner = match.group("body").strip()
    distinct = _DISTINCT_RE.match(inner)
    if distinct:
        lightdash_type = _DISTINCT_FUNCTION_TO_TYPE.get(function)
        inner = distinct.group("inner").strip()
    else:
        lightdash_type = _FUNCTION_TO_TYPE.get(function)
    if lightdash_type is None or not inner or inner == "*":
        return None
    return ParsedAggregation(lightdash_type, inner)


def build_aggregation(
    lightdash_type: str, inner: str, percentile: Optional[float] = None
) -> Optional[str]:
    """Build the Ossie expression for a typed Lightdash metric over ``inner``.

    Returns None for metric types that are not aggregations (``number``,
    ``string``, ``boolean``, ...).
    """
    if lightdash_type == "percentile":
        fraction = (50 if percentile is None else percentile) / 100
        return f"PERCENTILE_CONT({fraction:g}) WITHIN GROUP (ORDER BY {inner})"
    if lightdash_type in _DISTINCT_AGGREGATE_FUNCTIONS:
        return f"{_DISTINCT_AGGREGATE_FUNCTIONS[lightdash_type]}(DISTINCT {inner})"
    function = _AGGREGATE_FUNCTIONS.get(lightdash_type)
    if function is None:
        return None
    return f"{function}({inner})"


def is_column_reference(expression: str) -> bool:
    """True for a bare ``column`` or ``qualifier.column`` reference."""
    return _COLUMN_REFERENCE_RE.match(expression) is not None


def strip_qualifier(column_ref: str) -> str:
    """Return the bare column name of a possibly ``qualifier.column`` reference."""
    return column_ref.rsplit(".", 1)[-1]


def qualifier_of(column_ref: str) -> Optional[str]:
    """Return the qualifier of a ``qualifier.column`` reference, if present."""
    if "." in column_ref:
        return column_ref.rsplit(".", 1)[0]
    return None


def ossie_sql_to_lightdash(
    expression: str, dataset: str, references: Optional[Dict[str, str]] = None
) -> str:
    """Rewrite Ossie column references into Lightdash references.

    ``dataset.column`` becomes ``${TABLE}.column``; a dataset in ``references``
    (joined from ``dataset``) becomes ``${<join reference>.column}``, where the
    join reference is the joined model's name or its alias.
    """
    names = {dataset: None, **(references or {})}

    def replace(match: "re.Match[str]") -> str:
        name, column = match.group(1), match.group(2)
        reference = names[name]
        if reference is None:
            return f"${{TABLE}}.{column}"
        return f"${{{reference}.{column}}}"

    alternatives = "|".join(re.escape(name) for name in names)
    return re.sub(rf"\b({alternatives})\.(\w+)", replace, expression)


def has_non_portable_reference(sql: str) -> bool:
    """True when the SQL depends on Lightdash query-time evaluation: project
    parameters, user attributes, or Liquid templating."""
    return (
        _NON_PORTABLE_REFERENCE_RE.search(sql) is not None
        or _NON_PORTABLE_SYNTAX_RE.search(sql) is not None
    )


@dataclass
class RewriteResult:
    expression: str
    inlined_metrics: List[str] = field(default_factory=list)
    flattened_aliases: List[str] = field(default_factory=list)


def lightdash_sql_to_ossie(
    sql: str,
    dataset: str,
    *,
    aliases: Optional[Dict[str, str]] = None,
    resolve_metric: Optional[Callable[[str], Optional[str]]] = None,
) -> RewriteResult:
    """Rewrite Lightdash references into Ossie ``dataset.column`` references.

    ``${TABLE}.column`` and bare ``${column}`` refer to the current model;
    ``${other_table.column}`` refers to a joined model and becomes a
    cross-dataset reference; ``${alias.column}`` is flattened onto the joined
    model; ``${metric}`` is replaced by that metric's expression when
    ``resolve_metric`` knows it. Parameter and user-attribute references are
    left untouched: callers check ``has_non_portable_reference`` first.
    """
    alias_map = aliases or {}
    result = RewriteResult(expression="")

    def replace(match: "re.Match[str]") -> str:
        reference = match.group(1)
        if _NON_PORTABLE_REFERENCE_RE.match(match.group(0)):
            return match.group(0)
        if reference == "TABLE":
            return dataset
        if reference.startswith("TABLE."):
            return f"{dataset}.{reference[len('TABLE.'):]}"
        if "." in reference:
            table, column = reference.split(".", 1)
            if table in alias_map:
                result.flattened_aliases.append(table)
                return f"{alias_map[table]}.{column}"
            return reference
        if resolve_metric is not None:
            resolved = resolve_metric(reference)
            if resolved is not None:
                result.inlined_metrics.append(reference)
                return f"({resolved})"
        return f"{dataset}.{reference}"

    result.expression = _REFERENCE_RE.sub(replace, sql)
    return result


_STRING_LITERAL_RE = re.compile(r"('(?:[^']|'')*')")


def qualify_bare_columns(sql: str, dataset: str, columns) -> str:
    """Prefix bare references to the model's own columns with the dataset name.

    Lightdash lets SQL name a column of the current model without ``${TABLE}``
    (``SUM(budget_use)``); Ossie consumers need ``dataset.column``. Only
    identifiers that name one of ``columns`` are touched, never inside string
    literals, never when already qualified, never when followed by ``(``.
    """
    lookup = {column.lower(): column for column in columns}
    if not lookup:
        return sql
    pattern = re.compile(
        r"(?<![\w.$])(" + "|".join(re.escape(column) for column in lookup) + r")\b(?!\s*\()",
        re.IGNORECASE,
    )

    def replace(match: "re.Match[str]") -> str:
        return f"{dataset}.{match.group(1)}"

    parts = _STRING_LITERAL_RE.split(sql)
    return "".join(
        part if index % 2 else pattern.sub(replace, part)
        for index, part in enumerate(parts)
    )


def referenced_datasets(expression: str, dataset_names: set) -> set:
    """Return which of the given dataset names an Ossie expression references."""
    found = set()
    for match in re.finditer(r"([A-Za-z_]\w*)\.\w+", expression):
        if match.group(1) in dataset_names:
            found.add(match.group(1))
    return found
