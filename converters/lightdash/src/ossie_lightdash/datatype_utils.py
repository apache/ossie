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

"""Translation between Ossie datatypes and Lightdash dimension types.

Ossie carries a portable logical datatype on every field; Lightdash has a
coarser set of dimension types. The mapping is therefore lossy in one
direction: several Ossie numeric and temporal types collapse onto a single
Lightdash type, so a round-trip preserves the *category* of a datatype but not
always its exact member (e.g. ``Integer`` comes back as ``Decimal``).
"""

from typing import Optional

from ossie import OssieDataType

# Ossie datatype -> Lightdash dimension type.
_DATATYPE_TO_LIGHTDASH = {
    OssieDataType.STRING: "string",
    OssieDataType.INTEGER: "number",
    OssieDataType.DECIMAL: "number",
    OssieDataType.FLOAT: "number",
    OssieDataType.BOOLEAN: "boolean",
    OssieDataType.DATE: "date",
    OssieDataType.DATE_TIME: "timestamp",
    OssieDataType.DATE_TIME_TZ: "timestamp",
    # Lightdash has no time-of-day dimension type; a string keeps the value
    # visible rather than dropping the column.
    OssieDataType.TIME: "string",
    OssieDataType.OPAQUE: "string",
}

# Lightdash dimension type -> Ossie datatype. Numeric widths are not expressed
# in Lightdash, so `number` maps to the widest exact type.
_LIGHTDASH_TO_DATATYPE = {
    "string": OssieDataType.STRING,
    "number": OssieDataType.DECIMAL,
    "boolean": OssieDataType.BOOLEAN,
    "date": OssieDataType.DATE,
    "timestamp": OssieDataType.DATE_TIME,
}

_TEMPORAL = {OssieDataType.DATE, OssieDataType.TIME, OssieDataType.DATE_TIME, OssieDataType.DATE_TIME_TZ}


def datatype_to_lightdash_type(datatype: Optional[OssieDataType]) -> Optional[str]:
    """Return the Lightdash dimension type for an Ossie datatype, if any."""
    if datatype is None:
        return None
    return _DATATYPE_TO_LIGHTDASH.get(datatype)


def lightdash_type_to_datatype(lightdash_type: Optional[str]) -> Optional[OssieDataType]:
    """Return the Ossie datatype for a Lightdash dimension type, if any."""
    if lightdash_type is None:
        return None
    return _LIGHTDASH_TO_DATATYPE.get(lightdash_type)


_COUNT_TYPES = {"count", "count_distinct"}
_NUMERIC_AGGREGATES = {
    "sum", "sum_distinct", "average", "average_distinct", "median", "percentile"
}
_ORDER_AGGREGATES = {"min", "max"}
_VALUE_TYPES = {
    "boolean": OssieDataType.BOOLEAN,
    "string": OssieDataType.STRING,
    "date": OssieDataType.DATE,
    "timestamp": OssieDataType.DATE_TIME,
}


def metric_datatype(
    lightdash_type: str, column_type: Optional[str]
) -> Optional[OssieDataType]:
    """The Ossie datatype of a Lightdash metric's result, when it is implied.

    Counts are integers; numeric aggregates over a `number` column are
    decimals; min/max keep the column's type; value-typed metrics (`boolean`,
    `string`, `date`, `timestamp`) declare their own type. Anything else
    (`number` with arbitrary SQL, aggregates over untyped columns) is left
    unset rather than guessed.
    """
    if lightdash_type in _COUNT_TYPES:
        return OssieDataType.INTEGER
    if lightdash_type in _VALUE_TYPES:
        return _VALUE_TYPES[lightdash_type]
    if lightdash_type in _NUMERIC_AGGREGATES and column_type == "number":
        return OssieDataType.DECIMAL
    if lightdash_type in _ORDER_AGGREGATES:
        return lightdash_type_to_datatype(column_type)
    return None


def is_temporal(datatype: Optional[OssieDataType]) -> bool:
    """True when the datatype represents a point in time."""
    return datatype in _TEMPORAL
