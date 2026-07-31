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

from ..util.rewrite_refs import RefResolver


def export_ref_resolver(
    *,
    model_id: str,
    relation_targets: dict[str, str],
    dim_ids_by_model: dict[str, set[str]],
) -> RefResolver:
    """Build the resolver the import will have when it reads this document back.

    Stands in for ``ossie_to_hex.ref_resolver``: an Ossie ``dataset.field`` pair
    is only addressable from Hex when the field is really there and, for another
    model, when this one reaches it through a relation. Ossie field names are the
    Hex dimension IDs that produced them, so no normalizing is needed here.
    """

    def resolve(qualifier: str, field: str) -> str | None:
        if field not in dim_ids_by_model.get(qualifier, set()):
            return None
        if qualifier == model_id:
            return field
        relation_id = relation_targets.get(qualifier)
        if relation_id is None:
            return None
        return f"{relation_id}.{field}"

    return resolve
