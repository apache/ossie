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

from ..hex_extension import HexProjectStash
from ..hex_types import HexView
from ..util.errors import ConversionError


def restore_hex_views(
    project_stash: HexProjectStash | None,
    *,
    taken_ids: set[str],
) -> list[HexView]:
    """Rebuild the project files for views stashed on the semantic model.

    Ossie has no view concept, so a view survives a round trip only as a payload
    on the semantic model and comes back verbatim rather than being reconverted.
    """
    if project_stash is None or project_stash.views is None:
        return []
    views: list[HexView] = []
    for view_stash in project_stash.views:
        view = view_stash.resource
        if view.id in taken_ids:
            raise ConversionError(
                f"view '{view.id}' collides with another resource of the same ID; "
                f"Hex resource IDs are unique across models and views."
            )
        taken_ids.add(view.id)
        views.append(view)
    return views
