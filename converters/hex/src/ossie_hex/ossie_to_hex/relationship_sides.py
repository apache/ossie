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

from typing import NamedTuple

from ossie import OSIRelationship

from ..hex_extension import HexRelationStash, read_stash
from ..hex_types import HexRelationType


class RelationshipSides(NamedTuple):
    """Which end of an Ossie relationship the Hex relation is declared on.

    Dataset names and columns are Ossie's, left for the caller to resolve to Hex
    IDs. ``local``/``remote`` are the relation's own orientation, which is the
    reverse of ``from``/``to`` for a one-to-many.
    """

    relation_type: HexRelationType
    local_dataset: str
    remote_dataset: str
    local_columns: list[str]
    remote_columns: list[str]


def relationship_sides(rel: OSIRelationship) -> RelationshipSides:
    """Read a relationship from the side of the model that declares the relation.

    Ossie puts the many side in ``from``, so a one-to-many is stored pointing
    back at the model holding it and has to be read inside out.
    """
    rel_type: HexRelationType = HexRelationType.MANY_TO_ONE

    stash = read_stash(rel.custom_extensions, HexRelationStash)
    if stash is not None and stash.relation_type is not None:
        rel_type = stash.relation_type

    if rel_type == HexRelationType.ONE_TO_MANY:
        return RelationshipSides(
            relation_type=rel_type,
            local_dataset=rel.to,
            remote_dataset=rel.from_dataset,
            local_columns=list(rel.to_columns),
            remote_columns=list(rel.from_columns),
        )
    return RelationshipSides(
        relation_type=rel_type,
        local_dataset=rel.from_dataset,
        remote_dataset=rel.to,
        local_columns=list(rel.from_columns),
        remote_columns=list(rel.to_columns),
    )
