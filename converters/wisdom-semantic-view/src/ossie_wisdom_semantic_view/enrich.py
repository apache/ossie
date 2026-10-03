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

"""The only place a name that was not in the semantic view is added."""

from __future__ import annotations

import copy

from ossie_wisdom_semantic_view.model import check_metric_expression, die, require_keys
from ossie_wisdom_semantic_view.semantic_view import MANY_SIDE
from ossie_wisdom_semantic_view.wisdom import knowledge_id, measure_id, reject_nonempty_synonym_sets

_ENRICHMENT_KEYS = {"metric", "knowledge"}
_METRIC_KEYS = {"name", "expr", "description"}
_KNOWLEDGE_KEYS = {"name", "content"}


def load_enrichment(text: str) -> dict:
    import yaml

    loaded = yaml.safe_load(text)
    require_keys(loaded, _ENRICHMENT_KEYS)
    for key in ("metric", "knowledge"):
        if key not in loaded:
            die(key)
    return loaded


def apply_enrichment(export: dict, enrichment: dict) -> dict:
    require_keys(enrichment, _ENRICHMENT_KEYS)
    metric = require_keys(enrichment["metric"], _METRIC_KEYS)
    for key in ("name", "expr", "description"):
        if key not in metric:
            die(key)
    dataset_names = [table["zsheet_json"]["ref"]["name"] for table in export["tables"]]
    expression = metric["expr"]
    if not isinstance(expression, str):
        die("expr")
    check_metric_expression(expression, dataset_names)

    enriched = copy.deepcopy(export)
    reject_nonempty_synonym_sets(enriched)
    bookings = None
    for table in enriched["tables"]:
        zsheet = table["zsheet_json"]
        if zsheet["ref"]["name"] == MANY_SIDE:
            bookings = zsheet
    if bookings is None:
        die(MANY_SIDE)
    measures = bookings.setdefault("measures", [])
    if any(item["name"] == metric["name"] for item in measures):
        die(metric["name"])
    measures.append(
        {
            "name": metric["name"],
            "expression": expression,
            "description": metric["description"],
            "id": measure_id(metric["name"]),
        }
    )

    knowledge = enriched["domain"]["zsheet_json"].setdefault("knowledge", [])
    blocks = enrichment["knowledge"]
    if not isinstance(blocks, list) or not blocks:
        die("knowledge")
    for block in blocks:
        require_keys(block, _KNOWLEDGE_KEYS)
        for key in ("name", "content"):
            if key not in block:
                die(key)
        content = block["content"]
        if not isinstance(content, str):
            die("content")
        content = content.rstrip("\n")
        knowledge.append(
            {
                "name": block["name"],
                "content": content,
                "id": knowledge_id(block["name"]),
            }
        )
    reject_nonempty_synonym_sets(enriched)
    return enriched
