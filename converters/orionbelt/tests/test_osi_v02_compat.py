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

"""OSI v0.2 compatibility tests.

Covers the v2.6 spec bump from OSI v0.1.1 → v0.2.0.dev0:

- Emitted ``version`` is the v0.2 constant
- Top-level ``dialects`` / ``vendors`` informational arrays are present
- Dataset ``primary_key`` is promoted from per-column ``primaryKey: true``
- Dataset ``unique_keys`` round-trips lossly via OBSL custom_extensions
- Field ``label`` round-trips via OBSL custom_extensions
- Legacy v0.1.1 inputs are normalized in place by the shim
- Every emitted document validates against the resolved v0.2 core schema
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import ossie_orionbelt.converter as conv

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema_validator() -> Any:
    """Draft 2020-12 validator pinned to the resolved OSI v0.2 core schema
    (vendored copy if present, otherwise the ossie ``core-spec/`` copy)."""
    jsonschema = pytest.importorskip("jsonschema")
    with open(conv._OSI_SCHEMA_PATH) as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


def _collect_vendor_names(osi: dict[str, Any]) -> set[str]:
    """Every ``custom_extensions[].vendor_name`` anywhere in an OSI document."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for ext in node.get("custom_extensions", []) or []:
                if isinstance(ext, dict) and ext.get("vendor_name"):
                    found.add(ext["vendor_name"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(osi)
    return found


_OBML_WITH_PK_AND_LABEL: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "orders",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Order ID": {
                    "code": "order_id",
                    "abstractType": "string",
                    "primaryKey": True,
                    "customExtensions": [
                        {
                            "vendor": "OBSL",
                            "data": json.dumps({"obml_field_label": "filter"}),
                        }
                    ],
                },
                "Line Number": {
                    "code": "line_no",
                    "abstractType": "int",
                    "primaryKey": True,
                },
                "Amount": {"code": "amount", "abstractType": "float"},
            },
            "customExtensions": [
                {
                    "vendor": "OBSL",
                    "data": json.dumps(
                        {"obml_unique_keys": [["order_id"], ["order_id", "line_no"]]}
                    ),
                }
            ],
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


