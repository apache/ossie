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

from ossie import OSIDialect, OSIMetric, OSISemanticModel

from ..hex_types import HexMeasureStash, read_stash
from ..util.errors import ConversionError
from .references import datasets_referenced


def assign_ossie_metrics(
    model: OSISemanticModel,
    *,
    hex_ids_by_dataset: dict[str, str],
    base_model_id: str | None,
    preferred_dialect: OSIDialect,
) -> dict[str, list[OSIMetric]]:
    """Group a semantic model's metrics by the Hex model each belongs to.

    Ossie metrics sit beside the datasets rather than on one, while a Hex
    measure always belongs to a model, so every metric has to be placed before
    it can be converted.

    A metric goes where its custom extension says, else to the single dataset
    its expression names, else to ``base_model_id``. A metric that names several
    datasets with no base model to fall back on is an error rather than a guess.
    """
    ossie_dataset_names = set(hex_ids_by_dataset)
    hex_model_ids = set(hex_ids_by_dataset.values())

    metrics_by_dataset: dict[str, list[OSIMetric]] = {}
    unassigned: list[OSIMetric] = []
    for metric in model.metrics or []:
        stash = read_stash(metric.custom_extensions, HexMeasureStash)
        ds_id = stash.model_id if stash is not None else None
        if ds_id and ds_id in hex_model_ids:
            metrics_by_dataset.setdefault(ds_id, []).append(metric)
            continue
        refs = datasets_referenced(metric, preferred_dialect, ossie_dataset_names)
        if len(refs) == 1:
            metrics_by_dataset.setdefault(hex_ids_by_dataset[refs[0]], []).append(
                metric
            )
        elif len(refs) == 0 and base_model_id:
            metrics_by_dataset.setdefault(base_model_id, []).append(metric)
        elif len(refs) == 0 and len(hex_model_ids) == 1:
            metrics_by_dataset.setdefault(next(iter(hex_model_ids)), []).append(metric)
        else:
            unassigned.append(metric)

    if unassigned:
        if base_model_id:
            for metric in unassigned:
                metrics_by_dataset.setdefault(base_model_id, []).append(metric)
        else:
            names = ", ".join(m.name for m in unassigned)
            raise ConversionError(
                f"Could not assign metric(s) to a Hex model: {names}. "
                f"Pass --base-model to choose a dataset."
            )

    return metrics_by_dataset
