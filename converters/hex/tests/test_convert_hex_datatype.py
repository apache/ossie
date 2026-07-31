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

import pytest
from ossie import OSIDataType

from ossie_hex.hex_to_ossie.convert_hex_datatype import hex_to_ossie_datatype
from ossie_hex.hex_types import HexDataType


@pytest.mark.parametrize(
    ("hex_type", "ossie_type"),
    [
        (HexDataType.STRING, OSIDataType.STRING),
        (HexDataType.NUMBER, OSIDataType.DECIMAL),
        (HexDataType.BOOLEAN, OSIDataType.BOOLEAN),
        (HexDataType.DATE, OSIDataType.DATE),
        (HexDataType.TIMESTAMP_NAIVE, OSIDataType.DATE_TIME),
        (HexDataType.TIMESTAMP_TZ, OSIDataType.DATE_TIME_TZ),
        (HexDataType.NULL, OSIDataType.OPAQUE),
        (HexDataType.OTHER, OSIDataType.OPAQUE),
    ],
)
def test_hex_to_ossie_datatype(
    hex_type: HexDataType,
    ossie_type: OSIDataType,
) -> None:
    result = hex_to_ossie_datatype(hex_type)
    assert result == ossie_type
