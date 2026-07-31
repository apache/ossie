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


def ref_resolver(
    *,
    dataset_name: str,
    dim_ids_by_dataset: dict[str, dict[str, str]],
    relation_ids_by_target: dict[str, str],
) -> RefResolver:
    """Build a resolver from Ossie ``dataset.field`` pairs to Hex references.

    Hex addresses another model through the ID of a *relation* pointing at it,
    not the model's own ID, so a foreign dataset is only reachable when this
    model declares a relation targeting it.
    """

    def resolve(qualifier: str, field: str) -> str | None:
        dim_ids = dim_ids_by_dataset.get(qualifier, {})
        dim_id = dim_ids.get(field)
        if dim_id is None:
            return None
        if qualifier == dataset_name:
            return dim_id
        relation_id = relation_ids_by_target.get(qualifier)
        if relation_id is None:
            return None
        return f"{relation_id}.{dim_id}"

    return resolve
