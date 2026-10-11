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

"""Snowflake semantic-view YAML, both directions, for this finance model only."""

from __future__ import annotations

import yaml

from ossie_wisdom_semantic_view.model import (
    SPEC_VERSION,
    check_expression,
    check_metric_expression,
    die,
    dimension_for_datatype,
    only_snowflake,
    require_keys,
    snowflake_expression,
    SNOWFLAKE_TO_OSSIE,
    OSSIE_TO_SNOWFLAKE,
)

RELATIONSHIP_NAME = "bookings_to_fiscal_calendar"
MANY_SIDE = "bookings"
ONE_SIDE = "fiscal_calendar"

_VIEW_KEYS = {
    "name",
    "description",
    "tables",
    "relationships",
    "metrics",
    "module_custom_instructions",
}
_TABLE_KEYS = {
    "name",
    "base_table",
    "primary_key",
    "dimensions",
    "time_dimensions",
    "facts",
}
_BASE_TABLE_KEYS = {"database", "schema", "table"}
_PRIMARY_KEY_KEYS = {"columns"}
_COLUMN_KEYS = {"name", "expr", "data_type"}
_RELATIONSHIP_KEYS = {"name", "left_table", "right_table", "relationship_columns"}
_RELATIONSHIP_COLUMN_KEYS = {"left_column", "right_column"}
_METRIC_KEYS = {"name", "description", "expr", "synonyms"}
_MODULE_KEYS = {"sql_generation"}


def load_semantic_view(text: str) -> dict:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        die("name")
    return loaded


def dump_semantic_view(view: dict) -> str:
    return yaml.safe_dump(view, sort_keys=False, width=4096, allow_unicode=True)


def semantic_view_to_ossie(view: dict) -> dict:
    require_keys(view, _VIEW_KEYS)
    for key in ("name", "description", "tables", "relationships", "metrics"):
        if key not in view:
            die(key)
    datasets = [_table_to_dataset(table) for table in _as_list(view["tables"], "tables")]
    relationships = [
        _relationship_to_ossie(item) for item in _as_list(view["relationships"], "relationships")
    ]
    dataset_names = [dataset["name"] for dataset in datasets]
    metrics = [
        _metric_to_ossie(item, dataset_names) for item in _as_list(view["metrics"], "metrics")
    ]
    model = {
        "version": SPEC_VERSION,
        "name": view["name"],
        "description": view["description"],
    }
    instructions = view.get("module_custom_instructions")
    if instructions is not None:
        require_keys(instructions, _MODULE_KEYS)
        if "sql_generation" not in instructions:
            die("sql_generation")
        model["ai_context"] = {"instructions": instructions["sql_generation"]}
    model["datasets"] = datasets
    model["relationships"] = relationships
    model["metrics"] = metrics
    return model


def ossie_to_semantic_view(model: dict) -> dict:
    if model.get("version") != SPEC_VERSION:
        die(str(model.get("version")))
    tables = [_dataset_to_table(dataset) for dataset in model["datasets"]]
    relationships = [_ossie_relationship_to_view(item) for item in model.get("relationships") or []]
    dataset_names = [dataset["name"] for dataset in model["datasets"]]
    metrics = [
        _ossie_metric_to_view(item, dataset_names) for item in model.get("metrics") or []
    ]
    view = {
        "name": model["name"],
        "description": model["description"],
        "tables": tables,
        "relationships": relationships,
        "metrics": metrics,
    }
    ai_context = model.get("ai_context")
    if ai_context is not None:
        require_keys(ai_context, {"instructions"})
        if "instructions" not in ai_context:
            die("instructions")
        view["module_custom_instructions"] = {"sql_generation": ai_context["instructions"]}
    return view


def _table_to_dataset(table: dict) -> dict:
    require_keys(table, _TABLE_KEYS)
    for key in ("name", "base_table", "primary_key"):
        if key not in table:
            die(key)
    base_table = require_keys(table["base_table"], _BASE_TABLE_KEYS)
    for key in ("database", "schema", "table"):
        if key not in base_table:
            die(key)
    primary_key = require_keys(table["primary_key"], _PRIMARY_KEY_KEYS)
    columns = primary_key.get("columns")
    if not isinstance(columns, list) or not columns:
        die("columns")
    fields = []
    for column in _as_list(table.get("dimensions", []), "dimensions"):
        fields.append(_attribute_to_field(column, is_time=False))
    for column in _as_list(table.get("time_dimensions", []), "time_dimensions"):
        fields.append(_attribute_to_field(column, is_time=True))
    for column in _as_list(table.get("facts", []), "facts"):
        fields.append(_fact_to_field(column))
    return {
        "name": table["name"],
        "source": f"{base_table['database']}.{base_table['schema']}.{base_table['table']}",
        "primary_key": list(columns),
        "fields": fields,
    }


def _attribute_to_field(column: dict, *, is_time: bool) -> dict:
    name, expression, datatype = _column_parts(column)
    if expression != name:
        die(expression)
    expected = dimension_for_datatype(datatype)
    if expected != {"is_time": is_time}:
        die(column["data_type"])
    return {
        "name": name,
        "expression": snowflake_expression(expression),
        "datatype": datatype,
        "dimension": {"is_time": is_time},
    }


def _fact_to_field(column: dict) -> dict:
    name, expression, datatype = _column_parts(column)
    if expression != name:
        die(expression)
    if dimension_for_datatype(datatype) is not None:
        die(column["data_type"])
    return {
        "name": name,
        "expression": snowflake_expression(expression),
        "datatype": datatype,
    }


