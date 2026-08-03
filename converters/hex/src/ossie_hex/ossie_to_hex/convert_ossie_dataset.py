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

from __future__ import annotations

import re
from typing import Any, Literal, assert_never

from ossie import OSIDataset, OSIDialect, OSIMetric, OSIRelationship

from ..hex_extension import HexModelStash, read_stash
from ..hex_types import (
    HexDataType,
    HexDimension,
    HexMeasure,
    HexModel,
    HexRelation,
    HexVisibility,
    id_to_name,
    normalize_to_hex_id,
)
from ..util.errors import ConversionWarning
from ..util.rewrite_refs import RefResolver
from .convert_ossie_field import convert_ossie_field
from .convert_ossie_metric import convert_ossie_metric
from .convert_ossie_relationship import convert_ossie_relationship
from .ref_resolver import ref_resolver
from .relationship_sides import relationship_sides


def convert_ossie_dataset(
    dataset: OSIDataset,
    *,
    hex_id: str,
    hex_ids_by_dataset: dict[str, str],
    dim_ids_by_dataset: dict[str, dict[str, str]],
    preferred_dialect: OSIDialect,
    relationships: list[OSIRelationship],
    metrics: list[OSIMetric],
    warnings: list[ConversionWarning],
) -> HexModel:
    """Convert an Ossie dataset, with its relationships and metrics, to a Hex model."""
    stash = read_stash(dataset.custom_extensions, HexModelStash)
    resource: dict[str, Any] = {"id": hex_id}

    source_kind = (
        stash.source_kind if stash is not None else guess_source_kind(dataset.source)
    )
    if source_kind == "table":
        resource["base_sql_table"] = dataset.source
    elif source_kind == "query":
        resource["base_sql_query"] = dataset.source
    else:
        assert_never(source_kind)

    if stash is not None and stash.display_name != id_to_name(hex_id):
        resource["name"] = stash.display_name
    if dataset.description:
        resource["description"] = dataset.description
    if stash is not None and stash.visibility is not None:
        resource["visibility"] = stash.visibility

    # Ordered so synthesized dimensions below land in a stable position; set
    # iteration here would reorder the emitted YAML between runs.
    unique_names = dict.fromkeys(dataset.primary_key or [])
    for key in dataset.unique_keys or []:
        unique_names.update(dict.fromkeys(key))

    dim_id_by_field = dim_ids_by_dataset[dataset.name]
    # Dimensions, relations, and measures share one ID namespace, so they draw
    # from a single set of taken names.
    taken_ids = set(dim_id_by_field.values())

    unsupported_dimensions = stash.dimensions if stash is not None else None
    # Reserved before anything else is converted so a relation, metric, or
    # synthesized key dimension cannot be coerced onto an ID a preserved
    # dimension is about to reclaim.
    taken_ids.update(dimension.id for dimension in unsupported_dimensions or [])

    # Relations come first: Hex reaches another model through a relation ID, so
    # measure and dimension expressions cannot be rewritten until these exist.
    relations, relation_ids_by_target = convert_ossie_relationships_by_dataset(
        relationships,
        dataset=dataset,
        hex_id=hex_id,
        hex_ids_by_dataset=hex_ids_by_dataset,
        dim_ids_by_dataset=dim_ids_by_dataset,
        taken_ids=taken_ids,
        warnings=warnings,
    )

    resolve = ref_resolver(
        dataset_name=dataset.name,
        dim_ids_by_dataset=dim_ids_by_dataset,
        relation_ids_by_target=relation_ids_by_target,
    )

    dimensions = convert_ossie_dataset_fields(
        dataset,
        hex_id=hex_id,
        dim_id_by_field=dim_id_by_field,
        unique_names=unique_names,
        preferred_dialect=preferred_dialect,
        resolve=resolve,
        taken_ids=taken_ids,
        warnings=warnings,
    )
    dimensions.extend(unsupported_dimensions or [])

    if dimensions:
        resource["dimensions"] = dimensions

    unsupported_measures = stash.measures if stash is not None else None
    # Reserved before the metrics are converted so a metric cannot be coerced
    # onto an ID a preserved measure is about to reclaim.
    taken_ids.update(measure.id for measure in unsupported_measures or [])

    measures = convert_ossie_metrics_by_dataset(
        metrics,
        dataset=dataset,
        hex_id=hex_id,
        hex_ids_by_dataset=hex_ids_by_dataset,
        relation_ids_by_target=relation_ids_by_target,
        resolve=resolve,
        preferred_dialect=preferred_dialect,
        taken_ids=taken_ids,
        warnings=warnings,
    )
    # Passing the recorded measures through untouched keeps ``exclude_unset``
    # able to tell authored fields from derived defaults.
    measures.extend(unsupported_measures or [])

    if measures:
        resource["measures"] = measures

    unsupported_relations = stash.relations if stash is not None else None
    for relation in unsupported_relations or []:
        if relation.id in taken_ids:
            continue
        taken_ids.add(relation.id)
        relations.append(relation)

    if relations:
        resource["relations"] = relations

    return HexModel(**resource)


