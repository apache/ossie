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

import re
from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError
from typing_extensions import TypeAliasType

from ..util.errors import ConversionError

HEX_ID_PATTERN = r"^[a-z_][a-z0-9_]{1,127}$"
HEX_ID_RE = re.compile(HEX_ID_PATTERN)

HEX_RESERVED_IDS = ["this", "self", "dataset", "model", "view", "metric", "env"]
HEX_RESERVED_ID_PREFIX = "_hex"


def _exclude_reserved_ids(hex_id: str) -> str:
    if hex_id in HEX_RESERVED_IDS:
        raise PydanticCustomError(
            "custom.string_disallowed",
            "ID '{hex_id}' is a reserved term and cannot be used",
            {"hex_id": hex_id},
        )
    if hex_id.startswith(HEX_RESERVED_ID_PREFIX):
        raise PydanticCustomError(
            "custom.string_disallowed",
            "ID '{hex_id}' cannot begin with '{HEX_RESERVED_ID_PREFIX}'",
            {"hex_id": hex_id, "HEX_RESERVED_ID_PREFIX": HEX_RESERVED_ID_PREFIX},
        )
    return hex_id


HexID = TypeAliasType(
    "HexID",
    Annotated[
        str,
        Field(
            title="HexID",
            description=(
                "An ID between 2 and 128 characters that begins with a lowercase "
                "letter or underscore and contains only lowercase letters, "
                "underscores, and numbers. The IDs ``this``, ``self``, ``dataset``, ``model``, ``view``, ``metric``, and ``env``, and IDs beginning with ``_hex``, are reserved."
            ),
            pattern=HEX_ID_PATTERN,
            min_length=2,
            max_length=128,
        ),
        AfterValidator(_exclude_reserved_ids),
    ],
)


def id_to_name(hex_id: str) -> str:
    words = hex_id.split("_")
    words[0] = words[0].title()
    return " ".join(words)


def normalize_to_hex_id(name: str, what: str, taken: set[str]) -> str:
    """Coerce an Ossie name into a Hex ID.

    Collisions and blank names are errors.
    """
    if not name.strip():
        raise ConversionError(f"{what} has a blank name; name it in the Ossie model.")
    raw = name
    if HEX_ID_RE.match(raw):
        # preserve valid Hex ID's
        out = raw
    else:
        # lowercase; replace invalid characters with underscores; remove
        # leading/trailing underscores
        out = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
        if not out:
            out = "_1"
        elif out[0].isdigit():
            out = f"_{out}"
        if len(out) < 2:
            out = f"{out}_"
        if len(out) > 128:
            out = out[:128]
    if out in taken:
        raise ConversionError(
            f"{what} '{name}' coerces to '{out}', which collides with another "
            f"name; rename it in the Ossie model."
        )
    if out.startswith(HEX_RESERVED_ID_PREFIX) or out in HEX_RESERVED_IDS:
        raise ConversionError(f"{what} '{name}' coerced to reserved Hex ID '{out}'")
    taken.add(out)
    return out
