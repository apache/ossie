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

from ossie import OSIDataset, OSISemanticModel

from ..hex_types import normalize_to_hex_id


def dataset_hex_ids(
    model: OSISemanticModel,
    *,
    taken: set[str],
) -> dict[str, str]:
    """Resolve each Ossie dataset of a model to the Hex model ID it takes."""
    return {
        ds.name: normalize_to_hex_id(ds.name, "dataset", taken) for ds in model.datasets
    }


def dimension_hex_ids(ossie_dataset: OSIDataset) -> dict[str, str]:
    """Resolve each Ossie field of a dataset to the Hex dimension ID it takes."""
    taken: set[str] = set()
    return {
        field.name: normalize_to_hex_id(field.name, "dimension", taken)
        for field in ossie_dataset.fields or []
    }
