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

from ossie import (
    OSIDataset,
    OSIDocument,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from ..hex_types import (
    HexModel,
    HexProject,
    HexProjectStash,
    HexView,
    HexViewStash,
    maybe_write_extension,
)
from ..ossie_types import OSSIE_VERSION
from ..util.dialect import map_hex_dialect_to_ossie
from ..util.errors import ConversionError, ConversionWarning
from .convert_hex_model import convert_hex_model
from .convert_hex_view import convert_hex_view


def convert_hex_project(
    hex_project: HexProject,
) -> tuple[OSIDocument, list[ConversionWarning]]:
    """Convert a Hex project to an Ossie document.

    Returns ``(ossie_document, warnings)``.
    """

    warnings: list[ConversionWarning] = []

    ossie_dialect = map_hex_dialect_to_ossie(hex_project.dialect)

    models: list[OSIDataset] = []
    relationships: list[OSIRelationship] = []
    metrics: list[OSIMetric] = []
    views_stash: list[HexViewStash] = []
    metric_names: set[str] = set()

    dim_ids_by_model = {
        resource.id: {dim.id for dim in resource.dimensions}
        for resource in hex_project.resources
        if isinstance(resource, HexModel)
    }

    for resource in hex_project.resources:
        if isinstance(resource, HexView):
            view_stash, view_warnings = convert_hex_view(resource)
            views_stash.append(view_stash)
            warnings.extend(view_warnings)
            continue

        assert isinstance(resource, HexModel)
        ds, ds_metrics, ds_rels, ds_warnings = convert_hex_model(
            resource,
            ossie_dialect=ossie_dialect,
            metric_names=metric_names,
            dim_ids_by_model=dim_ids_by_model,
        )
        models.append(ds)
        metrics.extend(ds_metrics)
        relationships.extend(ds_rels)
        warnings.extend(ds_warnings)

    if not models:
        raise ConversionError("Hex project contains no convertible models")

    project_stash = HexProjectStash(
        hex_dialect=hex_project.dialect,
        views=views_stash or None,
    )

    semantic_model = OSISemanticModel(
        name=hex_project.name,
        datasets=models,
        relationships=relationships or None,
        metrics=metrics or None,
        custom_extensions=maybe_write_extension(project_stash),
    )
    document = OSIDocument(
        version=OSSIE_VERSION,
        vendors=[OSIVendor.HEX],
        semantic_model=[semantic_model],
    )
    return document, warnings
