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

"""Convert GoodData declarative LDM to Ossie semantic model."""

from __future__ import annotations

import json
from typing import Any

from ossie_gooddata.models import (
    GdAttribute,
    GdDataset,
    GdDateInstance,
    GdDeclarativeModel,
    GdFact,
    GdLabel,
    GdReference,
)

OSI_VERSION = "0.2.0.dev0"


def gooddata_to_osi(
    model: GdDeclarativeModel,
    model_name: str = "gooddata_model",
    model_description: str = "",
    data_source_id: str | None = None,
) -> dict[str, Any]:
    """Convert a GoodData declarative model to an Ossie semantic model dict.

    Args:
        model: Parsed GoodData declarative model.
        model_name: Name for the Ossie semantic model.
        model_description: Description for the Ossie semantic model.
        data_source_id: Data source ID to include in custom_extensions.

    Returns:
        Ossie semantic model as a dict (ready to serialize to YAML).
    """
    osi_datasets = []
    osi_relationships = []

    # Map dataset id -> {attribute id -> source column}. Reference targets point
    # at a grain attribute id; Ossie relationships need the physical column name.
    attr_source_col_map: dict[str, dict[str, str]] = {}
    for ds in model.ldm.datasets:
        attr_source_col_map[ds.id] = {a.id: a.source_column for a in ds.attributes}

    # Convert regular datasets
    for ds in model.ldm.datasets:
        osi_ds, rels = _convert_dataset(ds, attr_source_col_map)
        osi_datasets.append(osi_ds)
        osi_relationships.extend(rels)

    # Convert date instances to datasets
    for di in model.ldm.date_instances:
        osi_datasets.append(_convert_date_instance(di))

    semantic_model: dict[str, Any] = {
        "name": model_name,
        "datasets": osi_datasets,
    }
    if model_description:
        semantic_model["description"] = model_description
    if osi_relationships:
        semantic_model["relationships"] = osi_relationships

    # Model-level custom_extensions for GoodData workspace metadata
    extensions = []
    if data_source_id:
        extensions.append(
            {
                "vendor_name": "GOODDATA",
                "data": json.dumps({"data_source_id": data_source_id}),
            }
        )
    if extensions:
        semantic_model["custom_extensions"] = extensions

    return {
        "version": OSI_VERSION,
        "semantic_model": [semantic_model],
    }


