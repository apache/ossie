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

"""Dialect mapping between Hex and Ossie."""

from __future__ import annotations

from ossie import OSIDialect

from ._common import ConversionError
from .hex_models import HexDialect

HEX_TO_OSSIE_DIALECT: dict[HexDialect, OSIDialect] = {
    HexDialect.BIGQUERY: OSIDialect.BIGQUERY,
    HexDialect.DATABRICKS: OSIDialect.DATABRICKS,
    HexDialect.SNOWFLAKE: OSIDialect.SNOWFLAKE,
    HexDialect.SPARK: OSIDialect.DATABRICKS,
    # omitted dialects (duckdb) fallback to ANSI_SQL default
}

OSSIE_TO_HEX_DIALECT: dict[OSIDialect, HexDialect] = {
    OSIDialect.SNOWFLAKE: HexDialect.SNOWFLAKE,
    OSIDialect.BIGQUERY: HexDialect.BIGQUERY,
    OSIDialect.DATABRICKS: HexDialect.SPARK,
    OSIDialect.ANSI_SQL: HexDialect.DUCKDB,
    # MDX, MAQL, TABLEAU have no Hex equivalent dialect
}


def map_hex_dialect_to_ossie(hex_dialect: HexDialect | str) -> OSIDialect:
    """Map a Hex project dialect to an Ossie expression dialect.

    Unknown dialects are rejected.
    """
    raw = hex_dialect.value if isinstance(hex_dialect, HexDialect) else str(hex_dialect)
    try:
        normalized = HexDialect(raw.lower())
    except ValueError:
        supported = ", ".join(d.value for d in HexDialect)
        # Better to error during conversion than produce invalid SQL that errors
        # during runtime.
        raise ConversionError(
            f"Unknown Hex dialect '{hex_dialect}'; expected one of {supported}"
        ) from None
    return HEX_TO_OSSIE_DIALECT.get(normalized, OSIDialect.ANSI_SQL)


def map_ossie_dialect_to_hex(
    ossie_dialect: OSIDialect | str | None,
    fallback: HexDialect | str = HexDialect.DUCKDB,
) -> str:
    """Map an Ossie expression dialect to a Hex project dialect."""
    fallback_value = fallback.value if isinstance(fallback, HexDialect) else fallback
    if not ossie_dialect:
        return fallback_value
    try:
        normalized = OSIDialect(ossie_dialect)
    except ValueError:
        return fallback_value
    mapped = OSSIE_TO_HEX_DIALECT.get(normalized)
    return mapped.value if mapped else fallback_value
