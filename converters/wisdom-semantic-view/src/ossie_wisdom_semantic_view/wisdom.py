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

"""Wisdom domain-export JSON, format 1.0, both directions, for this finance model."""

from __future__ import annotations

import json
import re

from ossie_wisdom_semantic_view.model import (
    SPEC_VERSION,
    WISDOM_TO_OSSIE,
    check_expression,
    check_metric_expression,
    die,
    dimension_for_datatype,
    only_snowflake,
    OSSIE_TO_WISDOM,
    snowflake_expression,
)
from ossie_wisdom_semantic_view.semantic_view import MANY_SIDE, ONE_SIDE, RELATIONSHIP_NAME

# Constants for this offline demo. They are not read from a Wisdom tenant.
# domain_name is the model name. For this fixture that name is finance_cfo_cockpit.
EXPORTED_AT = "2026-01-01T00:00:00+00:00"
SOURCE_DOMAIN_ID = "demo-finance-cfo"
CONNECTION_ID = "et-connection-snowflake"
BOARD_REVENUE = "Board revenue"
SYNONYM_PREFIX = "Synonyms for "


def dump_wisdom(export: dict) -> str:
    return json.dumps(export, indent=2) + "\n"


def load_wisdom(text: str) -> dict:
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        die("version")
    return loaded


def domain_uuid(name: str) -> str:
    return f"ET_DOMAIN_{name}"


def zsheet_uuid(name: str) -> str:
    return f"ET_ZSHEET_{name}"


def knowledge_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"ET_UNSTRUCTURED_KNOWLEDGE_{slug}"


def measure_id(name: str) -> str:
    return f"MEASURE_{name}"


def empty_item_list() -> dict:
    return {"ref": None, "items_json": "{}"}


def reject_duplicate_knowledge_ids(items: list[dict]) -> None:
    """Fail when two knowledge names slugify to the same id. Do not suffix."""

    seen: dict[str, str] = {}
    for item in items:
        item_id = item["id"]
        name = item["name"]
        if item_id in seen:
            die(f"duplicate knowledge id: {item_id} ({seen[item_id]}, {name})")
        seen[item_id] = name


def reject_nonempty_synonym_sets(export: dict) -> None:
    synonym_sets = export.get("synonym_sets", {})
    # A present null is an empty set, not a missing dict.
    if synonym_sets is None:
        return
    if not isinstance(synonym_sets, dict):
        die("synonym_sets")
    raw = synonym_sets.get("items_json", "{}")
    if raw is None:
        return
    if not isinstance(raw, str):
        die("synonym_sets.items_json")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        die("synonym_sets.items_json")
    if parsed is None:
        return
    if not isinstance(parsed, dict) or parsed != {}:
        die("synonym_sets.items_json")


def ossie_to_wisdom(model: dict) -> dict:
    if model.get("version") != SPEC_VERSION:
        die(str(model.get("version")))
    name = model["name"]
    dataset_names = [dataset["name"] for dataset in model["datasets"]]
    measures_by_dataset: dict[str, list[dict]] = {dataset_name: [] for dataset_name in dataset_names}
    for metric in model.get("metrics") or []:
        expression = only_snowflake(metric["expression"])
        check_metric_expression(expression, dataset_names)
        if MANY_SIDE not in measures_by_dataset:
            die(expression)
        measures_by_dataset[MANY_SIDE].append(
            {
                "name": metric["name"],
                "expression": expression,
                "description": metric["description"],
                "id": measure_id(metric["name"]),
            }
        )

    zsheets = []
    tables = []
    table_metadata = []
    for dataset in model["datasets"]:
        uuid = zsheet_uuid(dataset["name"])
        zsheets.append({"uuid": uuid, "name": dataset["name"], "version": "1"})
        database, schema, table_name = _split_source(dataset["source"])
        columns = []
        primary_key = list(dataset.get("primary_key") or [])
        for field in dataset["fields"]:
            expression = only_snowflake(field["expression"])
            if expression != field["name"]:
                die(expression)
            datatype = field["datatype"]
            if dimension_for_datatype(datatype) != field.get("dimension"):
                die(field["name"])
            properties = {"dataType": OSSIE_TO_WISDOM[datatype]}
            if field["name"] in primary_key:
                properties["isPrimaryKey"] = True
            columns.append({"name": field["name"], "properties": properties})
        zsheet = {
            "ref": {"uuid": uuid, "name": dataset["name"], "version": "1"},
            "location": {
                "database": database,
                "schema": schema,
                "dbTable": table_name,
                "connectionId": CONNECTION_ID,
            },
            "primaryKey": {"columns": primary_key},
            "columns": columns,
        }
        if measures_by_dataset[dataset["name"]]:
            zsheet["measures"] = measures_by_dataset[dataset["name"]]
        tables.append({"zsheet_uuid": uuid, "zsheet_json": zsheet})
        table_metadata.append(
            {
                "zsheet_uuid": uuid,
                "connection_id": CONNECTION_ID,
                "database": database,
                "schema": schema,
                "table_name": table_name,
            }
        )

    return {
        "version": "1.0",
        "export_metadata": {
            "exported_at": EXPORTED_AT,
            "source_domain_id": SOURCE_DOMAIN_ID,
            "domain_name": name,
        },
        "domain": {
            "zsheet_json": {
                "ref": {"uuid": domain_uuid(name), "name": name, "version": "1"},
                "description": model["description"],
                "zsheetType": "DOMAIN",
                "domainSystemInstructions": model["description"],
                "knowledge": _knowledge_from_model(model),
                "relationshipGraph": {
                    "zsheets": zsheets,
                    "relationships": _ossie_relationships(model),
                },
            }
        },
        "tables": tables,
        "connections": [
            {
                "connection_id": CONNECTION_ID,
                "dialect": "snowflake",
                "name": "Snowflake",
            }
        ],
        "reviewed_queries": empty_item_list(),
        "synonym_sets": empty_item_list(),
        "table_metadata": table_metadata,
        "recommended_questions": [],
    }


