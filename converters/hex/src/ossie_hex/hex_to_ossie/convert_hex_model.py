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

from ossie import OSIDataset, OSIDialect, OSIField, OSIMetric, OSIRelationship

from ..hex_types import (
    HexDimension,
    HexMeasure,
    HexModel,
    HexModelStash,
    HexRelation,
    maybe_write_extension,
)
from ..util.errors import ConversionWarning
from ..util.rewrite_refs import RefResolver
from .convert_hex_dimension import convert_hex_dimension
from .convert_hex_measure import convert_hex_measure
from .convert_hex_relation import convert_hex_relation
from .ref_resolver import export_ref_resolver


def convert_hex_model(
    model: HexModel,
    *,
    ossie_dialect: OSIDialect,
    metric_names: set[str],
    dim_ids_by_model: dict[str, set[str]],
) -> tuple[OSIDataset, list[OSIMetric], list[OSIRelationship], list[ConversionWarning]]:
    """Convert a Hex model to an Ossie dataset and the document-level entries it adds."""
    warnings: list[ConversionWarning] = []

    # Relations are converted first because whether a dimension's reference to
    # another model survives the trip back depends on which of them become
    # relationships.
    (
        relationships,
        unsupported_relations,
        relation_targets,
        relation_warnings,
    ) = convert_hex_model_relations(model)
    warnings.extend(relation_warnings)

    resolve = export_ref_resolver(
        model_id=model.id,
        relation_targets=relation_targets,
        dim_ids_by_model=dim_ids_by_model,
    )

    (
        fields,
        unsupported_dimensions,
        primary_key,
        unique_keys,
        dimension_warnings,
    ) = convert_hex_model_dimensions(
        model,
        ossie_dialect=ossie_dialect,
        resolve=resolve,
    )
    warnings.extend(dimension_warnings)

    metrics, unsupported_measures, measure_warnings = convert_hex_model_measures(
        model,
        ossie_dialect=ossie_dialect,
        metric_names=metric_names,
    )
    warnings.extend(measure_warnings)

    # even though our parsing does well, it's better to be safe and preserve
    source_kind = "table" if model.base_sql_table else "query"

    stash = HexModelStash(
        display_name=model.name,
        source_kind=source_kind,
        visibility=model.visibility,
        dimensions=unsupported_dimensions or None,
        measures=unsupported_measures or None,
        relations=unsupported_relations or None,
    )

    dataset = OSIDataset(
        name=model.id,
        source=model.base_sql_table or model.base_sql_query or "",
        primary_key=primary_key,
        unique_keys=unique_keys,
        description=model.description or None,
        fields=fields or None,
        custom_extensions=maybe_write_extension(stash),
    )

    return dataset, metrics, relationships, warnings


def convert_hex_model_relations(
    model: HexModel,
) -> tuple[
    list[OSIRelationship],
    list[HexRelation],
    dict[str, str],
    list[ConversionWarning],
]:
    """Convert a model's relations, and index the reachable ones by target model."""
    relationships: list[OSIRelationship] = []
    unsupported_relations: list[HexRelation] = []
    relation_targets: dict[str, str] = {}
    warnings: list[ConversionWarning] = []
    for relation in model.relations:
        relationship, unsupported_relation, relation_warnings = convert_hex_relation(
            relation, base_model_id=model.id
        )
        warnings.extend(relation_warnings)
        if relationship is not None:
            relationships.append(relationship)
            if relation.target != model.id:
                relation_targets.setdefault(relation.target, relation.id)
        elif unsupported_relation is not None:
            unsupported_relations.append(unsupported_relation)
    return relationships, unsupported_relations, relation_targets, warnings


def convert_hex_model_dimensions(
    model: HexModel,
    *,
    ossie_dialect: OSIDialect,
    resolve: RefResolver,
) -> tuple[
    list[OSIField],
    list[HexDimension],
    list[str] | None,
    list[list[str]] | None,
    list[ConversionWarning],
]:
    """Convert a model's dimensions, collecting the ones marked unique.

    Returns a tuple of:
    - ``fields``: Ossie fields.
    - ``unsupported_dimensions``: dimensions Ossie cannot express.
    - ``primary_key``: Ossie primary key, if any.
    - ``unique_keys``: Ossie unique keys, if any.
    - ``warnings``: Conversion warnings.
    """
    fields: list[OSIField] = []
    unsupported_dimensions: list[HexDimension] = []
    unique_field_names: list[str] = []

    warnings: list[ConversionWarning] = []
    for dim in model.dimensions:
        field, unsupported_dimension, dimension_warnings = convert_hex_dimension(
            dim,
            model_id=model.id,
            ossie_dialect=ossie_dialect,
            resolve=resolve,
        )
        warnings.extend(dimension_warnings)
        if field is not None:
            fields.append(field)
            if dim.unique:
                unique_field_names.append(dim.id)
        elif unsupported_dimension is not None:
            unsupported_dimensions.append(unsupported_dimension)

    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    if unique_field_names:
        # Hex doesn't have a concept of a primary key, so just use the first
        # unique field.
        primary_key = [unique_field_names[0]]
        # Hex marks each dimension unique on its own, and does not reflect composite keys
        unique_keys = [[name] for name in unique_field_names[1:]] or None

    return fields, unsupported_dimensions, primary_key, unique_keys, warnings


def convert_hex_model_measures(
    model: HexModel,
    *,
    ossie_dialect: OSIDialect,
    metric_names: set[str],
) -> tuple[list[OSIMetric], list[HexMeasure], list[ConversionWarning]]:
    """Convert a model's measures, setting aside the ones Ossie cannot express."""
    metrics: list[OSIMetric] = []
    unsupported_measures: list[HexMeasure] = []
    warnings: list[ConversionWarning] = []
    for measure in model.measures:
        metric, unsupported_measure, measure_warnings = convert_hex_measure(
            measure,
            model_id=model.id,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
        )
        warnings.extend(measure_warnings)
        if metric is not None:
            metrics.append(metric)
        elif unsupported_measure is not None:
            unsupported_measures.append(unsupported_measure)
    return metrics, unsupported_measures, warnings
