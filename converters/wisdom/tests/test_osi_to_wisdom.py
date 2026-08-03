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

from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIRelationship,
    OSISemanticModel,
)
from ossie_wisdom import ConverterIssueType, OSIToWisdomConverter, WisdomToOSIConverter

FIXTURE = Path(__file__).parent / "fixtures" / "sample_export.json"


def _snowflake(expression):
    return OSIExpression(dialects=[OSIDialectExpression(dialect=OSIDialect.SNOWFLAKE, expression=expression)])


@pytest.fixture(scope="module")
def osi_document():
    export = json.loads(FIXTURE.read_text())
    return WisdomToOSIConverter().convert(export).output


@pytest.fixture(scope="module")
def result(osi_document):
    return OSIToWisdomConverter().convert(osi_document, exported_at="2026-07-10T00:00:00+00:00")


@pytest.fixture(scope="module")
def export(result):
    return result.output


def _issues_of(result, issue_type):
    return [issue for issue in result.issues if issue.issue_type is issue_type]


def _table(export, name):
    return next(
        table["zsheet_json"] for table in export["tables"] if table["zsheet_json"]["ref"]["name"] == name
    )


def test_export_envelope(export):
    assert export["version"] == "1.0"
    assert export["export_metadata"]["domain_name"] == "Sample Sales"
    assert export["export_metadata"]["source_domain_id"] == export["domain"]["zsheet_json"]["ref"]["uuid"]
    domain = export["domain"]["zsheet_json"]
    assert domain["zsheetType"] == "DOMAIN"
    assert domain["description"] == "Synthetic sales domain used for converter tests"


def test_ai_context_splits_into_instructions_and_knowledge(export):
    domain = export["domain"]["zsheet_json"]
    assert domain["domainSystemInstructions"] == "Only answer questions about sales data."
    assert [knowledge["content"] for knowledge in domain["knowledge"]] == [
        "The fiscal year starts in February.",
        "Pipeline refers to open orders expected to close this quarter.",
    ]


def test_tables_and_locations(export):
    orders = _table(export, "orders")
    assert orders["location"]["database"] == "analytics"
    assert orders["location"]["schema"] == "sales"
    assert orders["location"]["dbTable"] == "orders"
    assert orders["primaryKey"] == {"columns": ["order_id"]}
    assert _table(export, "customers")["primaryKey"] == {"columns": ["customer_id"]}
    metadata = {entry["zsheet_uuid"]: entry for entry in export["table_metadata"]}
    assert metadata[orders["ref"]["uuid"]]["table_name"] == "orders"


def test_fields_split_into_columns_and_formulas(export):
    orders = _table(export, "orders")
    column_names = [column["name"] for column in orders["columns"]]
    assert "order_id" in column_names
    # A quoted bare-name expression is recognized as a plain column.
    assert "Discount - Percent" in column_names
    formulas = {formula["name"]: formula for formula in orders["formulas"]}
    assert set(formulas) == {"is_large"}
    assert formulas["is_large"]["expression"] == 'CASE WHEN "orders"."amount" > 100 THEN TRUE ELSE FALSE END'
    assert formulas["is_large"]["properties"]["displayName"] == "Is Large"
    status = next(column for column in orders["columns"] if column["name"] == "status")
    assert status["description"] == "Current order status"
    assert status["properties"]["displayName"] == "Order Status"


def test_metrics_attach_to_referenced_tables(export):
    orders_measures = {measure["name"] for measure in _table(export, "orders")["measures"]}
    customers_measures = {measure["name"] for measure in _table(export, "customers")["measures"]}
    assert orders_measures == {"total_amount"}
    assert customers_measures == {"customers_total_amount"}


def test_relationship_types_restored(export):
    edges = export["domain"]["zsheet_json"]["relationshipGraph"]["relationships"]
    types = [edge["properties"]["relationshipType"] for edge in edges]
    assert types == ["MANY_TO_ONE", "MANY_TO_ONE", "MANY_TO_MANY", "MANY_TO_ONE"]
    compound = edges[3]["properties"]["compoundJoinCondition"]["nestedCondition"]
    assert compound["logicalOperator"] == "AND"
    assert len(compound["conditions"]) == 2


