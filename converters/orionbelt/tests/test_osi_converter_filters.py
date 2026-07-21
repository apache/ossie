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

"""Tests for OSI ↔ OBML static filter roundtrip.

Validates that model-level static filters survive the OBML → OSI → OBML
roundtrip via custom_extensions preservation.
"""

from __future__ import annotations

import json
from typing import Any

import ossie_orionbelt.converter as conv

_OBML_WITH_FILTERS: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "ORDERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Order ID": {"code": "ORDER_ID", "abstractType": "string"},
                "Status": {"code": "STATUS", "abstractType": "string"},
                "Order Date": {"code": "ORDER_DATE", "abstractType": "date"},
                "Amount": {"code": "AMOUNT", "abstractType": "float"},
            },
        },
        "Customers": {
            "code": "CUSTOMERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Customer ID": {"code": "CUSTOMER_ID", "abstractType": "string"},
                "Region": {"code": "REGION", "abstractType": "string"},
            },
            "joins": [
                {
                    "joinType": "many-to-one",
                    "joinTo": "Orders",
                    "columnsFrom": ["Customer ID"],
                    "columnsTo": ["Order ID"],
                }
            ],
        },
    },
    "dimensions": {
        "Order Status": {
            "dataObject": "Orders",
            "column": "Status",
            "resultType": "string",
        },
    },
    "measures": {
        "Revenue": {
            "columns": [{"dataObject": "Orders", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
        },
    },
    "filters": [
        {
            "dataObject": "Orders",
            "column": "Status",
            "operator": "equals",
            "value": "completed",
        },
        {
            "dataObject": "Customers",
            "column": "Region",
            "operator": "in",
            "values": ["EMEA", "APAC"],
        },
    ],
}


class TestOBMLtoOSIFilters:
    """OBML → OSI preserves static filters in custom_extensions."""

    def test_filters_in_custom_extensions(self):
        converter = conv.OBMLtoOSI(_OBML_WITH_FILTERS)
        osi = converter.convert()
        sem = osi["semantic_model"][0]
        exts = sem.get("custom_extensions", [])
        assert len(exts) >= 1
        common = next(e for e in exts if e["vendor_name"] == "ORIONBELT")
        data = json.loads(common["data"])
        assert "obml_filters" in data
        assert len(data["obml_filters"]) == 2
        assert data["obml_filters"][0]["operator"] == "equals"
        assert data["obml_filters"][1]["operator"] == "in"

    def test_no_filters_no_key(self):
        obml = {**_OBML_WITH_FILTERS, "filters": []}
        converter = conv.OBMLtoOSI(obml)
        osi = converter.convert()
        sem = osi["semantic_model"][0]
        common = next(e for e in sem["custom_extensions"] if e["vendor_name"] == "ORIONBELT")
        data = json.loads(common["data"])
        assert "obml_filters" not in data


class TestOSItoOBMLFilters:
    """OSI → OBML restores static filters from custom_extensions."""

    def test_filters_restored(self):
        converter = conv.OBMLtoOSI(_OBML_WITH_FILTERS)
        osi = converter.convert()
        back = conv.OSItoOBML(osi).convert()
        assert "filters" in back
        assert len(back["filters"]) == 2
        assert back["filters"][0]["dataObject"] == "Orders"
        assert back["filters"][0]["column"] == "Status"
        assert back["filters"][0]["operator"] == "equals"
        assert back["filters"][0]["value"] == "completed"
        assert back["filters"][1]["operator"] == "in"
        assert back["filters"][1]["values"] == ["EMEA", "APAC"]

    def test_no_filters_no_key(self):
        obml = {k: v for k, v in _OBML_WITH_FILTERS.items() if k != "filters"}
        converter = conv.OBMLtoOSI(obml)
        osi = converter.convert()
        back = conv.OSItoOBML(osi).convert()
        assert "filters" not in back
