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

from ossie_hex.hex_types import HexDataType
from ossie_hex.ossie_to_hex.convert_ossie_datatype import ossie_to_hex_datatype


@pytest.mark.parametrize(
    ("ossie_type", "hex_type"),
    [
        (OSIDataType.STRING, HexDataType.STRING),
        (OSIDataType.INTEGER, HexDataType.NUMBER),
        (OSIDataType.DECIMAL, HexDataType.NUMBER),
        (OSIDataType.FLOAT, HexDataType.NUMBER),
        (OSIDataType.BOOLEAN, HexDataType.BOOLEAN),
        (OSIDataType.DATE, HexDataType.DATE),
        (OSIDataType.DATE_TIME, HexDataType.TIMESTAMP_NAIVE),
        (OSIDataType.DATE_TIME_TZ, HexDataType.TIMESTAMP_TZ),
        (OSIDataType.TIME, HexDataType.OTHER),
        (OSIDataType.OPAQUE, HexDataType.OTHER),
    ],
)
def test_ossie_to_hex_datatype(
    ossie_type: OSIDataType,
    hex_type: HexDataType,
) -> None:
    result, warning = ossie_to_hex_datatype(
        ossie_type,
        default=HexDataType.STRING,
    )

    assert result == hex_type
    assert warning is None


def test_stashed_hex_datatype_takes_precedence() -> None:
    result, warning = ossie_to_hex_datatype(
        OSIDataType.DECIMAL,
        default=HexDataType.NUMBER,
        stash=HexDataType.STRING,
    )

    assert result == HexDataType.STRING
    assert warning is None


def test_missing_ossie_datatype_warns() -> None:
    result, warning = ossie_to_hex_datatype(
        None,
        default=HexDataType.STRING,
    )

    assert result == HexDataType.STRING
    assert warning == ("Ossie datatype not found. Using default 'string'")


def test_stash_suppresses_missing_datatype_warning() -> None:
    result, warning = ossie_to_hex_datatype(
        None,
        default=HexDataType.STRING,
        stash=HexDataType.NULL,
    )

    assert result == HexDataType.NULL
    assert warning is None