def _convert_dataset(
    ds: GdDataset,
    attr_source_col_map: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert a GoodData dataset to an Ossie dataset + relationships."""
    # Build source from dataSourceTableId
    source = _build_source(ds)

    # Primary key = grain attribute source columns
    primary_key = [attr.source_column for attr in ds.attributes if any(g.id == attr.id for g in ds.grain)]

    fields: list[dict[str, Any]] = []
    for attr in ds.attributes:
        fields.append(_convert_attribute(attr, ds.id))
    for fact in ds.facts:
        fields.append(_convert_fact(fact, ds.id))

    osi_ds: dict[str, Any] = {
        "name": ds.id,
        "source": source,
    }
    if primary_key:
        osi_ds["primary_key"] = primary_key
    if ds.description:
        osi_ds["description"] = ds.description
    if ds.title != ds.id:
        osi_ds["ai_context"] = {"synonyms": [ds.title]}
    if fields:
        osi_ds["fields"] = fields

    # Convert references to Ossie relationships
    relationships = []
    for ref in ds.references:
        rel = _convert_reference(ds.id, ref, attr_source_col_map)
        relationships.append(rel)

    return osi_ds, relationships


def _convert_date_instance(di: GdDateInstance) -> dict[str, Any]:
    """Convert a GoodData date instance to an Ossie dataset."""
    osi_ds: dict[str, Any] = {
        "name": di.id,
        "source": di.id,
    }
    if di.description:
        osi_ds["description"] = di.description
    if di.title != di.id:
        osi_ds["ai_context"] = {"synonyms": [di.title]}

    # Store date-specific metadata in custom_extensions
    ext_data: dict[str, Any] = {
        "date_dimension": True,
        "granularities": di.granularities,
    }
    osi_ds["custom_extensions"] = [
        {"vendor_name": "GOODDATA", "data": json.dumps(ext_data)},
    ]

    return osi_ds


def _convert_attribute(attr: GdAttribute, dataset_id: str) -> dict[str, Any]:
    """Convert a GoodData attribute to an Ossie field."""
    osi_field: dict[str, Any] = {
        "name": attr.source_column,
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": attr.source_column},
                {"dialect": "MAQL", "expression": f"{{label/{dataset_id}.{attr.id}}}"},
            ],
        },
        "dimension": {"is_time": False},
    }
    if attr.description:
        osi_field["description"] = attr.description
    if attr.title != attr.source_column:
        osi_field.setdefault("ai_context", {})["synonyms"] = [attr.title]

    # Store GoodData-specific attribute metadata in custom_extensions
    ext = _build_attribute_extension(attr)
    if ext:
        osi_field["custom_extensions"] = [{"vendor_name": "GOODDATA", "data": json.dumps(ext)}]

    return osi_field


def _convert_fact(fact: GdFact, dataset_id: str) -> dict[str, Any]:
    """Convert a GoodData fact to an Ossie field."""
    osi_field: dict[str, Any] = {
        "name": fact.source_column,
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": fact.source_column},
                {"dialect": "MAQL", "expression": f"{{fact/{dataset_id}.{fact.id}}}"},
            ],
        },
    }
    if fact.description:
        osi_field["description"] = fact.description
    if fact.title != fact.source_column:
        osi_field.setdefault("ai_context", {})["synonyms"] = [fact.title]

    return osi_field


def _convert_reference(
    from_dataset: str,
    ref: GdReference,
    attr_source_col_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Convert a GoodData reference to an Ossie relationship.

    Each source carries an explicit `target` grain identifier. For attribute
    targets the target column is resolved via the target dataset's attribute
    id -> source_column map. For date targets (pointing at a date instance,
    which has no physical columns) the source column is used as-is.
    """
    to_dataset = ref.identifier.id
    target_cols_by_ds = attr_source_col_map.get(to_dataset, {})

    from_columns: list[str] = []
    to_columns: list[str] = []
    for s in ref.sources:
        from_columns.append(s.column)
        if s.target.type == "attribute":
            col = target_cols_by_ds.get(s.target.id)
            if col is None:
                raise ValueError(
                    f"Reference {from_dataset} -> {to_dataset}: target attribute "
                    f"'{s.target.id}' not found in target dataset."
                )
            to_columns.append(col)
        else:
            to_columns.append(s.column)

    rel: dict[str, Any] = {
        "name": f"{from_dataset}_to_{to_dataset}",
        "from": from_dataset,
        "to": to_dataset,
        "from_columns": from_columns,
        "to_columns": to_columns,
    }
    if ref.multivalue:
        rel["custom_extensions"] = [
            {"vendor_name": "GOODDATA", "data": json.dumps({"multivalue": True})},
        ]
    return rel


def _build_source(ds: GdDataset) -> str:
    """Build an Ossie source string from a GoodData dataset's data source table."""
    if ds.data_source_table_id:
        t = ds.data_source_table_id
        if t.path:
            return ".".join([t.data_source_id, *t.path])
        return f"{t.data_source_id}.{t.id}"
    return ds.id


def _build_attribute_extension(attr: GdAttribute) -> dict[str, Any]:
    """Build GoodData custom extension data for an attribute's extra metadata."""
    ext: dict[str, Any] = {"field_type": "attribute"}
    if attr.labels:
        ext["labels"] = [_label_ext(lb) for lb in attr.labels]
    if attr.sort_column:
        ext["sort_column"] = attr.sort_column
    if attr.sort_direction:
        ext["sort_direction"] = attr.sort_direction
    return ext


def _label_ext(lb: GdLabel) -> dict[str, Any]:
    d: dict[str, Any] = {"id": lb.id, "source_column": lb.source_column}
    if lb.value_type != "TEXT":
        d["value_type"] = lb.value_type
    return d