def wisdom_to_ossie(export: dict) -> dict:
    reject_nonempty_synonym_sets(export)
    if export.get("version") != "1.0":
        die(str(export.get("version")))
    metadata = export.get("export_metadata") or {}
    if metadata.get("exported_at") != EXPORTED_AT:
        die("exported_at")
    if metadata.get("source_domain_id") != SOURCE_DOMAIN_ID:
        die("source_domain_id")
    domain = export["domain"]["zsheet_json"]
    name = domain["ref"]["name"]
    if metadata.get("domain_name") != name:
        die("domain_name")
    if domain.get("domainSystemInstructions") != domain.get("description"):
        die("domainSystemInstructions")
    if domain["ref"].get("uuid") != domain_uuid(name):
        die(domain["ref"].get("uuid", "uuid"))

    dataset_names = [table["zsheet_json"]["ref"]["name"] for table in export["tables"]]
    datasets = []
    metrics: list[dict] = []
    for table in export["tables"]:
        zsheet = table["zsheet_json"]
        if zsheet.get("formulas"):
            die("formulas")
        dataset = _wisdom_table_to_dataset(zsheet)
        datasets.append(dataset)
        if zsheet["ref"]["name"] != MANY_SIDE and zsheet.get("measures"):
            die("measures")
        if zsheet["ref"]["name"] == MANY_SIDE:
            for measure in zsheet.get("measures") or []:
                expression = measure.get("expression")
                if not isinstance(expression, str):
                    die("expression")
                check_metric_expression(expression, dataset_names)
                metrics.append(
                    {
                        "name": measure["name"],
                        "description": measure["description"],
                        "expression": snowflake_expression(expression),
                    }
                )

    instructions = None
    for item in domain.get("knowledge") or []:
        title = item.get("name", "")
        content = item.get("content", "")
        if title == BOARD_REVENUE:
            if instructions is not None:
                die(title)
            instructions = content
            continue
        if title.startswith(SYNONYM_PREFIX):
            metric_name = title[len(SYNONYM_PREFIX) :]
            metric = next((item for item in metrics if item["name"] == metric_name), None)
            if metric is None:
                die(title)
            if "ai_context" in metric:
                die(title)
            metric["ai_context"] = {"synonyms": _parse_bullets(content)}
            continue
        die(title or "knowledge")

    model = {
        "version": SPEC_VERSION,
        "name": name,
        "description": domain["description"],
    }
    if instructions:
        model["ai_context"] = {"instructions": instructions}
    model["datasets"] = datasets
    model["relationships"] = _wisdom_relationships(domain)
    model["metrics"] = metrics
    return model