_OSI_V01_INPUT: dict[str, Any] = {
    "version": "0.1.1",
    "semantic_model": [
        {
            "name": "ecommerce",
            "datasets": [
                {
                    "name": "Orders",
                    "source": "WAREHOUSE.PUBLIC.orders",
                    # Legacy: PK stashed in custom_extensions (pre-v0.2 shape)
                    "custom_extensions": [
                        {
                            "vendor_name": "COMMON",
                            "data": json.dumps(
                                {
                                    "obml_primary_key": ["order_id"],
                                    "obml_unique_keys": [["order_id"], ["order_number"]],
                                }
                            ),
                        }
                    ],
                    "fields": [
                        {
                            "name": "order_id",
                            "expression": {
                                "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                            },
                            "data_type": "string",
                        },
                        {
                            "name": "amount",
                            "expression": {
                                "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]
                            },
                            "data_type": "number",
                        },
                    ],
                }
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Version + top-level shape
# ---------------------------------------------------------------------------


class TestEmittedVersion:
    def test_top_level_version_is_v02(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        assert osi["version"] == conv._OSI_VERSION
        assert osi["version"].startswith("0.2")

    def test_no_root_dialects_or_vendors(self) -> None:
        # The published OSI core schema forbids root-level dialects/vendors
        # (root is additionalProperties:false). See OSI PR #148.
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        assert "dialects" not in osi
        assert "vendors" not in osi
        assert set(osi.keys()) <= {"version", "semantic_model"}

    def test_dialect_tagged_per_expression(self) -> None:
        # Dialects live on each expression, the schema-valid home.
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        for metric in osi["semantic_model"][0].get("metrics", []):
            tags = [d["dialect"] for d in metric["expression"]["dialects"]]
            assert "ANSI_SQL" in tags

    def test_vendor_tagged_per_entity_not_root(self) -> None:
        # ORIONBELT tags our roundtrip metadata in per-entity custom_extensions,
        # not a root array. The OBML field label round-trips as a *native* OSI
        # field label (no vendor tag needed).
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        assert "ORIONBELT" in _collect_vendor_names(osi)
        fields = osi["semantic_model"][0]["datasets"][0]["fields"]
        assert any(f.get("label") for f in fields), "expected a native OSI field label"


# ---------------------------------------------------------------------------
# primary_key + unique_keys (first-class in v0.2)
# ---------------------------------------------------------------------------


class TestPrimaryKey:
    def test_composite_pk_emitted_in_declaration_order(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        ds = osi["semantic_model"][0]["datasets"][0]
        # Two columns flagged primaryKey: emit composite in declaration order
        assert ds["primary_key"] == ["order_id", "line_no"]

    def test_pk_roundtrip_restores_per_column_flag(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        obml = conv.OSItoOBML(osi).convert()
        # OSI fields surface as OBML columns keyed by physical code
        # (display names are an OBML-side concept).
        cols = obml["dataObjects"]["Orders"]["columns"]
        assert cols["order_id"].get("primaryKey") is True
        assert cols["line_no"].get("primaryKey") is True
        assert cols["amount"].get("primaryKey") is None or not cols["amount"]["primaryKey"]

    def test_unknown_pk_column_emits_warning(self) -> None:
        bad = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "x",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "a.b.c",
                            "primary_key": ["no_such_column"],
                            "fields": [
                                {
                                    "name": "amount",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "amount"}
                                        ]
                                    },
                                    "data_type": "number",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        converter = conv.OSItoOBML(bad)
        converter.convert()
        joined = " ".join(converter.warnings)
        assert "no_such_column" in joined


class TestUniqueKeys:
    def test_unique_keys_roundtrip(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        ds = osi["semantic_model"][0]["datasets"][0]
        assert ds.get("unique_keys") == [["order_id"], ["order_id", "line_no"]]


# ---------------------------------------------------------------------------
# Field label
# ---------------------------------------------------------------------------


class TestFieldLabel:
    def test_label_emitted_from_custom_extensions(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        fields = osi["semantic_model"][0]["datasets"][0]["fields"]
        order_id_field = next(f for f in fields if f["name"] == "order_id")
        assert order_id_field["label"] == "filter"

    def test_label_roundtrip(self) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        obml = conv.OSItoOBML(osi).convert()
        # OSI label round-trips back into OBSL customExtensions
        col = obml["dataObjects"]["Orders"]["columns"]["order_id"]
        exts = col.get("customExtensions", [])
        # OSI label round-trips back into an OSI-vendor customExtension
        assert any(
            e.get("vendor") == "OSI"
            and json.loads(e.get("data", "{}")).get("obml_field_label") == "filter"
            for e in exts
        )


# ---------------------------------------------------------------------------
# Legacy v0.1.x shim
# ---------------------------------------------------------------------------


class TestLegacyShim:
    def test_v01_input_promotes_primary_key(self) -> None:
        # The shim mutates self.osi in place — capture the dataset shape
        # before parsing finishes by snapshotting after _normalize_legacy_v01.
        converter = conv.OSItoOBML(_OSI_V01_INPUT.copy())
        # Manually invoke the shim
        converter.osi = json.loads(json.dumps(_OSI_V01_INPUT))  # deep copy
        converter._normalize_legacy_v01()
        ds = converter.osi["semantic_model"][0]["datasets"][0]
        assert ds["primary_key"] == ["order_id"]
        assert ds["unique_keys"] == [["order_id"], ["order_number"]]

    def test_v01_input_warns(self) -> None:
        converter = conv.OSItoOBML(json.loads(json.dumps(_OSI_V01_INPUT)))
        converter.convert()
        joined = " ".join(converter.warnings)
        assert "0.1.1" in joined and "shim" in joined.lower()

    def test_v02_input_not_normalized(self) -> None:
        # v0.2 input declaring primary_key directly — shim must be a no-op
        v02 = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "x",
                    "datasets": [
                        {
                            "name": "Orders",
                            "source": "a.b.c",
                            "primary_key": ["order_id"],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                    "data_type": "string",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        converter = conv.OSItoOBML(v02)
        converter.convert()
        # No shim warning for v0.2 inputs
        shim_warnings = [w for w in converter.warnings if "shim" in w.lower()]
        assert shim_warnings == []


# ---------------------------------------------------------------------------
# Schema validation (every emitted doc must validate against v0.2)
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_minimal_obml_passes_v02_validation(self, schema_validator: Any) -> None:
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        errors = list(schema_validator.iter_errors(osi))
        assert errors == [], [e.message for e in errors[:5]]

    def test_v01_via_shim_revalidates_clean(self, schema_validator: Any) -> None:
        obml = conv.OSItoOBML(json.loads(json.dumps(_OSI_V01_INPUT))).convert()
        osi = conv.OBMLtoOSI(obml).convert()
        errors = list(schema_validator.iter_errors(osi))
        assert errors == [], [e.message for e in errors[:5]]

    def test_tpcds_fixture_passes_v02_validation(self, schema_validator: Any) -> None:
        """Real-world TPC-DS OBML model must emit v0.2-clean OSI."""
        yaml = pytest.importorskip("yaml")
        fixture = Path(__file__).resolve().parent / "fixtures" / "tpcds_as_obml.yaml"
        with open(fixture) as f:
            obml = yaml.safe_load(f)
        osi = conv.OBMLtoOSI(obml).convert()
        errors = list(schema_validator.iter_errors(osi))
        assert errors == [], [e.message for e in errors[:5]]

    def test_schema_rejects_root_dialects_and_vendors(self, schema_validator: Any) -> None:
        """Guard OSI PR #148: root-level dialects/vendors are non-conformant."""
        osi = conv.OBMLtoOSI(_OBML_WITH_PK_AND_LABEL).convert()
        osi["dialects"] = ["ANSI_SQL"]
        osi["vendors"] = ["ORIONBELT"]
        messages = [e.message for e in schema_validator.iter_errors(osi)]
        assert any("dialects" in m for m in messages), messages
        assert any("vendors" in m for m in messages), messages


# ---------------------------------------------------------------------------
# MAQL dialect passthrough
# ---------------------------------------------------------------------------


class TestMAQLDialect:
    def test_maql_only_metric_does_not_raise(self) -> None:
        osi_in: dict[str, Any] = {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "gd",
                    "datasets": [
                        {
                            "name": "Sales",
                            "source": "a.b.sales",
                            "fields": [
                                {
                                    "name": "amount",
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
                            "name": "total_revenue_maql",
                            "expression": {
                                "dialects": [
                                    {"dialect": "MAQL", "expression": "SELECT SUM(amount)"},
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        # The MAQL dialect is OBSL-unparseable but must not raise; it
        # surfaces as a warning that the MAQL expression couldn't be
        # decomposed.
        converter = conv.OSItoOBML(osi_in)
        converter.convert()
        # No crash is the contract; warnings are acceptable.
