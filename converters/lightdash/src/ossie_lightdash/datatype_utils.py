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

from ossie import OSIDataType

# Ossie datatype -> Lightdash dimension type.
_DATATYPE_TO_LIGHTDASH = {
    OSIDataType.STRING: "string",
    OSIDataType.INTEGER: "number",
    OSIDataType.DECIMAL: "number",
    OSIDataType.FLOAT: "number",
    OSIDataType.BOOLEAN: "boolean",
    OSIDataType.DATE: "date",
    OSIDataType.DATE_TIME: "timestamp",
    OSIDataType.DATE_TIME_TZ: "timestamp",
    # Lightdash has no time-of-day dimension type; a string keeps the value
    # visible rather than dropping the column.
    OSIDataType.TIME: "string",
    OSIDataType.OPAQUE: "string",
}

# Lightdash dimension type -> Ossie datatype. Numeric widths are not expressed
# in Lightdash, so `number` maps to the widest exact type.
_LIGHTDASH_TO_DATATYPE = {
    "string": OSIDataType.STRING,
    "number": OSIDataType.DECIMAL,
    "boolean": OSIDataType.BOOLEAN,
    "date": OSIDataType.DATE,
    "timestamp": OSIDataType.DATE_TIME,
}

_TEMPORAL = {OSIDataType.DATE, OSIDataType.TIME, OSIDataType.DATE_TIME, OSIDataType.DATE_TIME_TZ}


def datatype_to_lightdash_type(datatype: Optional[OSIDataType]) -> Optional[str]:
    """Return the Lightdash dimension type for an Ossie datatype, if any."""
    if datatype is None:
        return None
    return _DATATYPE_TO_LIGHTDASH.get(datatype)


def lightdash_type_to_datatype(lightdash_type: Optional[str]) -> Optional[OSIDataType]:
    """Return the Ossie datatype for a Lightdash dimension type, if any."""
    if lightdash_type is None:
        return None
    return _LIGHTDASH_TO_DATATYPE.get(lightdash_type)


def is_temporal(datatype: Optional[OSIDataType]) -> bool:
    """True when the datatype represents a point in time."""
    return datatype in _TEMPORAL
