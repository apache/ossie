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

import re

from pydantic import TypeAdapter, ValidationError

from ..hex import HexEntityId
from .context import ExportContext

_HEX_ENTITY_ID_ADAPTER = TypeAdapter(HexEntityId)


def convert_ossie_name(
    ossie_name: str,
    *,
    ctx: ExportContext,
) -> HexEntityId | None:
    try:
        # if already valid, early return
        hex_entity_id = _HEX_ENTITY_ID_ADAPTER.validate_python(ossie_name)
        return hex_entity_id
    except ValidationError as _e:
        pass

    # attempt to normalize an Ossie name to a Hex entity ID.

    # basic transformation (doesn't require notice)
    # - lowercasing
    # - removing leading/trailing whitespace
    out = ossie_name.lower().strip()
    try:
        hex_entity_id = _HEX_ENTITY_ID_ADAPTER.validate_python(out)
        return hex_entity_id
    except ValidationError as _e:
        pass

    # more aggressive transformation
    # - replace invalid characters with underscores
    before = out
    out = re.sub(r"[^a-z0-9_]+", "_", out)

    if not out:
        ctx.error(f"Unable to convert identifier: '{ossie_name}'.")
        return None
    if out[0].isdigit():
        out = f"_{out}"
    if len(out) < 2:
        out = f"{out}_"
    elif len(out) > 128:
        out = out[:128]

    if before != out:
        try:
            hex_entity_id = _HEX_ENTITY_ID_ADAPTER.validate_python(out)
            ctx.info(f"Normalized identifier: '{ossie_name}' -> '{out}'.")
            return hex_entity_id
        except ValidationError as _e:
            pass

    ctx.error(f"Unable to convert identifier: '{ossie_name}'.")
    return None