def _knowledge_from_model(model: dict) -> list[dict]:
    items = []
    for metric in model.get("metrics") or []:
        synonyms = (metric.get("ai_context") or {}).get("synonyms")
        if not synonyms:
            continue
        title = f"{SYNONYM_PREFIX}{metric['name']}"
        items.append(
            {
                "name": title,
                "content": "\n".join(f"- {synonym}" for synonym in synonyms),
                "id": knowledge_id(title),
            }
        )
    instructions = (model.get("ai_context") or {}).get("instructions") if isinstance(model.get("ai_context"), dict) else None
    if instructions:
        items.append(
            {
                "name": BOARD_REVENUE,
                "content": instructions,
                "id": knowledge_id(BOARD_REVENUE),
            }
        )
    reject_duplicate_knowledge_ids(items)
    return items


def _ossie_relationships(model: dict) -> list[dict]:
    edges = []
    for relationship in model.get("relationships") or []:
        if relationship.get("from") != MANY_SIDE or relationship.get("to") != ONE_SIDE:
            die(str(relationship.get("name")))
        from_columns = relationship.get("from_columns") or []
        to_columns = relationship.get("to_columns") or []
        if len(from_columns) != 1 or len(to_columns) != 1:
            die("relationship_columns")
        left_ref = {"uuid": zsheet_uuid(relationship["from"]), "name": relationship["from"]}
        right_ref = {"uuid": zsheet_uuid(relationship["to"]), "name": relationship["to"]}
        edges.append(
            {
                "properties": {
                    "joinCondition": {
                        "leftColumn": {"name": from_columns[0], "zsheetRef": left_ref},
                        "rightColumn": {"name": to_columns[0], "zsheetRef": right_ref},
                    },
                    "relationshipType": "MANY_TO_ONE",
                },
                "leftDataSource": {"zsheet": left_ref},
                "rightDataSource": {"zsheet": right_ref},
            }
        )
    if len(edges) != 1:
        die("relationships")
    return edges


def _wisdom_relationships(domain: dict) -> list[dict]:
    edges = domain.get("relationshipGraph", {}).get("relationships") or []
    if len(edges) != 1:
        die("relationships")
    properties = edges[0].get("properties") or {}
    if "compoundJoinCondition" in properties:
        die("compoundJoinCondition")
    if properties.get("relationshipType") != "MANY_TO_ONE":
        die(str(properties.get("relationshipType", "relationshipType")))
    join = properties.get("joinCondition")
    if not isinstance(join, dict):
        die("joinCondition")
    left = join["leftColumn"]
    right = join["rightColumn"]
    left_name = left["zsheetRef"]["name"]
    right_name = right["zsheetRef"]["name"]
    if left_name != MANY_SIDE or right_name != ONE_SIDE:
        die(left_name)
    return [
        {
            "name": RELATIONSHIP_NAME,
            "from": left_name,
            "to": right_name,
            "from_columns": [left["name"]],
            "to_columns": [right["name"]],
        }
    ]


def _wisdom_table_to_dataset(zsheet: dict) -> dict:
    name = zsheet["ref"]["name"]
    if zsheet["ref"].get("uuid") != zsheet_uuid(name):
        die(zsheet["ref"].get("uuid", "uuid"))
    location = zsheet["location"]
    if location.get("connectionId") != CONNECTION_ID:
        die(str(location.get("connectionId", "connectionId")))
    fields = []
    for column in zsheet.get("columns") or []:
        data_type = (column.get("properties") or {}).get("dataType")
        if data_type not in WISDOM_TO_OSSIE:
            die(str(data_type))
        datatype = WISDOM_TO_OSSIE[data_type]
        expression = column["name"]
        check_expression(expression)
        if not isinstance(expression, str):
            die("expression")
        field = {
            "name": column["name"],
            "expression": snowflake_expression(expression),
            "datatype": datatype,
        }
        dimension = dimension_for_datatype(datatype)
        if dimension is not None:
            field["dimension"] = dimension
        fields.append(field)
    primary_key = (zsheet.get("primaryKey") or {}).get("columns")
    if not isinstance(primary_key, list) or not primary_key:
        die("primary_key")
    return {
        "name": name,
        "source": f"{location['database']}.{location['schema']}.{location['dbTable']}",
        "primary_key": list(primary_key),
        "fields": fields,
    }


def _split_source(source: str) -> tuple[str, str, str]:
    parts = source.split(".")
    if len(parts) != 3 or not all(parts):
        die(source)
    return parts[0], parts[1], parts[2]


def _parse_bullets(content: str) -> list[str]:
    lines = content.splitlines()
    if not lines:
        die("synonyms")
    synonyms = []
    for line in lines:
        if not line.startswith("- "):
            die(line)
        synonyms.append(line[2:])
    return synonyms
