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

"""Convert Ossie semantic model to GoodData declarative LDM."""

from __future__ import annotations

import json
import re
from typing import Any

from ossie_gooddata.datatype_mapping import (
    is_temporal_ossie_datatype,
    ossie_to_gooddata_datatype,
)
from ossie_gooddata.models import (
    GdAttribute,
    GdDataset,
    GdDataSourceTableId,
    GdDateInstance,
    GdDeclarativeModel,
    GdFact,
    GdGrain,
    GdGranularitiesFormatting,
    GdLabel,
    GdLdm,
    GdReference,
    GdReferenceIdentifier,
    GdReferenceSource,
    GdReferenceTarget,
)

# Regex to extract fact/label references from MAQL expressions
_MAQL_LABEL_RE = re.compile(r"\{label/([^.]+)\.([^}]+)\}")
_MAQL_FACT_RE = re.compile(r"\{fact/([^.]+)\.([^}]+)\}")


def osi_to_gooddata(
    osi_model: dict[str, Any],
    data_source_id: str = "default",
) -> GdDeclarativeModel:
    """Convert an Ossie semantic model dict to a GoodData declarative model.

    Args:
        osi_model: Parsed Ossie YAML as a dict.
        data_source_id: GoodData data source ID to use for table references.

    Returns:
        GdDeclarativeModel ready to serialize and PUT to the declarative API.
    """
    datasets: list[GdDataset] = []
    date_instances: list[GdDateInstance] = []

    for sm in osi_model.get("semantic_model", []):
        relationship_map = _build_relationship_map(sm)
        # Pre-pass: for each Ossie dataset, record whether it is a date instance
        # and map its physical source columns to the attribute ids that will
        # be generated. Reference target columns resolve via this map.
        target_info = _build_target_info(sm)

        for ds in sm.get("datasets", []):
            gd_ds, date_inst = _convert_osi_dataset(
                ds, relationship_map, target_info, data_source_id,
            )
            if date_inst:
                date_instances.append(date_inst)
            else:
                datasets.append(gd_ds)

    return GdDeclarativeModel(ldm=GdLdm(datasets=datasets, date_instances=date_instances))


