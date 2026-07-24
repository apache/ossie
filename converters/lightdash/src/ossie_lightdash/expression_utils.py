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

"""Small expression helpers shared by both conversion directions.

Lightdash SQL snippets reference columns as ``${TABLE}.column`` (and joined
tables as ``${other_table.column}``); OSI expressions reference them as
``dataset.column``. These helpers translate between the two spellings and
recognise the single-aggregation shapes that map onto Lightdash's typed
metrics.
"""

import re
from typing import Optional, Tuple

# Aggregations that translate to a typed Lightdash metric. Anything else is
# exported as a `number` metric with raw SQL.
_AGG_TO_LIGHTDASH_TYPE = {
    "SUM": "sum",
    "MIN": "min",
    "MAX": "max",
    "AVG": "average",
    "AVERAGE": "average",
    "MEDIAN": "median",
    "COUNT": "count",
}

_LIGHTDASH_TYPE_TO_AGG = {
    "sum": "SUM",
    "min": "MIN",
    "max": "MAX",
    "average": "AVG",
    "median": "MEDIAN",
    "count": "COUNT",
}

_SIMPLE_AGG_RE = re.compile(
    r"^\s*(?P<func>[A-Za-z_]+)\s*\(\s*(?P<distinct>DISTINCT\s+)?(?P<inner>[A-Za-z_][\w.]*)\s*\)\s*$",
    re.IGNORECASE,
)

def parse_simple_aggregation(expression: str) -> Optional[Tuple[str, str]]:
    """Parse ``AGG(qualifier.column)`` into a (lightdash_type, column_ref) pair.

    Returns None when the expression is anything more complex than a single
    aggregation over a single column reference.
    """
    match = _SIMPLE_AGG_RE.match(expression)
    if not match:
        return None
    func = match.group("func").upper()
    inner = match.group("inner")
    if match.group("distinct"):
        if func != "COUNT":
            return None
        return ("count_distinct", inner)
    lightdash_type = _AGG_TO_LIGHTDASH_TYPE.get(func)
    if lightdash_type is None:
        return None
    return (lightdash_type, inner)


def build_aggregation(lightdash_type: str, dataset: str, column: str) -> Optional[str]:
    """Build the OSI expression for a typed Lightdash metric, if it has one."""
    if lightdash_type == "count_distinct":
        return f"COUNT(DISTINCT {dataset}.{column})"
    agg = _LIGHTDASH_TYPE_TO_AGG.get(lightdash_type)
    if agg is None:
        return None
    return f"{agg}({dataset}.{column})"


def strip_qualifier(column_ref: str) -> str:
    """Return the bare column name of a possibly ``qualifier.column`` reference."""
    return column_ref.rsplit(".", 1)[-1]


def qualifier_of(column_ref: str) -> Optional[str]:
    """Return the qualifier of a ``qualifier.column`` reference, if present."""
    if "." in column_ref:
        return column_ref.rsplit(".", 1)[0]
    return None


def osi_sql_to_lightdash(expression: str, dataset: str) -> str:
    """Rewrite ``dataset.column`` references into Lightdash's ``${TABLE}.column``."""
    return re.sub(
        rf"\b{re.escape(dataset)}\.(\w+)",
        r"${TABLE}.\1",
        expression,
    )


def lightdash_sql_to_osi(sql: str, dataset: str) -> str:
    """Rewrite Lightdash column references into OSI ``dataset.column`` references.

    ``${TABLE}.column`` refers to the current model; ``${other_table.column}``
    refers to a joined model and becomes a cross-dataset reference.
    """
    rewritten = sql.replace("${TABLE}.", f"{dataset}.")
    return re.sub(r"\$\{(\w+)\.(\w+)\}", r"\1.\2", rewritten)



def referenced_datasets(expression: str, dataset_names: set) -> set:
    """Return which of the given dataset names an OSI expression references."""
    found = set()
    for match in re.finditer(r"([A-Za-z_]\w*)\.\w+", expression):
        if match.group(1) in dataset_names:
            found.add(match.group(1))
    return found
