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

"""Export an Ossie semantic model to a DuckDB SQL script.

Each dataset becomes a view over its physical source with the dataset's
fields as columns; each metric becomes a ``metric_<name>`` view whose FROM
clause is derived from the declared relationships. Descriptions become
``COMMENT ON`` statements.
"""

import re
from collections import deque

import yaml

from ossie_duckdb._common import (
    ConversionError,
    quote_identifier,
    select_expression,
    sql_string_literal,
)

_DATASET_REF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.")


def convert_osi_to_duckdb(osi_yaml: str, view_schema: str | None = None) -> str:
    """Convert an Ossie semantic model YAML document to a DuckDB SQL script.

    When view_schema is given, all views are created inside that schema
    (created with ``CREATE SCHEMA IF NOT EXISTS``); otherwise they land in
    the connection's default schema.
    """
    document = yaml.safe_load(osi_yaml)
    if not isinstance(document, dict) or not isinstance(document.get("semantic_model"), list):
        raise ConversionError("input is not an Ossie document (missing 'semantic_model' list)")

    statements: list[str] = []
    if view_schema:
        statements.append(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(view_schema)}")

    for model in document["semantic_model"]:
        statements.extend(_convert_model(model, view_schema))

    return "-- Apache Ossie semantic model exported to DuckDB SQL\n\n" + ";\n\n".join(statements) + ";\n"


def _qualify(view_schema: str | None, name: str) -> str:
    quoted = quote_identifier(name)
    if view_schema:
        return f"{quote_identifier(view_schema)}.{quoted}"
    return quoted


def _convert_model(model: dict, view_schema: str | None) -> list[str]:
    model_name = model.get("name", "<unnamed>")
    datasets = {d["name"]: d for d in model.get("datasets") or []}
    relationships = model.get("relationships") or []
    statements: list[str] = []

    for dataset in datasets.values():
        statements.extend(_dataset_view(dataset, view_schema))

    for metric in model.get("metrics") or []:
        statements.extend(_metric_view(metric, model_name, datasets, relationships, view_schema))

    if statements:
        statements[0] = f"-- Semantic model: {model_name}\n{statements[0]}"
    return statements


def _dataset_view(dataset: dict, view_schema: str | None) -> list[str]:
    name = dataset["name"]
    source = dataset.get("source")
    if not source:
        raise ConversionError(f"Dataset '{name}': missing 'source'")

    fields = dataset.get("fields") or []
    if fields:
        select_items = []
        for field in fields:
            expr = select_expression(field.get("expression"), f"Field '{name}.{field['name']}'")
            select_items.append(f"    {expr} AS {quote_identifier(field['name'])}")
        select_list = ",\n".join(select_items)
    else:
        select_list = "    *"

    view_name = _qualify(view_schema, name)
    statements = [f"CREATE OR REPLACE VIEW {view_name} AS\nSELECT\n{select_list}\nFROM {source}"]

    if dataset.get("description"):
        statements.append(f"COMMENT ON VIEW {view_name} IS {sql_string_literal(dataset['description'])}")
    for field in fields:
        if field.get("description"):
            statements.append(
                f"COMMENT ON COLUMN {view_name}.{quote_identifier(field['name'])} "
                f"IS {sql_string_literal(field['description'])}"
            )
    return statements


def _referenced_datasets(expression: str, datasets: dict) -> list[str]:
    """Dataset names referenced as ``dataset.field`` in an expression, in order."""
    seen: list[str] = []
    for match in _DATASET_REF.finditer(expression):
        name = match.group(1)
        if name in datasets and name not in seen:
            seen.append(name)
    return seen


def _check_join_columns(rel: dict, datasets: dict, metric_name: str) -> None:
    """Ensure relationship columns are exposed as fields on datasets that declare fields."""
    for side, columns_key in (("from", "from_columns"), ("to", "to_columns")):
        dataset = datasets[rel[side]]
        fields = dataset.get("fields") or []
        if not fields:
            continue  # SELECT * view exposes every source column
        field_names = {f["name"] for f in fields}
        missing = [c for c in rel.get(columns_key, []) if c not in field_names]
        if missing:
            raise ConversionError(
                f"Metric '{metric_name}': relationship '{rel.get('name')}' joins on "
                f"{missing} but dataset '{rel[side]}' does not expose those fields"
            )


def _join_tree(base: str, targets: list[str], relationships: list[dict], metric_name: str) -> list[tuple[dict, str]]:
    """BFS the relationship graph from base and return (relationship, joined_dataset)
    pairs covering every target, parents before children."""
    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for rel in relationships:
        adjacency.setdefault(rel["from"], []).append((rel["to"], rel))
        adjacency.setdefault(rel["to"], []).append((rel["from"], rel))

    parent: dict[str, tuple[str, dict] | None] = {base: None}
    order: list[str] = []
    queue = deque([base])
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor, rel in adjacency.get(node, []):
            if neighbor not in parent:
                parent[neighbor] = (node, rel)
                queue.append(neighbor)

    unreachable = [t for t in targets if t not in parent]
    if unreachable:
        raise ConversionError(
            f"Metric '{metric_name}': datasets {unreachable} are not connected to '{base}' by any declared relationship"
        )

    needed: set[str] = set()
    for target in targets:
        node = target
        while node != base:
            needed.add(node)
            node = parent[node][0]

    return [(parent[node][1], node) for node in order if node in needed]


def _metric_view(
    metric: dict,
    model_name: str,
    datasets: dict,
    relationships: list[dict],
    view_schema: str | None,
) -> list[str]:
    name = metric["name"]
    context = f"Metric '{name}' in model '{model_name}'"
    expr = select_expression(metric.get("expression"), context)

    refs = _referenced_datasets(expr, datasets)
    if not refs:
        if len(datasets) == 1:
            refs = [next(iter(datasets))]
        else:
            raise ConversionError(
                f"{context}: expression references no known dataset and the model "
                f"has {len(datasets)} datasets; cannot derive a FROM clause"
            )

    # Prefer a base that sits on the many ("from") side of a relationship.
    base = next((r for r in refs if any(rel["from"] == r for rel in relationships)), refs[0])

    joins = _join_tree(base, refs, relationships, name) if len(refs) > 1 else []
    for rel, _ in joins:
        _check_join_columns(rel, datasets, name)

    from_clause = f"FROM {_qualify(view_schema, base)} AS {quote_identifier(base)}"
    for rel, joined in joins:
        conditions = " AND ".join(
            f"{quote_identifier(rel['from'])}.{quote_identifier(fc)} = "
            f"{quote_identifier(rel['to'])}.{quote_identifier(tc)}"
            for fc, tc in zip(rel["from_columns"], rel["to_columns"], strict=True)
        )
        from_clause += f"\nLEFT JOIN {_qualify(view_schema, joined)} AS {quote_identifier(joined)} ON {conditions}"

    view_name = _qualify(view_schema, f"metric_{name}")
    statements = [
        f"CREATE OR REPLACE VIEW {view_name} AS\nSELECT\n    {expr} AS {quote_identifier(name)}\n{from_clause}"
    ]
    if metric.get("description"):
        statements.append(f"COMMENT ON VIEW {view_name} IS {sql_string_literal(metric['description'])}")
    return statements
