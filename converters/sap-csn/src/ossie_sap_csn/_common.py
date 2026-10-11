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

"""Names and helpers shared by both conversion directions."""

import json
from typing import Any, Optional

from ossie import OssieCustomExtension

from ossie_sap_csn.converter_issues import ConverterError

VENDOR = "SAP_CSN"

# Standard SAP annotations this converter reads and writes.
LABEL_KEYS = ("@EndUserText.label", "@Common.Label")
QUICKINFO_KEYS = ("@EndUserText.quickInfo", "@Common.QuickInfo")
AGGREGATION_KEY = "@Aggregation.default"
DATA_CATEGORY_KEY = "@Analytics.dataCategory"
UNIQUE_PREFIX = "@assert.unique."
TEMPORAL_PREFIXES = ("@Semantics.businessDate", "@Semantics.calendar", "@Semantics.fiscal")

# Annotations this converter invents, so Ossie content that CSN has no home for
# survives a round trip. CSN allows arbitrary annotations; consumers ignore these.
OSSIE_AI_CONTEXT = "@Ossie.aiContext"
OSSIE_EXTENSIONS = "@Ossie.customExtensions"
OSSIE_IS_TIME = "@Ossie.isTime"
OSSIE_METRIC = "@Ossie.metric"
OSSIE_SQL = "@Ossie.sql"
# The full key lists, written only when a key names a column CSN has no element for.
OSSIE_PRIMARY_KEY = "@Ossie.primaryKey"
OSSIE_UNIQUE_KEYS = "@Ossie.uniqueKeys"
OSSIE_ANNOTATIONS = frozenset(
    {
        OSSIE_AI_CONTEXT,
        OSSIE_EXTENSIONS,
        OSSIE_IS_TIME,
        OSSIE_METRIC,
        OSSIE_SQL,
        OSSIE_PRIMARY_KEY,
        OSSIE_UNIQUE_KEYS,
    }
)

# `meta.ossie` on the CSN root holds what has no element-level home.
META_KEY = "ossie"

# Aggregations `@Aggregation.default` can carry that Ossie metrics express.
AGGREGATIONS = ("SUM", "MIN", "MAX", "AVG", "COUNT", "COUNT_DISTINCT")


def enum_value(value: Any) -> Optional[str]:
    """The symbol of a CSN enum annotation value (`{"#": "SUM"}` or `"#SUM"`)."""
    if isinstance(value, dict) and isinstance(value.get("#"), str):
        return value["#"]
    if isinstance(value, str) and value.startswith("#"):
        return value[1:]
    return None


def enum_annotation(symbol: str) -> dict[str, str]:
    return {"#": symbol}


def vendor_extension(data: dict[str, Any]) -> OssieCustomExtension:
    return OssieCustomExtension(vendor_name=VENDOR, data=json.dumps(data, sort_keys=True))


def read_vendor_extension(extensions: Optional[list[OssieCustomExtension]], where: str) -> dict[str, Any]:
    """The decoded SAP_CSN extension payload, or ``{}`` when there is none."""
    for extension in extensions or []:
        if extension.vendor_name == VENDOR:
            try:
                data = json.loads(extension.data)
            except json.JSONDecodeError as exc:
                raise ConverterError(f"{where}: SAP_CSN custom extension is not valid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise ConverterError(f"{where}: SAP_CSN custom extension must be a JSON object")
            return data
    return {}


def foreign_extensions(extensions: Optional[list[OssieCustomExtension]]) -> list[dict[str, str]]:
    """Extensions that belong to other vendors, to carry through untouched."""
    return [e.model_dump() for e in extensions or [] if e.vendor_name != VENDOR]


def split_annotations(obj: dict[str, Any], consumed: set[str]) -> dict[str, Any]:
    """Annotations on ``obj`` that the converter did not map to an Ossie property."""
    return {
        k: v
        for k, v in obj.items()
        if k.startswith("@") and k not in consumed and k not in OSSIE_ANNOTATIONS
    }
