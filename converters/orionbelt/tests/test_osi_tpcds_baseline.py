"""OSI TPC-DS baseline (the converter-contributing step-5 requirement).

Exercises the conceptual conversion flow from the OSI converters guide against
the canonical ``examples/tpcds_semantic_model.yaml`` from the OSI repository
(vendored under ``fixtures/tpcds_semantic_model.yaml``, Apache-2.0):

    https://github.com/open-semantic-interchange/OSI/blob/main/converters/index.md#example-conceptual-conversion-flow

The canonical example carries ``SALESFORCE`` and ``DBT`` custom_extensions, so
this also pins step 7 of that flow: third-party vendor extensions are preserved
when the model round-trips back to OSI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ossie_orionbelt.converter as conv

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tpcds_semantic_model.yaml"


@pytest.fixture(scope="module")
def canonical_osi() -> dict[str, Any]:
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


def test_canonical_example_is_valid_osi(canonical_osi: dict[str, Any]) -> None:
    vr = conv.validate_osi(canonical_osi)
    assert not vr.schema_errors, vr.schema_errors


def test_conceptual_flow_roundtrips_and_validates(canonical_osi: dict[str, Any]) -> None:
    # OSI -> OBML
    obml = conv.OSItoOBML(canonical_osi).convert()
    assert not conv.validate_obml(obml).schema_errors

    # OBML -> OSI
    osi_out = conv.OBMLtoOSI(obml, "tpcds_retail_model").convert()
    assert not conv.validate_osi(osi_out).schema_errors


def test_foreign_extensions_preserved_through_roundtrip(canonical_osi: dict[str, Any]) -> None:
    # Step 7: SALESFORCE / DBT extensions are not applied to OBML but must
    # survive a round-trip back to OSI.
    assert {"SALESFORCE", "DBT"} <= _vendor_names(canonical_osi)

    obml = conv.OSItoOBML(canonical_osi).convert()
    osi_out = conv.OBMLtoOSI(obml, "tpcds_retail_model").convert()

    assert {"SALESFORCE", "DBT"} <= _vendor_names(osi_out)
