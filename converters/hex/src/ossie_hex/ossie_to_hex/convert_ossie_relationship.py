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

from typing import Any

from ossie import OSIRelationship

from ..hex_types import HexRelation, HexRelationStash, normalize_to_hex_id, read_stash
from ..util.equi_join import synthesize_join_sql
from ..util.errors import ConversionError, ConversionWarning
from .relationship_sides import relationship_sides


def convert_ossie_relationship(
    rel: OSIRelationship,
    *,
    base_dataset: str,
    hex_ids_by_dataset: dict[str, str],
    dim_ids_by_dataset: dict[str, dict[str, str]],
    taken: set[str],
) -> tuple[HexRelation, list[ConversionWarning]]:
    """Convert an Ossie relationship to a Hex relation on ``base_dataset``."""
    warnings: list[ConversionWarning] = []
    stash = read_stash(rel.custom_extensions, HexRelationStash)
    rel_id = normalize_to_hex_id(rel.name, "relation", taken)

    # Both sides name datasets and fields, so resolve them to the Hex IDs refs
    # address.
    sides = relationship_sides(rel)
    local_ds = hex_ids_by_dataset.get(sides.local_dataset, sides.local_dataset)
    target = hex_ids_by_dataset.get(sides.remote_dataset, sides.remote_dataset)
    if local_ds != base_dataset:
        raise ConversionError(
            f"relationship '{rel.name}' does not start at base dataset '{base_dataset}'"
        )

    local_dim_ids = dim_ids_by_dataset.get(sides.local_dataset, {})
    remote_dim_ids = dim_ids_by_dataset.get(sides.remote_dataset, {})
    local_cols = [local_dim_ids.get(c, c) for c in sides.local_columns]
    remote_cols = [remote_dim_ids.get(c, c) for c in sides.remote_columns]

    hex_rel: dict[str, Any] = {
        "id": rel_id,
        "type": sides.relation_type,
        "join_sql": synthesize_join_sql(
            local_columns=local_cols,
            remote_columns=remote_cols,
            relation_id=rel_id,
        ),
    }
    if target and target != rel_id:
        hex_rel["target"] = target
    if stash is not None and stash.visibility is not None:
        hex_rel["visibility"] = stash.visibility
    return HexRelation(**hex_rel), warnings