def _column_parts(column: dict) -> tuple[str, str, str]:
    require_keys(column, _COLUMN_KEYS)
    for key in ("name", "expr", "data_type"):
        if key not in column:
            die(key)
    expression = column["expr"]
    if not isinstance(expression, str):
        die("expr")
    check_expression(expression)
    data_type = column["data_type"]
    if data_type not in SNOWFLAKE_TO_OSSIE:
        die(str(data_type))
    return column["name"], expression, SNOWFLAKE_TO_OSSIE[data_type]


def _relationship_to_ossie(relationship: dict) -> dict:
    require_keys(relationship, _RELATIONSHIP_KEYS)
    for key in ("name", "left_table", "right_table", "relationship_columns"):
        if key not in relationship:
            die(key)
    # This file is many-to-one with bookings on the left. Do not infer
    # cardinality for any other relationship.
    if (
        relationship["name"] != RELATIONSHIP_NAME
        or relationship["left_table"] != MANY_SIDE
        or relationship["right_table"] != ONE_SIDE
    ):
        die(str(relationship["name"]))
    pairs = _as_list(relationship["relationship_columns"], "relationship_columns")
    if len(pairs) != 1:
        die("relationship_columns")
    pair = require_keys(pairs[0], _RELATIONSHIP_COLUMN_KEYS)
    for key in ("left_column", "right_column"):
        if key not in pair:
            die(key)
    return {
        "name": relationship["name"],
        "from": relationship["left_table"],
        "to": relationship["right_table"],
        "from_columns": [pair["left_column"]],
        "to_columns": [pair["right_column"]],
    }


def _metric_to_ossie(metric: dict, dataset_names: list[str]) -> dict:
    require_keys(metric, _METRIC_KEYS)
    for key in ("name", "description", "expr"):
        if key not in metric:
            die(key)
    expression = metric["expr"]
    if not isinstance(expression, str):
        die("expr")
    check_metric_expression(expression, dataset_names)
    converted = {
        "name": metric["name"],
        "description": metric["description"],
        "expression": snowflake_expression(expression),
    }
    if "synonyms" in metric:
        synonyms = _import_synonyms(metric["synonyms"])
        if synonyms:
            converted["ai_context"] = {"synonyms": synonyms}
    return converted


def _import_synonyms(synonyms: object) -> list[str]:
    if not isinstance(synonyms, list) or not all(isinstance(item, str) for item in synonyms):
        die("synonyms")
    # An empty list is the same as an omitted key. _knowledge_from_model
    # drops empty lists, so keeping [] would not round-trip.
    if not synonyms:
        return []
    seen: set[str] = set()
    for synonym in synonyms:
        if "\n" in synonym or "\r" in synonym or synonym in seen:
            die("synonyms")
        seen.add(synonym)
    return list(synonyms)


def _dataset_to_table(dataset: dict) -> dict:
    parts = str(dataset["source"]).split(".")
    if len(parts) != 3 or not all(parts):
        die(str(dataset["source"]))
    database, schema, table_name = parts
    dimensions: list[dict] = []
    time_dimensions: list[dict] = []
    facts: list[dict] = []
    for field in dataset["fields"]:
        expression = only_snowflake(field["expression"])
        if expression != field["name"]:
            die(expression)
        datatype = field["datatype"]
        if datatype not in OSSIE_TO_SNOWFLAKE:
            die(str(datatype))
        column = {
            "name": field["name"],
            "expr": expression,
            "data_type": OSSIE_TO_SNOWFLAKE[datatype],
        }
        if "dimension" in field:
            is_time = field["dimension"].get("is_time")
            expected = dimension_for_datatype(datatype)
            if expected != {"is_time": is_time}:
                die(field["name"])
            if is_time:
                time_dimensions.append(column)
            else:
                dimensions.append(column)
        else:
            if dimension_for_datatype(datatype) is not None:
                die(field["name"])
            facts.append(column)
    table = {
        "name": dataset["name"],
        "base_table": {"database": database, "schema": schema, "table": table_name},
        "primary_key": {"columns": list(dataset["primary_key"])},
    }
    if dimensions:
        table["dimensions"] = dimensions
    if time_dimensions:
        table["time_dimensions"] = time_dimensions
    if facts:
        table["facts"] = facts
    return table


def _ossie_relationship_to_view(relationship: dict) -> dict:
    if relationship.get("from") != MANY_SIDE or relationship.get("to") != ONE_SIDE:
        die(str(relationship.get("name")))
    from_columns = relationship.get("from_columns") or []
    to_columns = relationship.get("to_columns") or []
    if len(from_columns) != 1 or len(to_columns) != 1:
        die("relationship_columns")
    return {
        "name": relationship["name"],
        "left_table": relationship["from"],
        "right_table": relationship["to"],
        "relationship_columns": [
            {"left_column": from_columns[0], "right_column": to_columns[0]}
        ],
    }


def _ossie_metric_to_view(metric: dict, dataset_names: list[str]) -> dict:
    expression = only_snowflake(metric["expression"])
    check_metric_expression(expression, dataset_names)
    converted = {
        "name": metric["name"],
        "description": metric["description"],
        "expr": expression,
    }
    ai_context = metric.get("ai_context")
    if ai_context is not None:
        require_keys(ai_context, {"synonyms"})
        synonyms = ai_context.get("synonyms")
        if not isinstance(synonyms, list):
            die("synonyms")
        converted["synonyms"] = list(synonyms)
    return converted


def _as_list(value: object, key: str) -> list:
    if not isinstance(value, list):
        die(key)
    return value
