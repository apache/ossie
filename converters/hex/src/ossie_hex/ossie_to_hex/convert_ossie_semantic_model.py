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

from ossie import OssieSemanticModel

from ..hex import HexProject, HexResource
from .context import ExportContext
from .convert_ossie_dataset import convert_ossie_dataset
from .convert_ossie_metric import convert_ossie_metric
from .convert_ossie_relationship import convert_ossie_relationship


def convert_ossie_semantic_model(
    ossie_semantic_model: OssieSemanticModel,
    *,
    ctx: ExportContext,
) -> HexProject:
    """Convert an Ossie semantic model to a Hex project.

    Returns the converted Hex project.
    """
    with ctx.semantic_model_scope(ossie_semantic_model.name):
        with ctx.problem_scope("datasets"):
            for ossie_dataset in ossie_semantic_model.datasets:
                hex_model = convert_ossie_dataset(ossie_dataset, ctx=ctx)
                ctx.add_hex_model(hex_model)

        with ctx.problem_scope("relationships"):
            for ossie_relationship in ossie_semantic_model.relationships or []:
                convert_ossie_relationship(ossie_relationship, ctx=ctx)

        with ctx.problem_scope("metrics"):
            for ossie_metric in ossie_semantic_model.metrics or []:
                convert_ossie_metric(ossie_metric, ctx=ctx)

        with ctx.problem_scope("description"):
            if ossie_semantic_model.description is not None:
                ctx.warn("Not supported")

        with ctx.problem_scope("ai_context"):
            if ossie_semantic_model.ai_context is not None:
                ctx.warn("Not supported")

        with ctx.problem_scope("custom_extensions"):
            if ossie_semantic_model.custom_extensions is not None:
                ctx.warn("Not supported")

        hex_models = ctx.hex_models()
        hex_resources: list[HexResource] = []
        hex_resources.extend(hex_models)

        hex_project = HexProject(
            name=ossie_semantic_model.name,
            dialect=ctx.hex_dialect,
            resources=hex_resources,
        )

    return hex_project
