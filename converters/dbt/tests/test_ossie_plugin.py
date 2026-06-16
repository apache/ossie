"""Tests for the OSSIE I/O protocol adapter (ossie_plugin.py).

Each test mocks sys.stdin/sys.stdout/sys.argv and calls main() directly,
asserting the JSON response matches the expected shape.
"""
import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from osi_dbt.ossie_plugin import from_osi, to_osi
from tests.helpers import (
    _dimension,
    _entity,
    _manifest,
    _measure,
    _osi_doc,
    _osi_dataset,
    _osi_field,
    _simple_metric,
)
from metricflow_semantic_interfaces.implementations.semantic_model import (
    PydanticNodeRelation,
    PydanticSemanticModel,
)
from metricflow_semantic_interfaces.type_enums import DimensionType, EntityType
from metricflow_semantic_interfaces.test_utils import default_meta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_manifest_json() -> str:
    """Serialize a minimal but valid PydanticSemanticManifest to JSON."""
    entity = _entity("order_id", EntityType.PRIMARY)
    measure = _measure("order_count", expr="1")
    dimension = _dimension("is_cancelled", DimensionType.CATEGORICAL)
    sm = PydanticSemanticModel(
        name="orders",
        node_relation=PydanticNodeRelation(alias="orders", schema_name="public", database="analytics"),
        description="Order records",
        entities=[entity],
        measures=[measure],
        dimensions=[dimension],
        metadata=default_meta(),
        config=None,
    )
    metric = _simple_metric("order_count", "order_count")
    manifest = _manifest(semantic_models=[sm], metrics=[metric])
    # PydanticSemanticManifest uses pydantic v1 compat (.json(), not .model_dump_json()).
    return manifest.json(by_alias=True, exclude_none=True)


def _minimal_osi_yaml() -> str:
    """Serialize a minimal OSIDocument to YAML."""
    doc = _osi_doc(
        datasets=[_osi_dataset("orders", fields=[_osi_field("order_id")])],
        model_name="test",
    )
    return doc.to_osi_yaml()


# ---------------------------------------------------------------------------
# to_osi tests
# ---------------------------------------------------------------------------


def test_to_osi_no_json_file_returns_error_issue():
    response = to_osi({"model.yaml": "version: 1"})
    assert response["files"] == {}
    assert len(response["issues"]) == 1
    assert response["issues"][0]["severity"] == "error"


def test_to_osi_real_manifest_produces_osi_yaml():
    manifest_json = _minimal_manifest_json()
    response = to_osi({"semantic_manifest.json": manifest_json})

    assert response["files"], "expected output files"
    assert "semantic_model.yaml" in response["files"]

    osi_yaml = response["files"]["semantic_model.yaml"]
    assert "semantic_model" in osi_yaml, "expected OSI YAML with semantic_model key"


def test_to_osi_response_has_no_errors_for_simple_manifest():
    manifest_json = _minimal_manifest_json()
    response = to_osi({"semantic_manifest.json": manifest_json})

    error_issues = [i for i in response["issues"] if i["severity"] == "error"]
    assert error_issues == [], f"unexpected errors: {error_issues}"


# ---------------------------------------------------------------------------
# from_osi tests
# ---------------------------------------------------------------------------


def test_from_osi_no_yaml_file_returns_error_issue():
    response = from_osi({"manifest.json": "{}"})
    assert response["files"] == {}
    assert len(response["issues"]) == 1
    assert response["issues"][0]["severity"] == "error"


def test_from_osi_real_osi_yaml_produces_manifest():
    osi_yaml = _minimal_osi_yaml()
    response = from_osi({"model.yaml": osi_yaml})

    assert response["files"], "expected output files"
    assert "semantic_manifest.json" in response["files"]

    manifest = json.loads(response["files"]["semantic_manifest.json"])
    assert "semantic_models" in manifest, "expected semantic_models in output manifest"
