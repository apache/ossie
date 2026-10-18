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

"""An Ossie field is named by the OBML column name, never by the column code.

The code is the field's expression, and an Ossie import writes a calculated
field's SQL into it (apache/ossie#601). Naming the field by its code turned
``amount_with_tax`` into a field called ``amount * 1.1`` on the way back.
"""

from __future__ import annotations

import json
from typing import Any

import ossie_orionbelt.converter as conv


def _expr(sql: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def _ossie() -> dict[str, Any]:
    return {
        "version": "0.2.0",
        "name": "sales",
        "datasets": [
            {
                "name": "orders",
                "source": "db.orders",
                "primary_key": ["id"],
                "fields": [
                    {"name": "id", "expression": _expr("order_id")},
                    {"name": "amount", "expression": _expr("amount")},
                    {"name": "amount_with_tax", "expression": _expr("amount * 1.1")},
                ],
            }
        ],
        "metrics": [
            {
                "name": "taxed_revenue",
                "expression": _expr("SUM(orders.amount_with_tax)"),
            }
        ],
    }


def _dataset(ossie: dict[str, Any]) -> dict[str, Any]:
    return ossie["datasets"][0]


def _fields(ossie: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in _dataset(ossie)["fields"]}


class TestOssieFirstRoundTrip:
    def test_calculated_field_keeps_its_name(self) -> None:
        back = conv.OBMLtoOssie(conv.OssietoOBML(_ossie()).convert()).convert()
        fields = _fields(back)
        assert set(fields) == {"id", "amount", "amount_with_tax"}
        calc = fields["amount_with_tax"]
        assert calc["expression"]["dialects"][0]["expression"] == "amount * 1.1"
        assert "description" not in calc
        assert "ai_context" not in calc

    def test_renamed_field_keeps_its_name_and_primary_key(self) -> None:
        back = conv.OBMLtoOssie(conv.OssietoOBML(_ossie()).convert()).convert()
        assert _fields(back)["id"]["expression"]["dialects"][0]["expression"] == "order_id"
        assert _dataset(back)["primary_key"] == ["id"]

    def test_primary_key_matches_the_field_name(self) -> None:
        obml = conv.OssietoOBML(_ossie()).convert()
        assert obml["dataObjects"]["orders"]["columns"]["id"].get("primaryKey") is True

    def test_metric_references_the_calculated_field(self) -> None:
        back = conv.OBMLtoOssie(conv.OssietoOBML(_ossie()).convert()).convert()
        metric = next(m for m in back["metrics"] if m["name"] == "taxed_revenue")
        assert metric["expression"]["dialects"][0]["expression"] == ("SUM(orders.amount_with_tax)")

    def test_no_column_name_stash(self) -> None:
        back = conv.OBMLtoOssie(conv.OssietoOBML(_ossie()).convert()).convert()
        for field in _dataset(back)["fields"]:
            for ext in field.get("custom_extensions", []):
                assert "obml_column_name" not in json.loads(ext["data"])


class TestLegacyCodeNamedExport:
    """Earlier exports named fields by code and stashed the column name."""

    def test_column_name_and_primary_key_restored(self) -> None:
        legacy = {
            "version": "0.2.0",
            "name": "sales",
            "datasets": [
                {
                    "name": "Orders",
                    "source": "WH.PUBLIC.orders",
                    "primary_key": ["order_id"],
                    "fields": [
                        {
                            "name": "order_id",
                            "expression": _expr("order_id"),
                            "custom_extensions": [
                                {
                                    "vendor_name": "ORIONBELT",
                                    "data": json.dumps({"obml_column_name": "Order ID"}),
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        columns = conv.OssietoOBML(legacy).convert()["dataObjects"]["Orders"]["columns"]
        assert set(columns) == {"Order ID"}
        assert columns["Order ID"]["code"] == "order_id"
        assert columns["Order ID"].get("primaryKey") is True


def _legacy_field(code: str, column: str) -> dict[str, Any]:
    """A field as earlier exports wrote it: named by code, column stashed."""
    return {
        "name": code,
        "expression": _expr(code),
        "custom_extensions": [
            {"vendor_name": "ORIONBELT", "data": json.dumps({"obml_column_name": column})}
        ],
    }


def _legacy_export() -> dict[str, Any]:
    return {
        "version": "0.2.0",
        "name": "sales",
        "datasets": [
            {
                "name": "Orders",
                "source": "WH.PUBLIC.orders",
                "primary_key": ["order_id"],
                "fields": [
                    _legacy_field("order_id", "Order ID"),
                    _legacy_field("customer_id", "Customer"),
                    _legacy_field("amount", "Net Amount"),
                ],
            },
            {
                "name": "Customers",
                "source": "WH.PUBLIC.customers",
                "unique_keys": [["customer_id"]],
                "fields": [_legacy_field("customer_id", "Customer ID")],
            },
        ],
        "relationships": [
            {
                "name": "orders_to_customers",
                "from": "Orders",
                "to": "Customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
            }
        ],
        "metrics": [
            {
                "name": "grand_total",
                "expression": _expr('SUM("Orders"."amount") OVER ()'),
            }
        ],
    }


class TestLegacyStashesFollowTheRename:
    """What the import keeps verbatim from an older export is renamed with it."""

    @staticmethod
    def _metric(sql: str, dialect: str = "ANSI_SQL") -> tuple[str, list[str]]:
        legacy = _legacy_export()
        legacy["metrics"] = [
            {"name": "m", "expression": {"dialects": [{"dialect": dialect, "expression": sql}]}}
        ]
        importer = conv.OssietoOBML(legacy)
        ossie = conv.OBMLtoOssie(importer.convert()).convert()
        metric = next(m for m in ossie["metrics"] if m["name"] == "m")
        return metric["expression"]["dialects"][0]["expression"], importer.warnings

    def test_unique_keys_name_the_fields(self) -> None:
        obml = conv.OssietoOBML(_legacy_export()).convert()
        assert obml["dataObjects"]["Orders"]["joins"][0]["joinType"] == "many-to-one"
        ossie = conv.OBMLtoOssie(obml).convert()
        customers = ossie["datasets"][1]
        assert customers["unique_keys"] == [["Customer ID"]]
        back = conv.OssietoOBML(ossie).convert()
        assert back["dataObjects"]["Orders"]["joins"][0]["joinType"] == "many-to-one"

    def test_preserved_metric_references_the_fields(self) -> None:
        sql, _ = self._metric('"Orders"."amount" * 2')
        assert sql == '"Orders"."Net Amount" * 2'

    def test_unquoted_reference_matches_regardless_of_case(self) -> None:
        sql, _ = self._metric("Orders.AMOUNT * 2")
        assert sql == 'Orders."Net Amount" * 2'

    def test_only_the_field_tokens_change(self) -> None:
        # sqlglot drops a weighted percentile's third argument when it
        # regenerates SQL; the rename must not.
        sql, _ = self._metric("percentile(Orders.amount, 0.5, Orders.customer_id)", "DATABRICKS")
        assert sql == "percentile(Orders.`Net Amount`, 0.5, Orders.Customer)"

    def test_swapped_names_follow_the_stashed_mapping(self) -> None:
        # Column "Net Amount" has code amount, column "amount" has code
        # adjustment: field amount is "Net Amount", not the column amount.
        legacy = _legacy_export()
        orders = legacy["datasets"][0]
        orders["fields"] = [
            _legacy_field("order_id", "Order ID"),
            _legacy_field("customer_id", "Customer"),
            _legacy_field("amount", "Net Amount"),
            _legacy_field("adjustment", "amount"),
        ]
        orders["unique_keys"] = [["amount"], ["adjustment"]]
        legacy["metrics"] = [
            {
                "name": "m",
                "expression": _expr("Orders.amount - Orders.adjustment"),
            }
        ]
        ossie = conv.OBMLtoOssie(conv.OssietoOBML(legacy).convert()).convert()
        dataset = ossie["datasets"][0]
        assert dataset["unique_keys"] == [["Net Amount"], ["amount"]]
        metric = ossie["metrics"][0]
        assert metric["expression"]["dialects"][0]["expression"] == (
            'Orders."Net Amount" - Orders.amount'
        )

    def test_untokenizable_metric_is_kept_and_warned(self) -> None:
        sql, warnings = self._metric('"Orders"."amount" || \'unterminated')
        assert sql == '"Orders"."amount" || \'unterminated'
        assert any("'m'" in w and "not renamed" in w for w in warnings)

    def test_alias_declared_in_the_sql_is_not_the_dataset(self) -> None:
        # The query keeps the metric out of OBML, so it is preserved verbatim.
        sql, _ = self._metric(
            'SUM("Orders"."amount") OVER () + '
            '(SELECT MAX("Orders"."amount") FROM adjustments AS "Orders")'
        )
        assert sql == (
            'SUM("Orders"."Net Amount") OVER () + '
            '(SELECT MAX("Orders"."amount") FROM adjustments AS "Orders")'
        )

    def test_bracket_quoted_reference(self) -> None:
        sql, _ = self._metric("[Orders].[amount] * 2")
        assert sql == "[Orders].[Net Amount] * 2"


def _saved_by_an_older_version() -> dict[str, Any]:
    """OBML an older import saved: its verbatim payloads name fields by code."""
    return {
        "version": 1.0,
        "dataObjects": {
            "Orders": {
                "code": "orders",
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {
                    "Order ID": {"code": "order_id", "abstractType": "string"},
                    "Customer": {"code": "customer_id", "abstractType": "string"},
                    "Net Amount": {"code": "amount", "abstractType": "float"},
                },
                "joins": [
                    {
                        "joinType": "many-to-one",
                        "joinTo": "Customers",
                        "columnsFrom": ["Customer"],
                        "columnsTo": ["Customer ID"],
                    }
                ],
            },
            "Customers": {
                "code": "customers",
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {"Customer ID": {"code": "customer_id", "abstractType": "string"}},
                "customExtensions": [
                    {"vendor": "Ossie", "data": json.dumps({"obml_unique_keys": [["customer_id"]]})}
                ],
            },
        },
        "customExtensions": [
            {
                "vendor": "Ossie",
                "data": json.dumps(
                    {
                        "obml_unconverted_metrics": [
                            {"name": "m", "expression": _expr('SUM("Orders"."amount") OVER ()')}
                        ]
                    }
                ),
            }
        ],
    }


class TestSavedByOlderVersion:
    """OBML saved by an earlier version is renamed on export, from the column codes."""

    def test_unique_keys_and_metric_name_the_fields(self) -> None:
        ossie = conv.OBMLtoOssie(_saved_by_an_older_version()).convert()
        model = ossie
        assert model["datasets"][1]["unique_keys"] == [["Customer ID"]]
        metric = next(m for m in model["metrics"] if m["name"] == "m")
        assert metric["expression"]["dialects"][0]["expression"] == (
            'SUM("Orders"."Net Amount") OVER ()'
        )
        back = conv.OssietoOBML(ossie).convert()
        assert back["dataObjects"]["Orders"]["joins"][0]["joinType"] == "many-to-one"

    def test_code_that_is_another_column_name_is_kept_and_warned(self) -> None:
        obml = _saved_by_an_older_version()
        customers = obml["dataObjects"]["Customers"]
        customers["columns"]["customer_id"] = {"code": "legacy_id", "abstractType": "string"}
        exporter = conv.OBMLtoOssie(obml)
        ossie = exporter.convert()
        assert ossie["datasets"][1]["unique_keys"] == [["customer_id"]]
        assert any("'customer_id'" in w and "another column's code" in w for w in exporter.warnings)

    def test_marked_payload_is_emitted_as_is(self) -> None:
        obml = conv.OssietoOBML(conv.OBMLtoOssie(_saved_by_an_older_version()).convert()).convert()
        exporter = conv.OBMLtoOssie(obml)
        ossie = exporter.convert()
        assert ossie["datasets"][1]["unique_keys"] == [["Customer ID"]]
        assert not any("unique key" in w for w in exporter.warnings)


class TestMalformedPreservedMetrics:
    """A preserved metric is one the converter could not handle; renaming skips
    what is not shaped like a metric expression instead of crashing."""

    _STRING_EXPRESSION = {"name": "m", "expression": "SUM(Orders.amount)"}

    def test_export_of_obml_saved_by_an_older_version(self) -> None:
        obml = _saved_by_an_older_version()
        payload = {"obml_unconverted_metrics": [dict(self._STRING_EXPRESSION), "junk"]}
        obml["customExtensions"] = [{"vendor": "Ossie", "data": json.dumps(payload)}]
        ossie = conv.OBMLtoOssie(obml).convert()
        assert self._STRING_EXPRESSION in ossie["metrics"]

    def test_import_of_an_older_export(self) -> None:
        legacy = _legacy_export()
        legacy["metrics"] = [dict(self._STRING_EXPRESSION)]
        obml = conv.OssietoOBML(legacy).convert()
        ossie = conv.OBMLtoOssie(obml).convert()
        assert self._STRING_EXPRESSION in ossie["metrics"]


class TestRenamedFieldsDifferingOnlyByCase:
    """A preserved metric renamed to Amount and amount keeps two references."""

    @staticmethod
    def _legacy() -> dict[str, Any]:
        return {
            "version": "0.2.0",
            "name": "sales",
            "datasets": [
                {
                    "name": "Orders",
                    "source": "WH.PUBLIC.orders",
                    "fields": [_legacy_field("a", "Amount"), _legacy_field("b", "amount")],
                }
            ],
            "metrics": [
                {"name": "upper", "expression": _expr("Orders.a * 2")},
                {"name": "lower", "expression": _expr("Orders.b * 2")},
            ],
        }

    @staticmethod
    def _sql(ossie: dict[str, Any]) -> dict[str, str]:
        return {m["name"]: m["expression"]["dialects"][0]["expression"] for m in ossie["metrics"]}

    def test_import_of_an_older_export(self) -> None:
        ossie = conv.OBMLtoOssie(conv.OssietoOBML(self._legacy()).convert()).convert()
        assert self._sql(ossie) == {
            "upper": 'Orders."Amount" * 2',
            "lower": 'Orders."amount" * 2',
        }

    def test_export_of_obml_saved_by_an_older_version(self) -> None:
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "columns": {
                        "Amount": {"code": "a", "abstractType": "float"},
                        "amount": {"code": "b", "abstractType": "float"},
                    },
                }
            },
            "customExtensions": [
                {
                    "vendor": "Ossie",
                    "data": json.dumps({"obml_unconverted_metrics": self._legacy()["metrics"]}),
                }
            ],
        }
        assert self._sql(conv.OBMLtoOssie(obml).convert()) == {
            "upper": 'Orders."Amount" * 2',
            "lower": 'Orders."amount" * 2',
        }
