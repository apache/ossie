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

import pytest

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
# #220: OBML dimension name restored across the round-trip
# ---------------------------------------------------------------------------


class TestDimensionNameRoundTrip:
    """An OBML-origin round-trip restores each dimension's name (the OSI field
    name is the physical code, so without the ``obml_dimension_name`` extension
    the dimension would be renamed to its code) and never trips the collision
    fallback, since the restored names are unique by construction."""

    _OBML = {
        "version": 1.0,
        "dataObjects": {
            "Orders": {
                "code": "FACT_ORDERS",
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {
                    "Order Date": {"code": "order_dt", "abstractType": "date"},
                    "Region": {"code": "rgn", "abstractType": "string"},
                    "Amount": {"code": "amt", "abstractType": "float"},
                },
            },
            "Invoices": {
                "code": "FACT_INV",
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {"Invoice Date": {"code": "inv_dt", "abstractType": "date"}},
            },
        },
        # Names deliberately differ from their column codes; both date dims would
        # collide on the code path if names were not restored.
        "dimensions": {
            "Order Placed On": {
                "dataObject": "Orders",
                "column": "Order Date",
                "resultType": "date",
            },
            "Sales Region": {"dataObject": "Orders", "column": "Region", "resultType": "string"},
            "Invoice Raised On": {
                "dataObject": "Invoices",
                "column": "Invoice Date",
                "resultType": "date",
            },
        },
        "measures": {
            "Revenue": {
                "columns": [{"dataObject": "Orders", "column": "Amount"}],
                "aggregation": "sum",
                "resultType": "float",
            }
        },
    }

    def _roundtrip(self) -> tuple[dict[str, Any], list[str]]:
        osi = conv.OBMLtoOSI(self._OBML, model_name="sales").convert()
        c = conv.OSItoOBML(osi)
        return c.convert(), c.warnings

    def test_names_restored_not_renamed_to_code(self) -> None:
        obml, _ = self._roundtrip()
        assert set(obml["dimensions"]) == {
            "Order Placed On",
            "Sales Region",
            "Invoice Raised On",
        }

    def test_no_collision_fallback_for_obml_origin(self) -> None:
        _, warnings = self._roundtrip()
        assert not any("collision" in w.lower() for w in warnings), warnings

    def test_restored_name_not_left_as_a_synonym(self) -> None:
        obml, _ = self._roundtrip()
        # The dimension must not carry its own restored name as a synonym.
        for name, dim in obml["dimensions"].items():
            assert name not in dim.get("synonyms", [])

    def test_primary_dimension_synonyms_and_extensions_preserved(self) -> None:
        # The primary (single) dimension on a column keeps its own synonyms and
        # vendor extensions across the round-trip, not just the scalar props.
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "o",
                    "database": "W",
                    "schema": "P",
                    "columns": {"Status": {"code": "status", "abstractType": "string"}},
                }
            },
            "dimensions": {
                "Status": {
                    "dataObject": "Orders",
                    "column": "Status",
                    "synonyms": ["state", "condition"],
                    "customExtensions": [{"vendor": "TABLEAU", "data": '{"role": "dim"}'}],
                }
            },
        }
        back = conv.OSItoOBML(conv.OBMLtoOSI(obml).convert()).convert()
        dim = back["dimensions"]["Status"]
        assert dim.get("synonyms") == ["state", "condition"]
        assert dim.get("customExtensions") == [{"vendor": "TABLEAU", "data": '{"role": "dim"}'}]

    @pytest.mark.parametrize("bad", [["x"], 5, "", {}, None])
    def test_non_string_restored_name_is_ignored(self, bad: Any) -> None:
        # obml_dimension_name is opaque to validate_osi, so a foreign payload may
        # put any JSON there. A non-string (would be an unhashable dict key) must
        # be ignored and fall back to the field name, never crash the converter.
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "s",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "WH.PUBLIC.orders",
                            "fields": [
                                {
                                    "name": "dt",
                                    "expression": {
                                        "dialects": [{"dialect": "ANSI_SQL", "expression": "dt"}]
                                    },
                                    "dimension": {},
                                    "custom_extensions": [
                                        {
                                            "vendor_name": "ORIONBELT",
                                            "data": json.dumps({"obml_dimension_name": bad}),
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        obml = conv.OSItoOBML(osi).convert()
        assert list(obml["dimensions"]) == ["dt"]


# ---------------------------------------------------------------------------
# #222: N OBML dimensions over one column
# ---------------------------------------------------------------------------


class TestMultipleDimensionsPerColumn:
    """OBML allows N dimensions over one column (grain variants, role-playing);
    OSI is one-dimension-per-field. The export preserves the extras via an
    extension and warns (not silent); the import rebuilds them all (#222)."""

    _OBML = {
        "version": 1.0,
        "dataObjects": {
            "Orders": {
                "code": "FACT",
                "database": "WH",
                "schema": "PUBLIC",
                "columns": {
                    "date": {"code": "order_date", "abstractType": "date"},
                    "Amount": {"code": "amt", "abstractType": "float"},
                },
            }
        },
        "dimensions": {
            "Order Day": {
                "dataObject": "Orders",
                "column": "date",
                "resultType": "date",
                "timeGrain": "day",
            },
            "Order Month": {
                "dataObject": "Orders",
                "column": "date",
                "resultType": "date",
                "timeGrain": "month",
            },
            "Order Quarter": {
                "dataObject": "Orders",
                "column": "date",
                "resultType": "date",
                "timeGrain": "quarter",
            },
        },
        "measures": {
            "Revenue": {
                "columns": [{"dataObject": "Orders", "column": "Amount"}],
                "aggregation": "sum",
                "resultType": "float",
            }
        },
    }

    def _roundtrip(self) -> tuple[dict[str, Any], list[str]]:
        exp = conv.OBMLtoOSI(self._OBML, model_name="s")
        osi = exp.convert()
        return conv.OSItoOBML(osi).convert(), exp.warnings

    def test_all_dimensions_survive_with_distinct_grains(self) -> None:
        obml, _ = self._roundtrip()
        dims = obml["dimensions"]
        assert set(dims) == {"Order Day", "Order Month", "Order Quarter"}
        assert {d["timeGrain"] for d in dims.values()} == {"day", "month", "quarter"}
        # All three still point at the same physical column.
        assert {d["column"] for d in dims.values()} == {"order_date"}

    def test_export_warns_not_silent(self) -> None:
        _, warnings = self._roundtrip()
        assert any("backs 3 OBML dimensions" in w for w in warnings), warnings

    def test_extra_dimension_synonyms_and_extensions_preserved(self) -> None:
        # An extra dimension's own synonyms and vendor extensions must survive,
        # matching the primary's fidelity (not just the scalar props).
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "F",
                    "database": "W",
                    "schema": "P",
                    "columns": {"date": {"code": "dt", "abstractType": "date"}},
                }
            },
            "dimensions": {
                "Order Day": {
                    "dataObject": "Orders",
                    "column": "date",
                    "resultType": "date",
                    "timeGrain": "day",
                },
                "Order Month": {
                    "dataObject": "Orders",
                    "column": "date",
                    "resultType": "date",
                    "timeGrain": "month",
                    "synonyms": ["monthly date"],
                    "customExtensions": [{"vendor": "ACME", "data": '{"y": 2}'}],
                },
            },
        }
        back = conv.OSItoOBML(conv.OBMLtoOSI(obml, model_name="s").convert()).convert()
        month = back["dimensions"]["Order Month"]
        assert month.get("synonyms") == ["monthly date"]
        assert month.get("customExtensions") == [{"vendor": "ACME", "data": '{"y": 2}'}]

    @pytest.mark.parametrize(
        "bad",
        [
            "not-a-list",
            [5],
            [{"no_name": 1}],
            [{"name": ["x"]}],
            [{"name": ""}],
            [{"name": "OK"}, 7],
            [{"name": "OK", "synonyms": "not-a-list"}],
            [{"name": "OK", "synonyms": [1, {}]}],
            [{"name": "OK", "customExtensions": "bad"}],
            [{"name": "OK", "customExtensions": [7]}],
        ],
    )
    def test_malformed_extra_dimensions_never_crash(self, bad: Any) -> None:
        # obml_extra_dimensions is opaque to validate_osi. A malformed payload
        # (not a list, or items that are not dicts / lack a string name) must be
        # skipped, never crash the converter on an unhashable key.
        osi = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "s",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "WH.PUBLIC.orders",
                            "fields": [
                                {
                                    "name": "dt",
                                    "expression": {
                                        "dialects": [{"dialect": "ANSI_SQL", "expression": "dt"}]
                                    },
                                    "dimension": {},
                                    "custom_extensions": [
                                        {
                                            "vendor_name": "ORIONBELT",
                                            "data": json.dumps({"obml_extra_dimensions": bad}),
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        obml = conv.OSItoOBML(osi).convert()
        # The primary field-name dimension is always present; any well-formed
        # extra (the {"name": "OK"} case) is rebuilt, malformed items skipped.
        assert "dt" in obml["dimensions"]
        assert all(isinstance(k, str) for k in obml["dimensions"])


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
