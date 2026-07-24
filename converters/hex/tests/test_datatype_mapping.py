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

from ossie_hex.datatype_mapping import (
    hex_to_ossie_datatype,
    is_lossless_hex_type,
    is_temporal_hex_type,
    ossie_to_hex_datatype,
)
from ossie_hex.hex_models import HexDataType


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
