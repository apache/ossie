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

from ossie import OSIDialect, OSIRelationship, OSISemanticModel

from ..hex_types import HexModel, HexProjectStash, HexResource, read_stash
from ..util.errors import ConversionError, ConversionWarning
from .assign_ossie_metrics import assign_ossie_metrics
from .convert_ossie_dataset import convert_ossie_dataset
from .hex_ids import dataset_hex_ids, dimension_hex_ids
from .relationship_sides import relationship_sides
from .restore_hex_views import restore_hex_views


def convert_ossie_semantic_model(
    ossie_semantic_model: OSISemanticModel,
    ossie_dialect: OSIDialect,
    *,
    base_model: str | None = None,
    warnings: list[ConversionWarning],
) -> tuple[list[HexResource], list[ConversionWarning]]:
    """Convert Ossie semantic model to a Hex project

    Returns ``(hex_resources, warnings)``.
    """

    # Ossie names are free-form while Hex refs address Hex IDs, so resolve every
    # dataset up front and route relationship, metric, and expression qualifiers
    # through this map.
    taken_hex_resource_ids: set[str] = set()
    hex_ids_by_dataset = dataset_hex_ids(
        ossie_semantic_model, taken=taken_hex_resource_ids
    )
    # Relationship columns name fields on both sides, so every dataset's field
    # IDs must be resolvable before any single dataset is converted.
    dim_ids_by_dataset = {
        ds.name: dimension_hex_ids(ds) for ds in ossie_semantic_model.datasets
    }

    base_model_id = (
        hex_ids_by_dataset.get(base_model, base_model) if base_model else None
    )
    # Checked before use rather than where metrics are attached: an unknown name
    # would otherwise key `metrics_by_dataset` to a model no dataset reads back,
    # silently dropping every metric that fell through to it.
    if base_model_id is not None and base_model_id not in set(
        hex_ids_by_dataset.values()
    ):
        raise ConversionError(
            f"--base-model '{base_model}' does not name a dataset in "
            f"semantic model '{ossie_semantic_model.name}'"
        )

    # Index relationships by the Hex base (source) model.
    relations_by_dataset: dict[str, list[OSIRelationship]] = {}
    for rel in ossie_semantic_model.relationships or []:
        local = relationship_sides(rel).local_dataset
        base = hex_ids_by_dataset.get(local, local)
        relations_by_dataset.setdefault(base, []).append(rel)

    metrics_by_dataset = assign_ossie_metrics(
        ossie_semantic_model,
        hex_ids_by_dataset=hex_ids_by_dataset,
        base_model_id=base_model_id,
        preferred_dialect=ossie_dialect,
    )

    hex_models: list[HexModel] = []
    for dataset in ossie_semantic_model.datasets:
        hex_id = hex_ids_by_dataset[dataset.name]
        hex_model = convert_ossie_dataset(
            dataset,
            hex_id=hex_id,
            hex_ids_by_dataset=hex_ids_by_dataset,
            dim_ids_by_dataset=dim_ids_by_dataset,
            preferred_dialect=ossie_dialect,
            relationships=relations_by_dataset.get(hex_id, []),
            metrics=metrics_by_dataset.get(hex_id, []),
            warnings=warnings,
        )
        hex_models.append(hex_model)

    hex_project_stash = read_stash(
        ossie_semantic_model.custom_extensions, HexProjectStash
    )
    hex_views = restore_hex_views(hex_project_stash, taken_ids=taken_hex_resource_ids)
    hex_resources = hex_models + hex_views
    return hex_resources, warnings
