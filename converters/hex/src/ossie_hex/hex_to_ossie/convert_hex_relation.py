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

from ossie import OSIRelationship

from ..hex_types import (
    HEX_VENDOR,
    HexRelation,
    HexRelationStash,
    HexRelationType,
    maybe_write_extension,
)
from ..util.equi_join import parse_equi_join
from ..util.errors import ConversionWarning


def convert_hex_relation(
    relation: HexRelation,
    *,
    base_model_id: str,
) -> tuple[OSIRelationship | None, HexRelation | None, list[ConversionWarning]]:
    """Export a Hex relation as an Ossie relationship, or hand it back whole.

    A join with no column pairs to decompose into leaves no relationship to
    carry it, so the relation itself is returned for the model to preserve.
    """
    warnings: list[ConversionWarning] = []
    parsed = parse_equi_join(
        relation.join_sql,
        relation_id=relation.id,
        target=relation.target,
    )

    if parsed is None:
        warnings.append(
            ConversionWarning(
                f"relation '{base_model_id}.{relation.id}' join_sql could not be "
                f"decomposed into column pairs; preserved in custom_extensions[{HEX_VENDOR}]"
            )
        )
        return None, relation, warnings

    local_cols, remote_cols = parsed
    from_ds, to_ds = base_model_id, relation.target
    from_cols, to_cols = local_cols, remote_cols
    if relation.type == HexRelationType.ONE_TO_MANY:
        # Ossie `from` is the many side.
        from_ds, to_ds = relation.target, base_model_id
        from_cols, to_cols = remote_cols, local_cols

    # The join itself is not recorded. Everything `parse_equi_join` accepts is a
    # conjunction of equalities, which the export rebuilds from the column pairs
    # as the same conjunction: only operand order, spacing, and the qualifier
    # naming the remote side can come back differently.
    stash = HexRelationStash(
        relation_type=relation.type,
        visibility=relation.visibility,
    )

    rel = OSIRelationship(
        name=relation.id,
        to=to_ds,
        from_columns=from_cols,
        to_columns=to_cols,
        custom_extensions=maybe_write_extension(stash),
        **{"from": from_ds},
    )
    return rel, None, warnings
