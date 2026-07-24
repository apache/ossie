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
from ossie import OSIDialect

from ossie_hex._common import ConversionError
from ossie_hex.dialect_mapping import (
    map_hex_dialect_to_ossie,
    map_ossie_dialect_to_hex,
)
from ossie_hex.hex_models import HexDialect


@pytest.mark.parametrize(
    ("hex_dialect", "ossie_dialect"),
    [
        (HexDialect.BIGQUERY, OSIDialect.BIGQUERY),
        (HexDialect.DATABRICKS, OSIDialect.DATABRICKS),
        (HexDialect.SNOWFLAKE, OSIDialect.SNOWFLAKE),
        (HexDialect.SPARK, OSIDialect.DATABRICKS),
    ],
)
def test_map_hex_dialect_to_ossie(
    hex_dialect: HexDialect,
    ossie_dialect: OSIDialect,
) -> None:
    assert map_hex_dialect_to_ossie(hex_dialect) == ossie_dialect


def test_map_hex_dialect_is_case_insensitive() -> None:
    assert map_hex_dialect_to_ossie("SNOWFLAKE") == OSIDialect.SNOWFLAKE


def test_hex_dialect_without_an_ossie_counterpart_falls_back_to_ansi() -> None:
    assert map_hex_dialect_to_ossie(HexDialect.DUCKDB) == OSIDialect.ANSI_SQL


def test_unknown_hex_dialect_is_rejected() -> None:
    # Silently degrading a typo to ANSI_SQL would tag warehouse SQL as portable.
    with pytest.raises(ConversionError, match="Unknown Hex dialect 'snowfalke'"):
        map_hex_dialect_to_ossie("snowfalke")


@pytest.mark.parametrize(
    ("ossie_dialect", "hex_dialect"),
    [
        (OSIDialect.SNOWFLAKE, HexDialect.SNOWFLAKE.value),
        (OSIDialect.BIGQUERY, HexDialect.BIGQUERY.value),
        (OSIDialect.DATABRICKS, HexDialect.SPARK.value),
        (OSIDialect.ANSI_SQL, HexDialect.DUCKDB.value),
    ],
)
def test_map_ossie_dialect_to_hex(
    ossie_dialect: OSIDialect,
    hex_dialect: str,
) -> None:
    assert map_ossie_dialect_to_hex(ossie_dialect) == hex_dialect


def test_map_ossie_dialect_uses_custom_fallback() -> None:
    assert map_ossie_dialect_to_hex(None, fallback=HexDialect.SNOWFLAKE) == "snowflake"
    assert map_ossie_dialect_to_hex("unknown", fallback="custom") == "custom"
