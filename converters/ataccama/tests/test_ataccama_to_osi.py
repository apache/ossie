"""Tests for the Ataccama -> OSI conversion (offline, using a recorded fixture)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from ataccama_osi.ataccama_to_osi import (
    OSI_VERSION,
    attribute_to_field,
    ataccama_to_osi,
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


def _find_dataset(document: dict, name: str) -> dict:
    return next(d for d in document["semantic_model"][0]["datasets"] if d["name"] == name)


def test_document_is_schema_valid(document: dict) -> None:
    schema = json.loads(SCHEMA.read_text())
    errors = list(Draft202012Validator(schema).iter_errors(document))
    assert not errors, [e.message for e in errors]
    assert document["version"] == OSI_VERSION


def test_model_structure(document: dict) -> None:
    model = document["semantic_model"][0]
    assert model["name"] == "test_model"
    assert len(model["datasets"]) == 2
    # tenant preserved at model level
    assert any(e["vendor_name"] == "ATACCAMA" for e in model["custom_extensions"])


def test_attribute_becomes_quoted_ansi_field(document: dict) -> None:
    ds = _find_dataset(document, "Financial KPIs")
    assert len(ds["fields"]) == 25
    field = ds["fields"][0]
    assert field["name"] == "Prior Yr. SM"
    dialects = field["expression"]["dialects"]
    assert dialects == [{"dialect": "ANSI_SQL", "expression": '"Prior Yr. SM"'}]
    # attribute urn + typing preserved for round-tripping
    ext = json.loads(field["custom_extensions"][0]["data"])
    assert ext["data_type"] == "STRING"
    assert ext["attribute_urn"].startswith("urn:ata:")


def test_richtext_description_is_flattened(document: dict) -> None:
    ds = _find_dataset(document, "Analysis Year to Date Summary")
    assert ds["description"].startswith("This report provides")
    # no Slate JSON leaked through
    assert "{" not in ds["description"] and "children" not in ds["description"]


def test_assigned_term_becomes_ai_context(document: dict) -> None:
    ds = _find_dataset(document, "Analysis Year to Date Summary")
    assert ds["ai_context"]["synonyms"] == ["Marketing"]
    assert "Marketing" in ds["ai_context"]["instructions"]


def test_governance_metadata_preserved(document: dict) -> None:
    ds = _find_dataset(document, "Analysis Year to Date Summary")
    ext = json.loads(ds["custom_extensions"][0]["data"])
    assert ext["catalog_item_urn"].startswith("urn:ata:")
    assert "connection_urn" in ext and "stewardship_group_urn" in ext


# --- unit tests for helpers ---


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


def test_is_time_inferred_from_datatype() -> None:
    attr = CatalogAttribute(urn="urn:ata:t:catalog:catalog-attribute:1", name="created_at", data_type="DATETIME")
    field = attribute_to_field(attr, terms={}, used=set())
    assert field["dimension"] == {"is_time": True}


def test_non_time_datatype_has_no_dimension() -> None:
    attr = CatalogAttribute(urn="urn:ata:t:catalog:catalog-attribute:1", name="amount", data_type="DOUBLE")
    field = attribute_to_field(attr, terms={}, used=set())
    assert "dimension" not in field


def test_duplicate_dataset_names_are_disambiguated() -> None:
    items = [
        CatalogItemBundle(item=CatalogItem(urn=f"urn:ata:t:catalog:catalog-item:{i}", name="Orders"))
        for i in range(2)
    ]
    doc = ataccama_to_osi(items)
    names = [d["name"] for d in doc["semantic_model"][0]["datasets"]]
    assert names == ["Orders", "Orders_2"]


def test_build_source_uses_reversed_locations_then_name() -> None:
    item = CatalogItem(
        urn="urn:ata:t:catalog:catalog-item:1",
        name="customers",
        locations=[CatalogLocation(name="sales"), CatalogLocation(name="Workspaces")],
    )
    assert build_source(item) == "Workspaces.sales.customers"
