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

from collections.abc import Mapping

from ossie import OssieDataType

from ..hex import HexDataType

OSSIE_TO_HEX: Mapping[OssieDataType, HexDataType] = {
    OssieDataType.STRING: HexDataType.STRING,
    OssieDataType.INTEGER: HexDataType.NUMBER,
    OssieDataType.DECIMAL: HexDataType.NUMBER,
    OssieDataType.FLOAT: HexDataType.NUMBER,
    OssieDataType.BOOLEAN: HexDataType.BOOLEAN,
    OssieDataType.DATE: HexDataType.DATE,
    OssieDataType.DATE_TIME: HexDataType.TIMESTAMP_NAIVE,
    OssieDataType.DATE_TIME_TZ: HexDataType.TIMESTAMP_TZ,
    OssieDataType.TIME: HexDataType.OTHER,  # Hex has no time datatype
    OssieDataType.OPAQUE: HexDataType.OTHER,  # Hex has no opaque datatype
}


def convert_ossie_datatype(
    ossie_datatype: OssieDataType | None,
    default: HexDataType,
) -> HexDataType:
    """Map an Ossie datatype to a Hex type."""
    if ossie_datatype is None:
        # Already warned during load phase
        return default
    return OSSIE_TO_HEX[ossie_datatype]
