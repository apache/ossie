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

"""Regression tests for issue #201: OSI<->OBML converter round-trip fidelity
and validation robustness.

Covers three bugs found during the apache/ossie#153 review:

- P1: metric references broke the OBML -> OSI -> OBML round trip when a data
  object's physical ``code`` differs from its display name (the emitter writes
  the physical code, the importer resolved only by dataset name).
- P2: dimensions extracted from OSI collided when the same field name occurred
  in more than one dataset (the second silently overwrote the first).
- P2: ``validate_osi`` raised ``AttributeError`` on malformed input instead of
  returning a result with schema errors.
"""

from __future__ import annotations

import json
from typing import Any

import ossie_orionbelt.converter as conv

# ---------------------------------------------------------------------------
# P1: metric round-trip when dataObject.code != display name
# ---------------------------------------------------------------------------


class TestMetricRoundTripCodeVsName:
    _OBML: dict[str, Any] = {
        "version": 1.0,
        "dataObjects": {
            "Orders": {
                "code": "fact_orders",  # physical table code differs from name
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {"Amount": {"code": "amount", "abstractType": "float"}},
            }
        },
        "measures": {
            "Revenue": {
                "columns": [{"dataObject": "Orders", "column": "Amount"}],
                "resultType": "float",
                "aggregation": "sum",
            }
        },
    }

    def test_emitter_uses_physical_code(self) -> None:
        osi = conv.OBMLtoOSI(self._OBML).convert()
        sql = osi["semantic_model"][0]["metrics"][0]["expression"]["dialects"][0]["expression"]
        assert "fact_orders" in sql  # confirms the emit side uses the code

    def test_measure_survives_round_trip(self) -> None:
        osi = conv.OBMLtoOSI(self._OBML).convert()
        obml = conv.OSItoOBML(osi).convert()

        # The measure must come back as a real measure, not be stashed as an
        # unconverted metric.
        assert "Revenue" in obml.get("measures", {}), obml.get("measures")
        rev = obml["measures"]["Revenue"]
        assert rev["aggregation"] == "sum"
        assert rev["columns"] == [{"dataObject": "Orders", "column": "amount"}]

        # Nothing about Revenue should have leaked into an unconverted-metric stash.
        stashed = json.dumps(obml.get("customExtensions", []))
        assert "Revenue" not in stashed

    def test_third_party_field_name_differs_from_code(self) -> None:
        # Snowflake-style OSI: field display name != physical column code, and
        # the metric references the physical code. Must resolve, not drop.
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "WH.PUBLIC.fact_orders",
                            "fields": [
                                {
                                    "name": "Amount",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "amount"}
                                        ]
                                    },
                                    "data_type": "number",
                                }
                            ],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "Revenue",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "SUM(fact_orders.amount)"}
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        obml = conv.OSItoOBML(osi).convert()
        assert "Revenue" in obml.get("measures", {}), obml.get("measures")
        assert obml["measures"]["Revenue"]["columns"] == [
            {"dataObject": "Orders", "column": "Amount"}
        ]

    def test_physical_code_maps_to_field_name_with_space(self) -> None:
        # OSI field display name contains a space; the metric references the
        # physical column code. Resolution must produce a valid OBML measure
        # (column "Net Amount"), not a broken "{[Orders].[Net]} Amount" that
        # fails query resolution.
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "WH.PUBLIC.fact_orders",
                            "fields": [
                                {
                                    "name": "Net Amount",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "net_amount"}
                                        ]
                                    },
                                    "data_type": "number",
                                }
                            ],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "Net Revenue",
                            "expression": {
                                "dialects": [
                                    {
                                        "dialect": "ANSI_SQL",
                                        "expression": "SUM(fact_orders.net_amount)",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        obml = conv.OSItoOBML(osi).convert()
        assert "Net Revenue" in obml.get("measures", {}), obml.get("measures")
        m = obml["measures"]["Net Revenue"]
        assert m["aggregation"] == "sum"
        assert m["columns"] == [{"dataObject": "Orders", "column": "Net Amount"}]
        # It resolved to a clean single-column measure, not a dangling expression.
        assert "expression" not in m

    def test_quoted_physical_identifiers_resolve(self) -> None:
        # Snowflake/Databricks style: the source table and the field expression
        # are quoted identifiers. A metric referencing the bare physical code
        # must still resolve to a queryable measure, not fall through to LOSSY.
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": 'WH.PUBLIC."fact_orders"',
                            "fields": [
                                {
                                    "name": "Amount",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": '"net_amount"'}
                                        ]
                                    },
                                    "data_type": "number",
                                }
                            ],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "Revenue",
                            "expression": {
                                "dialects": [
                                    {
                                        "dialect": "ANSI_SQL",
                                        "expression": "SUM(fact_orders.net_amount)",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        obml = conv.OSItoOBML(osi).convert()
        assert "Revenue" in obml.get("measures", {}), obml.get("measures")
        assert obml["measures"]["Revenue"]["columns"] == [
            {"dataObject": "Orders", "column": "Amount"}
        ]


# ---------------------------------------------------------------------------
# P2: same field name in two datasets must not collide into one dimension
# ---------------------------------------------------------------------------


def _dim_dataset(name: str, source: str) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "fields": [
            {
                "name": "order_date",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]},
                "data_type": "date",
                "dimension": {},
            }
        ],
    }


class TestDimensionNameCollision:
    def test_both_dimensions_survive(self) -> None:
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        _dim_dataset("Orders", "WH.PUBLIC.orders"),
                        _dim_dataset("Invoices", "WH.PUBLIC.invoices"),
                    ],
                }
            ],
        }
        c = conv.OSItoOBML(osi)
        obml = c.convert()
        dims = obml.get("dimensions", {})

        # Two distinct dimensions, one per data object — neither dropped.
        assert len(dims) == 2, dims
        assert {d["dataObject"] for d in dims.values()} == {"Orders", "Invoices"}
        # The collision was disambiguated, not silent.
        assert any("multiple data objects" in w for w in c.warnings)


# ---------------------------------------------------------------------------
# P2: validate_osi robustness on malformed input
# ---------------------------------------------------------------------------


class TestValidateOsiRobustness:
    def test_malformed_datasets_does_not_raise(self) -> None:
        # datasets is a string, not a list — must return a result, not raise.
        r = conv.validate_osi(
            {"version": "0.1.1", "semantic_model": [{"name": "x", "datasets": "not-an-array"}]}
        )
        assert r is not None
        # No garbage semantic errors from iterating a string char-by-char.
        assert all("DUPLICATE" not in e for e in r.semantic_errors)

    def test_malformed_fields_does_not_raise(self) -> None:
        r = conv.validate_osi(
            {
                "version": "0.2.0.dev0",
                "semantic_model": [{"name": "x", "datasets": [{"name": "D", "fields": "nope"}]}],
            }
        )
        assert r is not None

    def test_still_flags_duplicate_datasets(self) -> None:
        # The robustness guards must not suppress real semantic errors.
        r = conv.validate_osi(
            {
                "version": "0.2.0.dev0",
                "semantic_model": [
                    {
                        "name": "m",
                        "datasets": [
                            {"name": "D", "source": "a.b.d", "fields": []},
                            {"name": "D", "source": "a.b.d2", "fields": []},
                        ],
                    }
                ],
            }
        )
        assert any("DUPLICATE_DATASET" in e for e in r.semantic_errors)