def _build_target_info(sm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """For each dataset: {is_date: bool, col_to_attr_id: {source_col -> attr id}}."""
    info: dict[str, dict[str, Any]] = {}
    for ds in sm.get("datasets", []):
        ds_name = ds["name"]
        is_date = _is_date_dataset(ds)
        col_to_attr: dict[str, str] = {}
        if not is_date:
            for f in ds.get("fields", []):
                src = _get_source_column(f)
                col_to_attr[src] = f"attr.{ds_name}.{f['name']}"
        info[ds_name] = {"is_date": is_date, "col_to_attr": col_to_attr}
    return info


def _is_date_dataset(ds: dict[str, Any]) -> bool:
    gd_ext = _get_gooddata_extension(ds)
    if gd_ext and gd_ext.get("date_dimension"):
        return True
    fields = ds.get("fields", [])
    return bool(fields) and all(_is_time_field(f) for f in fields) and not gd_ext


def _build_relationship_map(
    sm: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build a map of from_dataset_name -> list of relationship dicts."""
    rel_map: dict[str, list[dict[str, Any]]] = {}
    for rel in sm.get("relationships", []):
        from_ds = rel["from"]
        rel_map.setdefault(from_ds, []).append(rel)
    return rel_map


def _convert_osi_dataset(
    ds: dict[str, Any],
    relationship_map: dict[str, list[dict[str, Any]]],
    target_info: dict[str, dict[str, Any]],
    data_source_id: str,
) -> tuple[GdDataset, GdDateInstance | None]:
    """Convert an Ossie dataset to a GoodData dataset or date instance."""
    ds_name = ds["name"]
    source = ds.get("source", ds_name)

    # Check if this is a date dimension via custom_extensions
    gd_ext = _get_gooddata_extension(ds)
    if gd_ext and gd_ext.get("date_dimension"):
        return _placeholder_dataset(ds_name), _convert_to_date_instance(ds, gd_ext)

    # Check if all fields are time dimensions — heuristic for date datasets
    fields = ds.get("fields", [])
    all_time = fields and all(_is_time_field(f) for f in fields)
    if all_time and not gd_ext:
        return _placeholder_dataset(ds_name), _convert_to_date_instance_from_fields(ds)

    # Regular dataset
    attributes: list[GdAttribute] = []
    facts: list[GdFact] = []
    grain_ids: list[str] = []

    pk_columns = set(ds.get("primary_key", []))

    for field_def in fields:
        field_name = field_def["name"]
        is_dimension = field_def.get("dimension") is not None

        if is_dimension:
            attr = _convert_to_attribute(field_def, ds_name)
            attributes.append(attr)
            if field_name in pk_columns:
                grain_ids.append(attr.id)
        else:
            # Check MAQL expression to determine if fact or attribute
            maql_type = _detect_type_from_maql(field_def)
            if maql_type == "attribute":
                attr = _convert_to_attribute(field_def, ds_name)
                attributes.append(attr)
                if field_name in pk_columns:
                    grain_ids.append(attr.id)
            else:
                facts.append(_convert_to_fact(field_def, ds_name))

    grain = [GdGrain(id=gid, type="attribute") for gid in grain_ids]

    # Convert relationships from this dataset to GoodData references
    references = []
    for rel in relationship_map.get(ds_name, []):
        references.append(_convert_relationship(rel, target_info))

    # Build data source table reference from source string
    ds_table_id = _parse_source_to_table_id(source, data_source_id)

    return (
        GdDataset(
            id=ds_name,
            title=_get_title(ds),
            grain=grain,
            references=references,
            attributes=attributes,
            facts=facts,
            description=ds.get("description", ""),
            data_source_table_id=ds_table_id,
        ),
        None,
    )


def _convert_to_date_instance(ds: dict[str, Any], gd_ext: dict[str, Any]) -> GdDateInstance:
    """Convert an Ossie dataset with date_dimension extension to a GoodData date instance."""
    return GdDateInstance(
        id=ds["name"],
        title=_get_title(ds),
        description=ds.get("description", ""),
        granularities=gd_ext.get("granularities", ["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"]),
        granularities_formatting=GdGranularitiesFormatting(),
    )


def _convert_to_date_instance_from_fields(ds: dict[str, Any]) -> GdDateInstance:
    """Convert an all-time-dimension Ossie dataset to a GoodData date instance."""
    return GdDateInstance(
        id=ds["name"],
        title=_get_title(ds),
        description=ds.get("description", ""),
        granularities=["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"],
        granularities_formatting=GdGranularitiesFormatting(),
    )


def _convert_to_attribute(field_def: dict[str, Any], dataset_id: str) -> GdAttribute:
    """Convert an Ossie field (dimension) to a GoodData attribute."""
    field_name = field_def["name"]
    source_col = _get_source_column(field_def)
    attr_id = f"attr.{dataset_id}.{field_name}"

    # Check for label info in custom_extensions
    gd_ext = _get_gooddata_extension(field_def)
    labels: list[GdLabel] = []
    if gd_ext and "labels" in gd_ext:
        for lb_def in gd_ext["labels"]:
            labels.append(
                GdLabel(
                    id=lb_def["id"],
                    title=lb_def.get("title", lb_def["id"]),
                    source_column=lb_def.get("source_column", source_col),
                    value_type=lb_def.get("value_type", "TEXT"),
                )
            )

    return GdAttribute(
        id=attr_id,
        title=_get_title(field_def, fallback=field_name),
        source_column=source_col,
        description=field_def.get("description", ""),
        source_column_data_type=ossie_to_gooddata_datatype(
            field_def.get("datatype"),
            default="STRING",
            field_name=field_name,
            extension_type=gd_ext.get("source_column_data_type") if gd_ext else None,
        ),
        sort_column=gd_ext.get("sort_column") if gd_ext else None,
        sort_direction=gd_ext.get("sort_direction") if gd_ext else None,
        labels=labels,
    )


def _convert_to_fact(field_def: dict[str, Any], dataset_id: str) -> GdFact:
    """Convert an Ossie field (non-dimension) to a GoodData fact."""
    field_name = field_def["name"]
    source_col = _get_source_column(field_def)
    gd_ext = _get_gooddata_extension(field_def)

    return GdFact(
        id=f"fact.{dataset_id}.{field_name}",
        title=_get_title(field_def, fallback=field_name),
        source_column=source_col,
        description=field_def.get("description", ""),
        source_column_data_type=ossie_to_gooddata_datatype(
            field_def.get("datatype"),
            default="NUMERIC",
            field_name=field_name,
            extension_type=gd_ext.get("source_column_data_type") if gd_ext else None,
        ),
    )


def _get_source_column(field_def: dict[str, Any]) -> str:
    """Extract the source column name from an Ossie field's ANSI_SQL expression."""
    for dialect_expr in field_def.get("expression", {}).get("dialects", []):
        if dialect_expr.get("dialect") == "ANSI_SQL":
            return dialect_expr["expression"]
    return field_def["name"]


def _detect_type_from_maql(field_def: dict[str, Any]) -> str:
    """Detect whether a field is an attribute or fact from its MAQL expression."""
    for dialect_expr in field_def.get("expression", {}).get("dialects", []):
        if dialect_expr.get("dialect") == "MAQL":
            expr = dialect_expr["expression"]
            if _MAQL_FACT_RE.search(expr):
                return "fact"
            if _MAQL_LABEL_RE.search(expr):
                return "attribute"
    return "fact"


def _get_gooddata_extension(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the GOODDATA custom extension data from an Ossie object."""
    for ext in obj.get("custom_extensions", []):
        if ext.get("vendor_name") == "GOODDATA":
            data = ext.get("data", "{}")
            if isinstance(data, str):
                return json.loads(data)
            return data
    return None


def _get_title(obj: dict[str, Any], fallback: str = "") -> str:
    """Get a title from an Ossie object, preferring ai_context synonyms."""
    ctx = obj.get("ai_context")
    if isinstance(ctx, dict):
        synonyms = ctx.get("synonyms", [])
        if synonyms:
            return synonyms[0]
    return obj.get("description", "") or obj.get("name", "") or fallback


def _is_time_field(field_def: dict[str, Any]) -> bool:
    dim = field_def.get("dimension")
    if not isinstance(dim, dict):
        return False
    is_time = dim.get("is_time")
    if isinstance(is_time, bool):
        return is_time
    return is_temporal_ossie_datatype(field_def.get("datatype"))


def _convert_relationship(
    rel: dict[str, Any],
    target_info: dict[str, dict[str, Any]],
) -> GdReference:
    to_ds = rel["to"]
    from_columns: list[str] = rel.get("from_columns", [])
    to_columns: list[str] = rel.get("to_columns", from_columns)

    target_meta = target_info.get(to_ds, {"is_date": False, "col_to_attr": {}})
    sources: list[GdReferenceSource] = []
    for from_col, to_col in zip(from_columns, to_columns):
        if target_meta["is_date"]:
            target = GdReferenceTarget(id=to_ds, type="date")
        else:
            attr_id = target_meta["col_to_attr"].get(to_col)
            if attr_id is None:
                raise ValueError(
                    f"Relationship '{rel.get('name', from_col)}': target column "
                    f"'{to_col}' not found as a field of dataset '{to_ds}'."
                )
            target = GdReferenceTarget(id=attr_id, type="attribute")
        sources.append(GdReferenceSource(column=from_col, target=target))

    return GdReference(
        identifier=GdReferenceIdentifier(id=to_ds, type="dataset"),
        sources=sources,
        multivalue=_is_multivalue(rel),
    )


def _is_multivalue(rel: dict[str, Any]) -> bool:
    gd_ext = _get_gooddata_extension(rel)
    return bool(gd_ext and gd_ext.get("multivalue"))


def _parse_source_to_table_id(source: str, data_source_id: str) -> GdDataSourceTableId:
    """Parse an Ossie source string into a GoodData DataSourceTableId."""
    parts = source.split(".")
    if len(parts) >= 3:
        # source_id.schema.table or more
        return GdDataSourceTableId(
            id=parts[-1],
            data_source_id=parts[0],
            path=parts[1:],
        )
    if len(parts) == 2:
        return GdDataSourceTableId(
            id=parts[-1],
            data_source_id=parts[0],
            path=[parts[-1]],
        )
    return GdDataSourceTableId(
        id=source,
        data_source_id=data_source_id,
    )


def _placeholder_dataset(name: str) -> GdDataset:
    """Return an empty placeholder — only used as first element of the tuple when returning a date instance."""
    return GdDataset(id=name, title=name)
