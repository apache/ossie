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

"""Vendor-identity rules for Ossie custom_extensions.

- OBML -> Ossie tags OrionBelt-proprietary payloads as ``ORIONBELT``.
- Ossie -> OBML stashes Ossie-native fields OBML can't hold under ``Ossie``.
- Third-party vendor extensions (SNOWFLAKE, DBT, ...) round-trip verbatim,
  never relabelled, at model / dataObject / column level.
- Legacy ``COMMON`` / ``OBSL`` tags are still accepted on read (back-compat).
"""

from __future__ import annotations

import json
from typing import Any

import ossie_orionbelt.converter as conv


def _ossie_field(name: str, **extra: Any) -> dict[str, Any]:
    field = {
        "name": name,
        "data_type": "string",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]},
    }
    field.update(extra)
    return field


class TestOwnVendorTags:
    def test_obml_to_ossie_uses_orionbelt(self) -> None:
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "database": "WH",
                    "schema": "PUBLIC",
                    "owner": "data-team",
                    "columns": {"Amount": {"code": "amount", "abstractType": "float"}},
                }
            },
        }
        ossie = conv.OBMLtoOssie(obml).convert()
        ce = ossie["semantic_model"][0]["custom_extensions"]
        assert all(e["vendor_name"] == "ORIONBELT" for e in ce)

    def test_ossie_to_obml_native_stash_uses_ossie_vendor(self) -> None:
        ossie = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "m",
                    "datasets": [
                        {
                            "name": "Customers",
                            "source": "WH.PUB.customers",
                            "unique_keys": [["customer_id"]],
                            "fields": [_ossie_field("customer_id")],
                        }
                    ],
                }
            ],
        }
        obml = conv.OssietoOBML(ossie).convert()
        ce = obml["dataObjects"]["Customers"]["customExtensions"]
        assert any(
            e["vendor"] == "Ossie" and json.loads(e["data"]).get("obml_unique_keys") for e in ce
        )


class TestForeignVendorRoundtrip:
    def _ossie_with_foreign(self) -> dict[str, Any]:
        return {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "demo",
                    "custom_extensions": [
                        {"vendor_name": "DBT", "data": json.dumps({"model": "mart_x"})}
                    ],
                    "datasets": [
                        {
                            "name": "Customers",
                            "source": "WH.PUB.customers",
                            "custom_extensions": [
                                {
                                    "vendor_name": "SALESFORCE",
                                    "data": json.dumps({"object": "Account"}),
                                }
                            ],
                            "fields": [
                                _ossie_field(
                                    "customer_id",
                                    custom_extensions=[
                                        {
                                            "vendor_name": "GOODDATA",
                                            "data": json.dumps({"ldm": "a"}),
                                        }
                                    ],
                                )
                            ],
                        }
                    ],
                }
            ],
        }

    def test_foreign_carried_into_obml(self) -> None:
        obml = conv.OssietoOBML(self._ossie_with_foreign()).convert()
        assert {"vendor": "DBT", "data": json.dumps({"model": "mart_x"})} in obml[
            "customExtensions"
        ]
        do = obml["dataObjects"]["Customers"]
        assert {"vendor": "SALESFORCE", "data": json.dumps({"object": "Account"})} in do[
            "customExtensions"
        ]
        col = do["columns"]["customer_id"]
        assert {"vendor": "GOODDATA", "data": json.dumps({"ldm": "a"})} in col["customExtensions"]

    def test_foreign_reemitted_to_ossie(self) -> None:
        obml = conv.OssietoOBML(self._ossie_with_foreign()).convert()
        ossie = conv.OBMLtoOssie(obml, "demo").convert()
        sm = ossie["semantic_model"][0]
        model_vendors = {e["vendor_name"] for e in sm["custom_extensions"]}
        ds_vendors = {e["vendor_name"] for e in sm["datasets"][0]["custom_extensions"]}
        field_vendors = {
            e["vendor_name"] for e in sm["datasets"][0]["fields"][0]["custom_extensions"]
        }
        assert "DBT" in model_vendors
        assert "SALESFORCE" in ds_vendors
        assert "GOODDATA" in field_vendors

    def test_foreign_metric_roundtrip(self) -> None:
        ossie_in = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "demo",
                    "datasets": [
                        {
                            "name": "Sales",
                            "source": "WH.PUB.sales",
                            "fields": [_ossie_field("amount")],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "Total",
                            "data_type": "number",
                            "description": "d",
                            "custom_extensions": [
                                {"vendor_name": "LOOKER", "data": json.dumps({"view": "sales"})}
                            ],
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "SUM(sales.amount)"}
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        obml = conv.OssietoOBML(ossie_in).convert()
        target = (obml.get("measures") or {}).get("Total") or (obml.get("metrics") or {}).get(
            "Total"
        )
        assert {"vendor": "LOOKER", "data": json.dumps({"view": "sales"})} in target[
            "customExtensions"
        ]
        ossie_out = conv.OBMLtoOssie(obml, "demo").convert()
        metric = ossie_out["semantic_model"][0]["metrics"][0]
        assert any(e["vendor_name"] == "LOOKER" for e in metric["custom_extensions"])

    def test_foreign_dimension_emitted_to_field(self) -> None:
        # Ossie has no separate dimension entity, so an OBML dimension's foreign
        # extensions surface on the corresponding Ossie field.
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "database": "WH",
                    "schema": "PUBLIC",
                    "columns": {"Status": {"code": "status", "abstractType": "string"}},
                }
            },
            "dimensions": {
                "Status": {
                    "dataObject": "Orders",
                    "column": "Status",
                    "customExtensions": [
                        {"vendor": "TABLEAU", "data": json.dumps({"role": "dimension"})}
                    ],
                }
            },
        }
        ossie = conv.OBMLtoOssie(obml).convert()
        field = next(
            f for f in ossie["semantic_model"][0]["datasets"][0]["fields"] if f["name"] == "status"
        )
        assert any(e["vendor_name"] == "TABLEAU" for e in field["custom_extensions"])


class TestLegacyBackCompat:
    def test_legacy_common_and_obsl_still_read(self) -> None:
        # An OBML doc authored under the old scheme (COMMON / OBSL tags) must
        # still round-trip its payloads even though we now emit ORIONBELT / Ossie.
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "database": "WH",
                    "schema": "PUBLIC",
                    "columns": {
                        "Order ID": {
                            "code": "order_id",
                            "abstractType": "string",
                            "customExtensions": [
                                {
                                    "vendor": "OBSL",
                                    "data": json.dumps({"obml_field_label": "filter"}),
                                }
                            ],
                        }
                    },
                    "customExtensions": [
                        {"vendor": "OBSL", "data": json.dumps({"obml_unique_keys": [["order_id"]]})}
                    ],
                }
            },
        }
        ossie = conv.OBMLtoOssie(obml).convert()
        ds = ossie["semantic_model"][0]["datasets"][0]
        assert ds.get("unique_keys") == [["order_id"]]
        order_id = next(f for f in ds["fields"] if f["name"] == "order_id")
        assert order_id.get("label") == "filter"
