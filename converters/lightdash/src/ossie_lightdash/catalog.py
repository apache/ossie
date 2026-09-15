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
"""Column types from a dbt ``catalog.json``.

``dbt docs generate`` records the warehouse's real type for every column of
every model. Lightdash learns most dimension types from the warehouse rather
than from YAML, so a Lightdash project usually has few authored types; the
catalog fills the gaps with the physical type reduced to Ossie's logical
vocabulary.
"""

import json
import re
from pathlib import Path
from typing import Dict, Optional

from ossie import OssieDataType

# Column types keyed by model name, then by lower-cased column name.
Catalog = Dict[str, Dict[str, str]]

_TYPE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9 ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+))?\s*\))?\s*$")

_EXACT = {
    "INT": OssieDataType.INTEGER,
    "INTEGER": OssieDataType.INTEGER,
    "INT64": OssieDataType.INTEGER,
    "BIGINT": OssieDataType.INTEGER,
    "SMALLINT": OssieDataType.INTEGER,
    "TINYINT": OssieDataType.INTEGER,
    "INT2": OssieDataType.INTEGER,
    "INT4": OssieDataType.INTEGER,
    "INT8": OssieDataType.INTEGER,
    "FLOAT": OssieDataType.FLOAT,
    "FLOAT4": OssieDataType.FLOAT,
    "FLOAT8": OssieDataType.FLOAT,
    "FLOAT64": OssieDataType.FLOAT,
    "DOUBLE": OssieDataType.FLOAT,
    "DOUBLE PRECISION": OssieDataType.FLOAT,
    "REAL": OssieDataType.FLOAT,
    "STRING": OssieDataType.STRING,
    "VARCHAR": OssieDataType.STRING,
    "NVARCHAR": OssieDataType.STRING,
    "CHAR": OssieDataType.STRING,
    "CHARACTER": OssieDataType.STRING,
    "CHARACTER VARYING": OssieDataType.STRING,
    "TEXT": OssieDataType.STRING,
    "BOOL": OssieDataType.BOOLEAN,
    "BOOLEAN": OssieDataType.BOOLEAN,
    "DATE": OssieDataType.DATE,
    "DATETIME": OssieDataType.DATE_TIME,
    "TIMESTAMP": OssieDataType.DATE_TIME,
    "TIMESTAMP_NTZ": OssieDataType.DATE_TIME,
    "TIMESTAMP WITHOUT TIME ZONE": OssieDataType.DATE_TIME,
    "TIMESTAMPTZ": OssieDataType.DATE_TIME_TZ,
    "TIMESTAMP_TZ": OssieDataType.DATE_TIME_TZ,
    "TIMESTAMP_LTZ": OssieDataType.DATE_TIME_TZ,
    "TIMESTAMP WITH TIME ZONE": OssieDataType.DATE_TIME_TZ,
    "TIME": OssieDataType.TIME,
}
# Exact-decimal families: integral when the scale is 0 (or, for NUMBER, when
# Snowflake's default NUMBER(38,0) is spelled without arguments).
_DECIMAL_FAMILY = {"NUMERIC", "BIGNUMERIC", "DECIMAL", "NUMBER"}


def warehouse_type_to_datatype(warehouse_type: str) -> Optional[OssieDataType]:
    """Reduce a physical column type to an Ossie datatype, or None when the
    type is outside the portable vocabulary (arrays, structs, JSON, ...)."""
    match = _TYPE_RE.match(warehouse_type or "")
    if match is None:
        return None
    base = " ".join(match.group(1).upper().split())
    scale = match.group(3)
    if base in _DECIMAL_FAMILY:
        if scale is not None:
            return OssieDataType.INTEGER if int(scale) == 0 else OssieDataType.DECIMAL
        return OssieDataType.INTEGER if base == "NUMBER" else OssieDataType.DECIMAL
    return _EXACT.get(base)


def load_catalog(path: Path) -> Catalog:
    """Read a dbt ``catalog.json`` into ``{model: {column: type}}``.

    Models and seeds are keyed by their dbt name (the last segment of the
    node's unique id); column names are lower-cased because warehouses differ
    in how they report case.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    catalog: Catalog = {}
    for unique_id, node in (document.get("nodes") or {}).items():
        kind = unique_id.split(".", 1)[0]
        if kind not in ("model", "seed"):
            continue
        name = unique_id.rsplit(".", 1)[-1]
        catalog[name] = {
            column_name.lower(): (column.get("type") or "")
            for column_name, column in (node.get("columns") or {}).items()
        }
    return catalog
