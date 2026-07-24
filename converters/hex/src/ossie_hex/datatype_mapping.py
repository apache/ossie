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

"""Datatype mapping between Hex and Ossie."""

from __future__ import annotations

from ossie import OSIDataType

from .hex_models import HexDataType

HEX_TO_OSSIE: dict[HexDataType, OSIDataType] = {
    HexDataType.STRING: OSIDataType.STRING,
    HexDataType.NUMBER: OSIDataType.DECIMAL,  # expand to Decimal since it's the widest option
    HexDataType.BOOLEAN: OSIDataType.BOOLEAN,
    HexDataType.DATE: OSIDataType.DATE,
    HexDataType.TIMESTAMP_NAIVE: OSIDataType.DATE_TIME,
    HexDataType.TIMESTAMP_TZ: OSIDataType.DATE_TIME_TZ,
    HexDataType.OTHER: OSIDataType.OPAQUE,
    HexDataType.NULL: OSIDataType.OPAQUE,
}

TEMPORAL_HEX_TYPES = frozenset[HexDataType](
    {
        HexDataType.DATE,
        HexDataType.TIMESTAMP_NAIVE,
        HexDataType.TIMESTAMP_TZ,
    }
)

OSSIE_TO_HEX: dict[OSIDataType, HexDataType] = {
    OSIDataType.STRING: HexDataType.STRING,
    OSIDataType.INTEGER: HexDataType.NUMBER,
    OSIDataType.DECIMAL: HexDataType.NUMBER,
    OSIDataType.FLOAT: HexDataType.NUMBER,
    OSIDataType.BOOLEAN: HexDataType.BOOLEAN,
    OSIDataType.DATE: HexDataType.DATE,
    OSIDataType.DATE_TIME: HexDataType.TIMESTAMP_NAIVE,
    OSIDataType.DATE_TIME_TZ: HexDataType.TIMESTAMP_TZ,
    OSIDataType.TIME: HexDataType.OTHER,  # no Hex `time`
    OSIDataType.OPAQUE: HexDataType.OTHER,
}


def hex_to_ossie_datatype(value: HexDataType) -> OSIDataType:
    """Map a Hex datatype to an Ossie datatype."""
    return HEX_TO_OSSIE[value]


def ossie_to_hex_datatype(
    value: OSIDataType | None,
    *,
    default: HexDataType,
    stash: HexDataType | None = None,
) -> tuple[HexDataType, str | None]:
    """Map an Ossie datatype to a Hex type."""
    if stash is not None:
        return stash, None

    if value is None:
        return default, (f"Ossie datatype not found. Using default '{default.value}'")

    return OSSIE_TO_HEX.get(value, HexDataType.OTHER), None


def is_temporal_hex_type(value: HexDataType) -> bool:
    return value in TEMPORAL_HEX_TYPES


# Round-tripped once at import rather than named outright, so a Hex type added
# without a faithful Ossie counterpart falls out of the set on its own instead
# of silently starting to be lost. Only ``null`` fails today, having no Ossie
# datatype to be written as.
LOSSLESS_HEX_TYPES = frozenset[HexDataType](
    value
    for value in HexDataType
    if ossie_to_hex_datatype(hex_to_ossie_datatype(value), default=HexDataType.STRING)[
        0
    ]
    == value
)


def is_lossless_hex_type(value: HexDataType) -> bool:
    """Whether ``datatype`` alone brings this Hex type back unchanged."""
    return value in LOSSLESS_HEX_TYPES
