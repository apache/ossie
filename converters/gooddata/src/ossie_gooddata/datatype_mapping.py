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

"""Data type mapping shared by the GoodData converters."""

from __future__ import annotations

import warnings

GOODDATA_TO_OSSIE_DATATYPES = {
    "STRING": "String",
    "INT": "Integer",
    "NUMERIC": "Decimal",
    "BOOLEAN": "Boolean",
    "DATE": "Date",
    "TIMESTAMP": "DateTime",
    "TIMESTAMP_TZ": "DateTimeTz",
}

OSSIE_TO_GOODDATA_DATATYPES = {value: key for key, value in GOODDATA_TO_OSSIE_DATATYPES.items()}

def gooddata_to_ossie_datatype(source_type: str | None) -> str | None:
    """Map a GoodData source column type to an Ossie DataType value."""
    if source_type is None or not source_type.strip():
        return None
    return GOODDATA_TO_OSSIE_DATATYPES.get(source_type.strip().upper(), "Opaque")


def ossie_to_gooddata_datatype(
    datatype: str | None,
    *,
    default: str,
    field_name: str,
    extension_type: str | None = None,
) -> str:
    """Choose a GoodData source type, preserving exact extension data first."""
    mapped_type = OSSIE_TO_GOODDATA_DATATYPES.get(datatype) if datatype is not None else None

    if extension_type is not None and extension_type.strip():
        normalized_extension_type = extension_type.strip().upper()
        if datatype is None or datatype == "Opaque":
            return normalized_extension_type
        if mapped_type is not None and normalized_extension_type != mapped_type:
            warnings.warn(
                f"Field '{field_name}' has Ossie datatype '{datatype}', which maps to "
                f"GoodData '{mapped_type}', but its GOODDATA extension specifies "
                f"'{normalized_extension_type}'. Preserving the extension value.",
                stacklevel=2,
            )
        elif datatype == "Float":
            if normalized_extension_type == "NUMERIC":
                warnings.warn(
                    f"Field '{field_name}' has Ossie datatype 'Float'; GoodData only has "
                    "NUMERIC, so the exact/approximate distinction will be lost.",
                    stacklevel=2,
                )
            else:
                warnings.warn(
                    f"Field '{field_name}' has Ossie datatype 'Float', which maps lossily to "
                    f"GoodData 'NUMERIC', but its GOODDATA extension specifies "
                    f"'{normalized_extension_type}'. Preserving the extension value.",
                    stacklevel=2,
                )
        elif datatype == "Time":
            warnings.warn(
                f"Field '{field_name}' has Ossie datatype 'Time', which has no native "
                f"GoodData source column type. Preserving the extension value "
                f"'{normalized_extension_type}'.",
                stacklevel=2,
            )
        elif mapped_type is None:
            warnings.warn(
                f"Field '{field_name}' has unrecognized Ossie datatype '{datatype}'. "
                f"Preserving the GOODDATA extension value '{normalized_extension_type}'.",
                stacklevel=2,
            )
        return normalized_extension_type

    if datatype is None:
        return default
    if mapped_type is not None:
        return mapped_type
    if datatype == "Float":
        warnings.warn(
            f"Field '{field_name}' has Ossie datatype 'Float'; GoodData only has "
            "NUMERIC, so the exact/approximate distinction will be lost.",
            stacklevel=2,
        )
        return "NUMERIC"
    if datatype == "Time":
        warnings.warn(
            f"Field '{field_name}' has Ossie datatype 'Time', which has no native "
            f"GoodData source column type. Using the role default '{default}'.",
            stacklevel=2,
        )
        return default
    if datatype == "Opaque":
        warnings.warn(
            f"Field '{field_name}' has Ossie datatype 'Opaque' without an exact "
            f"GoodData source type in its GOODDATA extension. Using the role default '{default}'.",
            stacklevel=2,
        )
        return default

    warnings.warn(
        f"Field '{field_name}' has unrecognized Ossie datatype '{datatype}'. "
        f"Using the GoodData role default '{default}'.",
        stacklevel=2,
    )
    return default
