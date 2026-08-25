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

from ossie import OssieDataset, OssieMetric, OssieRelationship, OssieSemanticModel

from .context import ExportContext
from .load_ossie_dataset import load_ossie_dataset
from .load_ossie_metric import load_ossie_metric
from .load_ossie_relationship import validate_ossie_relationship


def load_ossie_semantic_model(
    semantic_model: OssieSemanticModel,
    *,
    ctx: ExportContext,
) -> OssieSemanticModel:
    """Load an Ossie semantic model

    Removes invalid dataset fields, metrics, and relationships.

    Returns an Ossie semantic model with only valid members.
    """
    with ctx.problem_scope(semantic_model.name):
        datasets = _load_ossie_datasets(semantic_model.datasets, ctx=ctx)

        relationships: list[OssieRelationship] | None = None
        if semantic_model.relationships:
            dataset_names = set[str](d.name for d in datasets)
            relationships = _load_ossie_relationships(
                semantic_model.relationships, dataset_names=dataset_names, ctx=ctx
            )

        metrics: list[OssieMetric] | None = None
        if semantic_model.metrics:
            field_names = list[tuple[str, str]](
                (d.name, f.name) for d in datasets for f in (d.fields or [])
            )
            metrics = _load_ossie_metrics(
                semantic_model.metrics, field_names=field_names, ctx=ctx
            )

    semantic_model = semantic_model.model_copy(
        update={
            "datasets": datasets,
            "relationships": relationships,
            "metrics": metrics,
        }
    )
    return semantic_model


def _load_ossie_datasets(
    datasets: list[OssieDataset],
    *,
    ctx: ExportContext,
) -> list[OssieDataset]:
    result = list[OssieDataset]()
    with ctx.problem_scope("datasets"):
        for dataset in datasets:
            if dataset := load_ossie_dataset(dataset, ctx=ctx):
                result.append(dataset)
    return result


def _load_ossie_relationships(
    relationships: list[OssieRelationship],
    *,
    dataset_names: set[str],
    ctx: ExportContext,
) -> list[OssieRelationship]:
    result = list[OssieRelationship]()
    with ctx.problem_scope("relationships"):
        for relationship in relationships:
            if validate_ossie_relationship(
                relationship,
                dataset_names=dataset_names,
                ctx=ctx,
            ):
                result.append(relationship)
    return result


def _load_ossie_metrics(
    metrics: list[OssieMetric],
    *,
    field_names: list[tuple[str, str]],
    ctx: ExportContext,
) -> list[OssieMetric]:
    result = list[OssieMetric]()
    with ctx.problem_scope("metrics"):
        for metric in metrics:
            if metric := load_ossie_metric(metric, field_names=field_names, ctx=ctx):
                result.append(metric)
    return result
