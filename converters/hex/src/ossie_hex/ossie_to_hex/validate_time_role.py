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

from ossie import OSIField

from ..hex_types import HexDataType, is_temporal_hex_type
from ..util.errors import ConversionWarning


def validate_time_role(
    field: OSIField,
    hex_type: HexDataType,
    *,
    dataset_id: str,
) -> list[ConversionWarning]:
    """Report a temporal role that the Hex type cannot carry.

    Ossie tracks the time axis separately from the datatype, so a year stored as
    an integer can still be a time dimension and an audit timestamp can opt out
    of the axis. Hex has no such marker and infers the axis from the type alone,
    so any disagreement between the two is lost on import.

    A field without a ``dimension`` block still becomes a Hex dimension, but it
    has no role to lose and must not be read as having opted out.
    """
    if field.dimension is None:
        return []
    hex_is_time = is_temporal_hex_type(hex_type)
    if field.is_time_dimension() == hex_is_time:
        return []
    where = f"field '{dataset_id}.{field.name}'"
    if hex_is_time:
        return [
            ConversionWarning(
                f"{where} is marked is_time: false, but Hex reads its "
                f"'{hex_type.value}' type as temporal; the opt-out is dropped"
            )
        ]
    return [
        ConversionWarning(
            f"{where} is a time dimension, but Hex infers the time axis from the "
            f"type and '{hex_type.value}' is not temporal; the role is dropped"
        )
    ]