def test_connections_are_per_dialect(export):
    dialects = {connection["dialect"] for connection in export["connections"]}
    assert dialects == {"snowflake", "ansi"}
    assert _table(export, "orders")["location"]["connectionId"] == "et-connection-snowflake"
    assert _table(export, "tags")["location"]["connectionId"] == "et-connection-ansi"


def test_round_trip_preserves_osi_document(osi_document, export):
    round_tripped = WisdomToOSIConverter().convert(export).output
    assert round_tripped == osi_document


def test_deterministic_output(osi_document, export):
    again = OSIToWisdomConverter().convert(osi_document, exported_at="2026-07-10T00:00:00+00:00").output
    assert again == export


def test_extra_models_and_unrepresentable_elements_are_reported():
    dataset = OSIDataset(
        name="orders",
        source="analytics.sales.orders",
        unique_keys=[["order_id"]],
        fields=[
            OSIField(name="order_id", expression=_snowflake("order_id"), ai_context="the identifier"),
        ],
    )
    second = OSISemanticModel(name="second", datasets=[OSIDataset(name="d", source="a.b.c")])
    document = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="first",
                datasets=[dataset],
                relationships=[
                    OSIRelationship(
                        name="orders_to_missing",
                        from_dataset="orders",
                        to="missing",
                        from_columns=["x"],
                        to_columns=["y"],
                    )
                ],
            ),
            second,
        ]
    )
    result = OSIToWisdomConverter().convert(document, exported_at="2026-07-10T00:00:00+00:00")
    assert [issue.element_name for issue in _issues_of(result, ConverterIssueType.EXTRA_MODEL_DROPPED)] == ["second"]
    assert [issue.element_name for issue in _issues_of(result, ConverterIssueType.UNIQUE_KEYS_DROPPED)] == ["orders"]
    assert [issue.element_name for issue in _issues_of(result, ConverterIssueType.AI_CONTEXT_DROPPED)] == [
        "orders.order_id"
    ]
    assert [issue.element_name for issue in _issues_of(result, ConverterIssueType.RELATIONSHIP_DROPPED)] == [
        "orders_to_missing"
    ]
    assert result.output["domain"]["zsheet_json"]["relationshipGraph"]["relationships"] == []


def test_one_to_one_note_restores_relationship_type():
    document = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="m",
                datasets=[
                    OSIDataset(name="a", source="db.s.a"),
                    OSIDataset(name="b", source="db.s.b"),
                ],
                relationships=[
                    OSIRelationship(
                        name="a_to_b",
                        from_dataset="a",
                        to="b",
                        from_columns=["id"],
                        to_columns=["id"],
                        ai_context="one-to-one relationship",
                    )
                ],
            )
        ]
    )
    export = OSIToWisdomConverter().convert(document, exported_at="2026-07-10T00:00:00+00:00").output
    edges = export["domain"]["zsheet_json"]["relationshipGraph"]["relationships"]
    assert edges[0]["properties"]["relationshipType"] == "ONE_TO_ONE"


def test_unresolved_metric_attaches_to_first_dataset():
    from ossie import OSIMetric

    document = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="m",
                datasets=[OSIDataset(name="a", source="db.s.a"), OSIDataset(name="b", source="db.s.b")],
                metrics=[OSIMetric(name="row_count", expression=_snowflake("COUNT(*)"))],
            )
        ]
    )
    result = OSIToWisdomConverter().convert(document, exported_at="2026-07-10T00:00:00+00:00")
    export = result.output
    assert [measure["name"] for measure in _table(export, "a")["measures"]] == ["row_count"]
    assert [issue.element_name for issue in _issues_of(result, ConverterIssueType.METRIC_TABLE_UNRESOLVED)] == [
        "row_count"
    ]
