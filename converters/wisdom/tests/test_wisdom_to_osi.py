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

import json
from pathlib import Path

import pytest
import yaml

from ossie import OSIDialect, OSIDocument
from ossie_wisdom import ConverterIssueType, WisdomToOSIConverter

FIXTURE = Path(__file__).parent / "fixtures" / "sample_export.json"


@pytest.fixture(scope="module")
def result():
    export = json.loads(FIXTURE.read_text())
    return WisdomToOSIConverter().convert(export)


@pytest.fixture(scope="module")
def model(result):
    assert len(result.output.semantic_model) == 1
    return result.output.semantic_model[0]


def _issues_of(result, issue_type):
    return [issue for issue in result.issues if issue.issue_type is issue_type]


def test_model_name_and_description(model):
    assert model.name == "Sample Sales"
    assert model.description == "Synthetic sales domain used for converter tests"


def test_knowledge_becomes_model_ai_context(model):
    assert model.ai_context == (
        "Only answer questions about sales data.\n"
        "- The fiscal year starts in February.\n"
        "- Pipeline refers to open orders expected to close this quarter."
    )


def test_datasets(model):
    datasets = {dataset.name: dataset for dataset in model.datasets}
    assert set(datasets) == {"orders", "customers", "tags"}
    assert datasets["orders"].source == "analytics.sales.orders"
    assert datasets["orders"].description == "Customer orders"
    # Explicit primaryKey wins; otherwise per-column isPrimaryKey flags are collected.
    assert datasets["customers"].primary_key == ["customer_id"]
    assert datasets["orders"].primary_key == ["order_id"]
    assert datasets["tags"].primary_key is None


def test_columns_become_fields(model):
    orders = next(dataset for dataset in model.datasets if dataset.name == "orders")
    fields = {field.name: field for field in orders.fields}
    assert fields["order_id"].expression.dialects[0].expression == "order_id"
    assert fields["order_id"].expression.dialects[0].dialect is OSIDialect.SNOWFLAKE
    assert fields["order_date"].dimension.is_time is True
    assert fields["order_id"].dimension is None
    assert fields["status"].label == "Order Status"
    assert fields["status"].description == "Current order status"
    # Hidden columns are included.
    assert "amount" in fields
    # Non-identifier column names are quoted in the expression so they parse as SQL.
    assert fields["Discount - Percent"].expression.dialects[0].expression == '"Discount - Percent"'


def test_formulas_become_fields(model):
    orders = next(dataset for dataset in model.datasets if dataset.name == "orders")
    fields = {field.name: field for field in orders.fields}
    is_large = fields["is_large"]
    assert is_large.expression.dialects[0].expression == 'CASE WHEN "orders"."amount" > 100 THEN TRUE ELSE FALSE END'
    assert is_large.expression.dialects[0].dialect is OSIDialect.SNOWFLAKE
    assert is_large.label == "Is Large"
    assert is_large.description == "true when the order amount exceeds 100"


def test_formula_colliding_with_column_is_dropped(result, model):
    customers = next(dataset for dataset in model.datasets if dataset.name == "customers")
    assert [field.name for field in customers.fields].count("region") == 1
    dropped = _issues_of(result, ConverterIssueType.DUPLICATE_FIELD_DROPPED)
    assert [issue.element_name for issue in dropped] == ["customers.region"]


def test_unsupported_dialect_falls_back_to_ansi(result, model):
    tags = next(dataset for dataset in model.datasets if dataset.name == "tags")
    assert all(field.expression.dialects[0].dialect is OSIDialect.ANSI_SQL for field in tags.fields)
    unsupported = _issues_of(result, ConverterIssueType.UNSUPPORTED_DIALECT)
    assert len(unsupported) == 1
    assert "postgres" in unsupported[0].element_name


def test_relationship_directions(model):
    relationships = {relationship.name: relationship for relationship in model.relationships}
    # MANY_TO_ONE keeps left as the many side.
    many_to_one = relationships["orders_to_customers"]
    assert (many_to_one.from_dataset, many_to_one.to) == ("orders", "customers")
    assert many_to_one.from_columns == ["customer_id"]
    assert many_to_one.to_columns == ["customer_id"]
    # ONE_TO_MANY is flipped so `from` is the many side; the name is deduped.
    flipped = relationships["orders_to_customers_2"]
    assert (flipped.from_dataset, flipped.to) == ("orders", "customers")


def test_many_to_many_is_kept_with_cardinality_loss(result, model):
    relationships = {relationship.name: relationship for relationship in model.relationships}
    many_to_many = relationships["orders_to_tags"]
    assert many_to_many.ai_context == "many-to-many relationship; cardinality is not representable in Ossie"
    losses = _issues_of(result, ConverterIssueType.CARDINALITY_LOSS)
    assert [issue.element_name for issue in losses] == ["orders <-> tags"]


def test_compound_and_join_is_flattened(model):
    relationships = {relationship.name: relationship for relationship in model.relationships}
    compound = relationships["orders_to_tags_2"]
    assert compound.from_columns == ["order_id", "customer_id"]
    assert compound.to_columns == ["order_id", "customer_id"]


def test_or_join_is_dropped(result, model):
    assert len(model.relationships) == 4
    dropped = _issues_of(result, ConverterIssueType.RELATIONSHIP_DROPPED)
    assert [issue.element_name for issue in dropped] == ["orders <-> tags"]


def test_measures_become_metrics(result, model):
    metrics = {metric.name: metric for metric in model.metrics}
    assert set(metrics) == {"total_amount", "customers_total_amount"}
    total = metrics["total_amount"]
    assert total.expression.dialects[0].expression == 'SUM("orders"."amount")'
    assert total.expression.dialects[0].dialect is OSIDialect.SNOWFLAKE
    assert total.description == "Total order amount"
    collisions = _issues_of(result, ConverterIssueType.METRIC_NAME_COLLISION)
    assert [issue.element_name for issue in collisions] == ["customers.total_amount"]


def test_stale_measure_is_kept_with_warning(result, model):
    assert any(metric.name == "total_amount" for metric in model.metrics)
    stale = _issues_of(result, ConverterIssueType.STALE_MEASURE)
    assert [issue.element_name for issue in stale] == ["orders.total_amount"]


def test_output_round_trips_through_osi_yaml(result):
    document = OSIDocument.model_validate(yaml.safe_load(result.output.to_osi_yaml()))
    assert document == result.output


def test_export_without_tables_is_rejected():
    with pytest.raises(ValueError, match="no tables"):
        WisdomToOSIConverter().convert({"domain": {"zsheet_json": {"ref": {"name": "empty"}}}, "tables": []})
