"""Tests for the Ataccama -> OSI conversion (offline, using a recorded fixture).

Fixture items: BANK_TRANSACTIONS (a warehouse table with terms, DATE columns, and
rich DQ results) and aggregation (a derived output with no locations and a 0% DQ case).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from ataccama_osi.ataccama_to_osi import (
    OSI_VERSION,
    _dataset_dq,
    _dataset_dq_warning,
    _quality_summary,
    ataccama_to_osi,
    attribute_to_field,
    build_source,
    flatten_richtext,
)
from ataccama_osi.models import CatalogAttribute, CatalogItem, CatalogItemBundle, CatalogLocation

FIXTURE = Path(__file__).parent / "fixtures" / "ataccama_bundles.json"
SCHEMA = Path(__file__).parents[3] / "core-spec" / "osi-schema.json"


@pytest.fixture
def bundles() -> list[CatalogItemBundle]:
    raw = json.loads(FIXTURE.read_text())
    return [CatalogItemBundle.from_dict(b) for b in raw]


@pytest.fixture
def document(bundles: list[CatalogItemBundle]) -> dict:
    return ataccama_to_osi(bundles, model_name="test_model", tenant="example-tenant")


def _dataset(document: dict, name: str) -> dict:
    return next(d for d in document["semantic_model"][0]["datasets"] if d["name"] == name)


def _field(dataset: dict, name: str) -> dict:
    return next(f for f in dataset["fields"] if f["name"] == name)


def _ext(obj: dict) -> dict:
    return json.loads(obj["custom_extensions"][0]["data"])


# --- structure & core mapping ---


def test_document_is_schema_valid(document: dict) -> None:
    schema = json.loads(SCHEMA.read_text())
    errors = list(Draft202012Validator(schema).iter_errors(document))
    assert not errors, [e.message for e in errors]
    assert document["version"] == OSI_VERSION


def test_model_structure(document: dict) -> None:
    model = document["semantic_model"][0]
    assert model["name"] == "test_model"
    assert {d["name"] for d in model["datasets"]} == {"BANK_TRANSACTIONS", "aggregation"}


def test_attribute_becomes_quoted_ansi_field(document: dict) -> None:
    ds = _dataset(document, "BANK_TRANSACTIONS")
    assert len(ds["fields"]) == 13
    field = _field(ds, "TRANSACTION_ID")
    assert field["expression"]["dialects"] == [{"dialect": "ANSI_SQL", "expression": '"TRANSACTION_ID"'}]
    assert _ext(field)["data_type"] == "STRING"


def test_date_columns_are_time_dimensions(document: dict) -> None:
    ds = _dataset(document, "BANK_TRANSACTIONS")
    assert _field(ds, "BIRTH_DATE")["dimension"] == {"is_time": True}
    assert _field(ds, "TRANSACTION_DATE")["dimension"] == {"is_time": True}
    assert "dimension" not in _field(ds, "ACCOUNT_BALANCE")


def test_source_from_locations_and_limitation(document: dict) -> None:
    # DB-backed item: reversed locations + name form a qualified path
    assert _dataset(document, "BANK_TRANSACTIONS")["source"] == "DEMO_ENV.CLOUD_DEMO.BANK_TRANSACTIONS"
    # derived item with no locations: source degrades to the bare name
    assert _dataset(document, "aggregation")["source"] == "aggregation"


def test_terms_become_ai_context(document: dict) -> None:
    ds = _dataset(document, "BANK_TRANSACTIONS")
    assert set(ds["ai_context"]["synonyms"]) >= {"Personal Data", "Finance"}
    email = _field(ds, "EMAIL")
    assert email["ai_context"]["synonyms"] == ["E-mail"]
    assert "email" in email["ai_context"]["instructions"].lower()


def test_governance_metadata_preserved(document: dict) -> None:
    ext = _ext(_dataset(document, "BANK_TRANSACTIONS"))
    assert ext["catalog_item_urn"].startswith("urn:ata:")
    assert "connection_urn" in ext and "stewardship_group_urn" in ext


# --- data quality enrichment ---


def test_dataset_dq_overall_and_dimensions(document: dict) -> None:
    dq = _ext(_dataset(document, "BANK_TRANSACTIONS"))["dq"]
    assert dq["passed"] == 1784 and dq["failed"] == 472
    assert dq["pass_rate_pct"] == 79.1
    dims = {d["name"]: d for d in dq["dimensions"]}
    assert dims["Completeness"]["pass_rate_pct"] == 98.8
    assert dims["Validity"]["pass_rate_pct"] == 79.1
    # dimensions with no evaluated records (Uniqueness/Accuracy = 0/0) are dropped
    assert "Uniqueness" not in dims and "Accuracy" not in dims
    assert dq["results_link"].startswith("https://")
    # active-finding count is always reported (0 = no open issues)
    assert dq["active_findings"] == 0


def test_dataset_dq_threshold(document: dict) -> None:
    # BANK_TRANSACTIONS has a configured overall bar of 75%; 79.1% is above it.
    dq = _ext(_dataset(document, "BANK_TRANSACTIONS"))["dq"]
    assert dq["threshold_pct"] == 75
    assert dq["below_threshold"] is False


def test_no_threshold_when_monitor_has_none(document: dict) -> None:
    # aggregation's monitor has no overall threshold configured.
    dq = _ext(_dataset(document, "aggregation"))["dq"]
    assert "threshold_pct" not in dq and "below_threshold" not in dq


def test_field_level_dq(document: dict) -> None:
    ds = _dataset(document, "BANK_TRANSACTIONS")
    dq = _ext(_field(ds, "TRANSACTION_ID"))["dq"]
    assert dq == {"passed": 2251, "failed": 5, "pass_rate_pct": 99.8}


def test_zero_pass_rate_case(document: dict) -> None:
    dq = _ext(_dataset(document, "aggregation"))["dq"]
    assert dq["passed"] == 0 and dq["failed"] == 4
    assert dq["pass_rate_pct"] == 0.0


def test_no_dq_keys_when_dq_absent(bundles: list[CatalogItemBundle]) -> None:
    b = bundles[0]
    b.dq_results = None  # simulate --no-dq / item without a monitor
    ds = ataccama_to_osi([b])["semantic_model"][0]["datasets"][0]
    assert "dq" not in _ext(ds)
    assert all("dq" not in _ext(f) for f in ds["fields"])


# --- opt-in DQ AI warnings ---


def test_ai_warnings_off_by_default(document: dict) -> None:
    # Default conversion must not inject warnings into ai_context.
    ds = _dataset(document, "BANK_TRANSACTIONS")
    assert "Data-quality warning" not in (ds.get("ai_context", {}).get("instructions") or "")
    email = _field(ds, "EMAIL")
    assert "Data-quality warning" not in email["ai_context"]["instructions"]


def test_ai_warning_appended_when_below_ataccama_threshold() -> None:
    # A dataset Ataccama flags as below its own configured threshold gets a warning.
    bundle = CatalogItemBundle(
        item=CatalogItem(urn="urn:ata:t:catalog:catalog-item:1", name="risky"),
        dq_results={"overallQuality": {"passedCount": 50, "failedCount": 50}, "overallDqFindings": []},
        dq_threshold_pct=75,
    )
    ds = ataccama_to_osi([bundle], dq_ai_warnings=True)["semantic_model"][0]["datasets"][0]
    instr = ds["ai_context"]["instructions"]
    assert "Data-quality warning" in instr and "configured 75% quality threshold" in instr


def test_no_ai_warning_without_ataccama_signal(bundles: list[CatalogItemBundle]) -> None:
    # BANK_TRANSACTIONS is above its 75% bar with no active findings; aggregation has no
    # threshold configured. Neither should be flagged — Ataccama is the source of truth.
    doc = ataccama_to_osi(bundles, dq_ai_warnings=True)
    bank = _dataset(doc, "BANK_TRANSACTIONS")["ai_context"]["instructions"]
    assert "Data-quality warning" not in bank
    agg = (_dataset(doc, "aggregation").get("ai_context") or {}).get("instructions", "")
    assert "Data-quality warning" not in agg


def test_dataset_dq_warning_triggers() -> None:
    # below Ataccama's own configured threshold
    below = _dataset_dq_warning({"pass_rate_pct": 60.0, "threshold_pct": 75, "below_threshold": True})
    assert below and "configured 75% quality threshold" in below
    # passing, no active findings -> no warning (no converter-side fallback exists)
    assert _dataset_dq_warning({"pass_rate_pct": 99.0, "active_findings": 0}) is None
    # a dataset with no configured threshold is never flagged on pass rate alone
    assert _dataset_dq_warning({"pass_rate_pct": 10.0}) is None
    # active findings alone
    af = _dataset_dq_warning({"pass_rate_pct": 99.0, "active_findings": 2})
    assert af and "2 active data-quality finding(s)" in af


# --- helper unit tests ---


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("plain text", "plain text"),
        ('[{"type":"paragraph","children":[{"text":"hi"}]}]', "hi"),
        ([{"type": "paragraph", "children": [{"text": "a "}, {"text": "b"}]}], "a b"),
    ],
)
def test_flatten_richtext(value, expected) -> None:
    assert flatten_richtext(value) == expected


@pytest.mark.parametrize(
    "oq,expected",
    [
        (None, None),
        ({"passedCount": 3, "failedCount": 1}, {"passed": 3, "failed": 1, "pass_rate_pct": 75.0}),
        ({"passedCount": 0, "failedCount": 0}, {"passed": 0, "failed": 0}),  # not evaluated -> no rate
    ],
)
def test_quality_summary(oq, expected) -> None:
    assert _quality_summary(oq) == expected


def test_below_threshold_flag_true() -> None:
    dq = _dataset_dq({"overallQuality": {"passedCount": 60, "failedCount": 40}}, threshold_pct=75)
    assert dq["pass_rate_pct"] == 60.0
    assert dq["threshold_pct"] == 75
    assert dq["below_threshold"] is True


def test_is_time_inferred_from_datatype() -> None:
    attr = CatalogAttribute(urn="urn:ata:t:catalog:catalog-attribute:1", name="created_at", data_type="DATETIME")
    assert attribute_to_field(attr, terms={}, used=set())["dimension"] == {"is_time": True}


def test_duplicate_dataset_names_are_disambiguated() -> None:
    items = [
        CatalogItemBundle(item=CatalogItem(urn=f"urn:ata:t:catalog:catalog-item:{i}", name="Orders"))
        for i in range(2)
    ]
    names = [d["name"] for d in ataccama_to_osi(items)["semantic_model"][0]["datasets"]]
    assert names == ["Orders", "Orders_2"]


def test_build_source_uses_reversed_locations_then_name() -> None:
    item = CatalogItem(
        urn="urn:ata:t:catalog:catalog-item:1",
        name="customers",
        locations=[CatalogLocation(name="sales"), CatalogLocation(name="Workspaces")],
    )
    assert build_source(item) == "Workspaces.sales.customers"
