"""Regression guard: converter output must validate against the core OSI schema."""

import json
import sys
from pathlib import Path

import pytest
import yaml

from osi import OSIDialect
from osi_dbt.msi_to_osi import MSIToOSIConverter

from metricflow_semantic_interfaces.implementations.semantic_model import PydanticNodeRelation
from metricflow_semantic_interfaces.test_utils import semantic_model_with_guaranteed_meta
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    EntityType,
    TimeGranularity,
)

from tests.helpers import _dimension, _entity, _manifest, _measure, _simple_metric

REPO_ROOT = Path(__file__).resolve().parents[3]
# Reuse the repository's canonical validator instead of reimplementing schema checks.
sys.path.insert(0, str(REPO_ROOT / "validation"))
from validate import validate_schema  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "core-spec" / "osi-schema.json"


def _representative_manifest():
    """A manifest that exercises datasets, keys, dimensions, measures, relationships
    and metrics, so the emitted document covers most of the core schema."""
    orders = semantic_model_with_guaranteed_meta(
        name="orders",
        description="Order facts",
        node_relation=PydanticNodeRelation(schema_name="analytics", alias="orders"),
        entities=[
            _entity("order_id", entity_type=EntityType.PRIMARY),
            _entity("customer", entity_type=EntityType.FOREIGN, expr="customer_id"),
        ],
        dimensions=[
            _dimension("ds", dim_type=DimensionType.TIME, granularity=TimeGranularity.DAY),
            _dimension("status", description="Order status", label="Status"),
        ],
        measures=[
            _measure("revenue", agg=AggregationType.SUM, expr="amount"),
            _measure("order_count", agg=AggregationType.COUNT, expr="order_id"),
        ],
    )
    customers = semantic_model_with_guaranteed_meta(
        name="customers",
        description="Customer dimension",
        node_relation=PydanticNodeRelation(schema_name="analytics", alias="customers"),
        entities=[
            _entity("customer", entity_type=EntityType.PRIMARY, expr="customer_id"),
            _entity("email", entity_type=EntityType.UNIQUE),
        ],
        dimensions=[_dimension("country")],
    )
    return _manifest(
        semantic_models=[orders, customers],
        metrics=[_simple_metric("revenue", "revenue"), _simple_metric("order_count", "order_count")],
    )


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@pytest.mark.parametrize("dialect", [OSIDialect.ANSI_SQL, OSIDialect.SNOWFLAKE])
def test_converter_output_conforms_to_core_schema(dialect: OSIDialect) -> None:
    document = (
        MSIToOSIConverter(dialect=dialect)
        .convert(_representative_manifest(), osi_model_name="conformance")
        .output
    )
    schema = _load_schema()

    # Both public serializations (CLI uses to_osi_yaml) must be schema-conformant.
    for serialization, data in (
        ("yaml", yaml.safe_load(document.to_osi_yaml())),
        ("json", json.loads(document.to_osi_json())),
    ):
        errors = validate_schema(data, schema)
        assert errors == [], (
            f"{dialect.value} converter output ({serialization}) is not schema-conformant:\n"
            + "\n".join(errors)
        )
