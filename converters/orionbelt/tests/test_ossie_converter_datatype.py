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
"""Ossie ``datatype`` support on the field conversion path.

Ossie defines a first-class ``datatype`` on ``Field``/``Metric`` backed by a
capitalised ``DataType`` enum. The converter reads it (over the name heuristic)
on import and emits it on export. Because OBML's ``abstractType`` is a coarse
*logical* layer with no exact ``decimal``, ``Decimal`` narrows to ``float`` for
fields - so the exact ``abstractType`` is stashed for a lossless return trip.
"""

from __future__ import annotations

from typing import Any

import ossie_orionbelt.converter as conv


def _ossie_field(name: str, **extra: Any) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]},
    }
    field.update(extra)
    return field


def _ossie_model(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "sales",
                "datasets": [
                    {"name": "Orders", "source": "ANALYTICS.PUBLIC.ORDERS", "fields": fields}
                ],
            }
        ],
    }


def _obml_columns(obml: dict[str, Any]) -> dict[str, dict[str, Any]]:
    (obj,) = obml["dataObjects"].values()
    return obj["columns"]


def _ossie_fields(ossie: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ds in ossie["semantic_model"][0]["datasets"]:
        for f in ds.get("fields", []):
            out[f["name"]] = f
    return out


class TestImportDatatype:
    """Ossie ``datatype`` maps to OBML ``abstractType`` on import."""

    def test_direct_mappings(self) -> None:
        ossie = _ossie_model(
            [
                _ossie_field("s", datatype="String"),
                _ossie_field("i", datatype="Integer"),
                _ossie_field("f", datatype="Float"),
                _ossie_field("b", datatype="Boolean"),
                _ossie_field("d", datatype="Date"),
                _ossie_field("t", datatype="Time"),
                _ossie_field("dt", datatype="DateTime"),
                _ossie_field("dttz", datatype="DateTimeTz"),
            ]
        )
        cols = _obml_columns(conv.OssietoOBML(ossie).convert())
        got = {name: c["abstractType"] for name, c in cols.items()}
        assert got == {
            "s": "string",
            "i": "int",
            "f": "float",
            "b": "boolean",
            "d": "date",
            "t": "time",
            "dt": "timestamp",
            "dttz": "timestamp_tz",
        }

    def test_decimal_narrows_to_float(self) -> None:
        cols = _obml_columns(
            conv.OssietoOBML(_ossie_model([_ossie_field("d", datatype="Decimal")])).convert()
        )
        assert cols["d"]["abstractType"] == "float"

    def test_opaque_falls_back_to_heuristic(self) -> None:
        # `price` is a heuristic float keyword; Opaque must not override it to a
        # literal type - it means "unknown/non-portable", so the heuristic runs.
        cols = _obml_columns(
            conv.OssietoOBML(_ossie_model([_ossie_field("price", datatype="Opaque")])).convert()
        )
        assert cols["price"]["abstractType"] == "float"

    def test_datatype_wins_over_legacy_and_heuristic(self) -> None:
        # New capitalised `datatype` beats the legacy lowercase `data_type` and
        # the name heuristic (name `amount` would heuristically be float).
        ossie = _ossie_model([_ossie_field("amount", datatype="Integer", data_type="number")])
        cols = _obml_columns(conv.OssietoOBML(ossie).convert())
        assert cols["amount"]["abstractType"] == "int"


class TestExportDatatype:
    """OBML ``abstractType`` emits a first-class Ossie ``datatype`` on export."""

    @staticmethod
    def _obml(abstract_type: str) -> dict[str, Any]:
        return {
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "columns": {"Val": {"code": "val", "abstractType": abstract_type}},
                }
            }
        }

    def test_emits_capitalised_datatype(self) -> None:
        for abstract_type, expected in [
            ("int", "Integer"),
            ("float", "Float"),
            ("timestamp_tz", "DateTimeTz"),
            ("json", "Opaque"),
            ("boolean", "Boolean"),
        ]:
            ossie = conv.OBMLtoOssie(self._obml(abstract_type), model_name="s").convert()
            assert _ossie_fields(ossie)["val"]["datatype"] == expected


class TestRoundtripLossless:
    """OBML -> Ossie -> OBML preserves the exact abstractType via the stash."""

    def test_narrowing_types_survive(self) -> None:
        # json -> Opaque and time_tz -> Time are lossy in the datatype map alone;
        # the stashed obml_abstract_type must restore them exactly.
        for abstract_type in ["json", "time_tz", "float", "timestamp_tz"]:
            obml = {
                "dataObjects": {
                    "Orders": {
                        "code": "orders",
                        "columns": {"Val": {"code": "val", "abstractType": abstract_type}},
                    }
                }
            }
            back = conv.OssietoOBML(conv.OBMLtoOssie(obml, model_name="s").convert()).convert()
            # The column key round-trips to its code (`val`); assert on the
            # single column's abstractType regardless of its restored key.
            (col,) = _obml_columns(back).values()
            assert col["abstractType"] == abstract_type


def _ossie_model_with_metric(datatype: str) -> dict[str, Any]:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "sales",
                "datasets": [
                    {
                        "name": "Orders",
                        "source": "A.P.ORDERS",
                        "fields": [_ossie_field("amount")],
                    }
                ],
                "metrics": [
                    {
                        "name": "Total",
                        "expression": {
                            "dialects": [
                                {"dialect": "ANSI_SQL", "expression": "SUM(Orders.amount)"}
                            ]
                        },
                        "datatype": datatype,
                    }
                ],
            }
        ],
    }


class TestMetricDatatype:
    """Ossie metric ``datatype`` maps to the exact OBML ``dataType`` and round-trips."""

    def test_import_sets_exact_data_type(self) -> None:
        for ossie_dt, expected in [
            ("Decimal", "decimal(18, 2)"),
            ("Integer", "integer"),
            ("Float", "double"),
        ]:
            obml = conv.OssietoOBML(_ossie_model_with_metric(ossie_dt)).convert()
            # SUM(Orders.amount) becomes an OBML measure named "Total".
            assert obml["measures"]["Total"]["dataType"] == expected

    def test_roundtrip_preserves_metric_datatype(self) -> None:
        # Regression: Ossie -> OBML -> Ossie used to drop the metric datatype.
        for ossie_dt in ["Decimal", "Integer", "Float"]:
            ossie = _ossie_model_with_metric(ossie_dt)
            back = conv.OBMLtoOssie(conv.OssietoOBML(ossie).convert(), model_name="sales").convert()
            metric = back["semantic_model"][0]["metrics"][0]
            assert metric.get("datatype") == ossie_dt

    def test_plain_measure_emits_no_datatype(self) -> None:
        # A measure with no explicit dataType (only the defaulted resultType)
        # must not gain a datatype on export - keeps round trips idempotent.
        obml = {
            "dataObjects": {
                "Orders": {
                    "code": "orders",
                    "columns": {"Amount": {"code": "amount", "abstractType": "float"}},
                }
            },
            "measures": {
                "Total": {
                    "columns": [{"dataObject": "Orders", "column": "Amount"}],
                    "resultType": "float",
                    "aggregation": "sum",
                }
            },
        }
        ossie = conv.OBMLtoOssie(obml, model_name="s").convert()
        metric = ossie["semantic_model"][0]["metrics"][0]
        assert "datatype" not in metric
