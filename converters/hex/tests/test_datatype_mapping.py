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

from ossie_hex.hex_types import HexDataType, is_lossless_hex_type, is_temporal_hex_type


@pytest.mark.parametrize(
    "hex_type",
    [
        HexDataType.DATE,
        HexDataType.TIMESTAMP_NAIVE,
        HexDataType.TIMESTAMP_TZ,
    ],
)
def test_temporal_hex_datatypes(hex_type: HexDataType) -> None:
    assert is_temporal_hex_type(hex_type)


@pytest.mark.parametrize(
    "hex_type",
    [
        HexDataType.NUMBER,
        HexDataType.STRING,
        HexDataType.BOOLEAN,
        HexDataType.NULL,
        HexDataType.OTHER,
    ],
)
def test_non_temporal_hex_datatypes(hex_type: HexDataType) -> None:
    assert not is_temporal_hex_type(hex_type)


@pytest.mark.parametrize(
    "hex_type",
    [
        HexDataType.STRING,
        HexDataType.NUMBER,
        HexDataType.BOOLEAN,
        HexDataType.DATE,
        HexDataType.TIMESTAMP_NAIVE,
        HexDataType.TIMESTAMP_TZ,
        HexDataType.OTHER,
    ],
)
def test_lossless_hex_datatypes(hex_type: HexDataType) -> None:
    assert is_lossless_hex_type(hex_type)


@pytest.mark.parametrize(
    "hex_type",
    [
        HexDataType.NULL,
    ],
)
def test_lossy_hex_datatypes(hex_type: HexDataType) -> None:
    assert not is_lossless_hex_type(hex_type)
