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

"""Ossie TPC-DS baseline (the converter-contributing step-5 requirement).

Exercises the conceptual conversion flow from the Ossie converters guide against
the canonical ``examples/tpcds_semantic_model.yaml`` from the Ossie repository
(vendored under ``fixtures/tpcds_semantic_model.yaml``, Apache-2.0):

    https://github.com/apache/ossie/blob/main/converters/README.md#example-conceptual-conversion-flow

The canonical example carries ``SALESFORCE`` and ``DBT`` custom_extensions, so
this also pins step 7 of that flow: third-party vendor extensions are preserved
when the model round-trips back to Ossie.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ossie_orionbelt.converter as conv

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tpcds_semantic_model.yaml"


@pytest.fixture(scope="module")
def canonical_ossie() -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(_FIXTURE.read_text())  # type: ignore[no-any-return]


def _vendor_names(doc: Any) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("vendor_name"):
                found.add(node["vendor_name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return found


def test_canonical_example_is_valid_ossie(canonical_ossie: dict[str, Any]) -> None:
    vr = conv.validate_ossie(canonical_ossie)
    assert not vr.schema_errors, vr.schema_errors


def test_conceptual_flow_roundtrips_and_validates(canonical_ossie: dict[str, Any]) -> None:
    # Ossie -> OBML
    obml = conv.OssietoOBML(canonical_ossie).convert()
    assert not conv.validate_obml(obml).schema_errors

    # OBML -> Ossie
    ossie_out = conv.OBMLtoOssie(obml, "tpcds_retail_model").convert()
    assert not conv.validate_ossie(ossie_out).schema_errors


def test_foreign_extensions_preserved_through_roundtrip(canonical_ossie: dict[str, Any]) -> None:
    # Step 7: SALESFORCE / DBT extensions are not applied to OBML but must
    # survive a round-trip back to Ossie.
    assert {"SALESFORCE", "DBT"} <= _vendor_names(canonical_ossie)

    obml = conv.OssietoOBML(canonical_ossie).convert()
    ossie_out = conv.OBMLtoOssie(obml, "tpcds_retail_model").convert()

    assert {"SALESFORCE", "DBT"} <= _vendor_names(ossie_out)