def convert_ossie_relationships_by_dataset(
    relationships: list[OSIRelationship],
    *,
    dataset: OSIDataset,
    hex_id: str,
    hex_ids_by_dataset: dict[str, str],
    dim_ids_by_dataset: dict[str, dict[str, str]],
    taken_ids: set[str],
    warnings: list[ConversionWarning],
) -> tuple[list[HexRelation], dict[str, str]]:
    """Convert Ossie Relationships to Hex Relations according to the dataset they reach."""
    relations: list[HexRelation] = []
    relation_ids_by_target: dict[str, str] = {}
    for relationship in relationships:
        relation, relationship_warnings = convert_ossie_relationship(
            relationship,
            base_dataset=hex_id,
            hex_ids_by_dataset=hex_ids_by_dataset,
            dim_ids_by_dataset=dim_ids_by_dataset,
            taken=taken_ids,
        )
        relations.append(relation)
        warnings.extend(relationship_warnings)
        target_dataset = relationship_sides(relationship).remote_dataset
        if target_dataset != dataset.name:
            relation_ids_by_target.setdefault(target_dataset, relation.id)
    return relations, relation_ids_by_target


def convert_ossie_dataset_fields(
    dataset: OSIDataset,
    *,
    hex_id: str,
    dim_id_by_field: dict[str, str],
    unique_names: dict[str, None],
    preferred_dialect: OSIDialect,
    resolve: RefResolver,
    taken_ids: set[str],
    warnings: list[ConversionWarning],
) -> list[HexDimension]:
    """Convert Ossie Fields to Hex Dimensions, adding any key column that has no field."""
    dimensions: list[HexDimension] = []
    for field in dataset.fields or []:
        # Ossie fields become Hex dimensions whether or not they carry a
        # `dimension` block, so the Hex model keeps every column.
        dimension, field_warnings = convert_ossie_field(
            field,
            dim_id=dim_id_by_field[field.name],
            unique_names=unique_names.keys(),
            preferred_dialect=preferred_dialect,
            dataset_id=hex_id,
            dataset_name=dataset.name,
            resolve=resolve,
        )
        dimensions.append(dimension)
        warnings.extend(field_warnings)

    # Ensure key columns exist as unique dimensions. Keys name Ossie fields, so
    # match on the field name as well as the ID it was coerced to.
    existing_ids = set(dim_id_by_field) | set(dim_id_by_field.values())
    for key_name in unique_names:
        if key_name not in existing_ids:
            dim_id = normalize_to_hex_id(key_name, "dimension", taken_ids)
            dimensions.append(
                HexDimension(
                    id=dim_id,
                    type=HexDataType.STRING,
                    unique=True,
                    visibility=HexVisibility.INTERNAL,
                )
            )
            warnings.append(
                ConversionWarning(
                    f"dataset '{dataset.name}' key column '{key_name}' has no field; "
                    f"added dimension '{dim_id}' typed as string"
                )
            )
    return dimensions


def convert_ossie_metrics_by_dataset(
    metrics: list[OSIMetric],
    *,
    dataset: OSIDataset,
    hex_id: str,
    hex_ids_by_dataset: dict[str, str],
    relation_ids_by_target: dict[str, str],
    resolve: RefResolver,
    preferred_dialect: OSIDialect,
    taken_ids: set[str],
    warnings: list[ConversionWarning],
) -> list[HexMeasure]:
    """Convert Ossie Metrics to Hex Measures according to the dataset they reach."""
    foreign_names = {n for n in hex_ids_by_dataset if n != dataset.name}
    measures: list[HexMeasure] = []
    for metric in metrics:
        measure, metric_warnings = convert_ossie_metric(
            metric,
            dataset_id=hex_id,
            foreign_names=foreign_names,
            relation_ids_by_target=relation_ids_by_target,
            resolve=resolve,
            preferred_dialect=preferred_dialect,
            taken=taken_ids,
        )
        measures.append(measure)
        warnings.extend(metric_warnings)
    return measures


_TABLE_REF_PART = r'(?:"(?:[^"]|"")+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_$-]*)'
_TABLE_REF_RE = re.compile(rf"^{_TABLE_REF_PART}(?:\s*\.\s*{_TABLE_REF_PART}){{0,3}}$")


def guess_source_kind(source: str) -> Literal["table", "query"]:
    """Determine whether an Ossie Dataset source field is a reference to a table or view.

    As opposed to a query (harder to match)."""
    stripped = source.strip()
    if not stripped:
        return "query"
    if _TABLE_REF_RE.match(stripped):
        return "table"
    return "query"
