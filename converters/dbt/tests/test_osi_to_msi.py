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

"""Tests for OSIToMSIConverter."""

import pytest
from syrupy.assertion import SnapshotAssertion

from ossie_dbt.msi_to_osi import MSIToOSIConverter
from ossie_dbt.osi_to_msi import OSIToMSIConverter
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    MetricType,
)
from tests.helpers import (
    _osi_dataset,
    _osi_doc,
    _osi_field,
    _osi_metric,
    _osi_relationship,
)


class TestOSIToMSIBasicConversion:
    def test_empty_document_produces_empty_manifest(self) -> None:
        result = OSIToMSIConverter().convert(_osi_doc()).output

        assert result.semantic_models == []
        assert result.metrics == []

    def test_single_dataset_becomes_semantic_model(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("orders", source="analytics.orders_table")])
        result = OSIToMSIConverter().convert(doc).output

        assert len(result.semantic_models) == 1
        sm = result.semantic_models[0]
        assert sm.name == "orders"
        assert sm.node_relation.schema_name == "analytics"
        assert sm.node_relation.alias == "orders_table"
        assert sm.node_relation.database is None

    def test_description_carried_over(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("orders", description="Order data")])
        result = OSIToMSIConverter().convert(doc).output

        assert result.semantic_models[0].description == "Order data"

    def test_source_two_parts(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("t", source="myschema.mytable")])
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.schema_name == "myschema"
        assert sm.node_relation.alias == "mytable"
        assert sm.node_relation.database is None

    def test_source_three_parts(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("t", source="mydb.myschema.mytable")])
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.database == "mydb"
        assert sm.node_relation.schema_name == "myschema"
        assert sm.node_relation.alias == "mytable"

    def test_source_bare_name(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("t", source="mytable")])
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.alias == "mytable"
        assert sm.node_relation.schema_name == ""

    def test_multiple_datasets_become_multiple_models(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("orders"), _osi_dataset("users")])
        result = OSIToMSIConverter().convert(doc).output

        names = [sm.name for sm in result.semantic_models]
        assert names == ["orders", "users"]


class TestOSIToMSIFieldClassification:
    def test_primary_key_field_becomes_primary_entity(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    fields=[_osi_field("order_id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.entities) == 1
        assert sm.entities[0].name == "order_id"
        assert sm.entities[0].type.value == "primary"

    def test_unique_key_field_becomes_unique_entity(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "users",
                    fields=[_osi_field("email")],
                    unique_keys=[["email"]],
                )
            ]
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.entities) == 1
        assert sm.entities[0].name == "email"
        assert sm.entities[0].type.value == "unique"

    def test_relationship_from_column_becomes_foreign_entity(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset("orders", fields=[_osi_field("user_id")]),
                _osi_dataset("users", primary_key=["user_id"]),
            ],
            relationships=[_osi_relationship("r", "orders", "users", ["user_id"], ["user_id"])],
        )
        orders_sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(orders_sm.entities) == 1
        assert orders_sm.entities[0].name == "user_id"
        assert orders_sm.entities[0].type.value == "foreign"

    @pytest.mark.parametrize(
        "field_name, is_time, expected_type",
        [
            ("created_at", True, DimensionType.TIME),
            ("status", False, DimensionType.CATEGORICAL),
            ("region", None, DimensionType.CATEGORICAL),
        ],
    )
    def test_field_becomes_dimension_by_is_time(
        self, field_name: str, is_time: bool | None, expected_type: DimensionType
    ) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("orders", fields=[_osi_field(field_name, is_time=is_time)])])
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.dimensions) == 1
        assert sm.dimensions[0].name == field_name
        assert sm.dimensions[0].type == expected_type

    def test_field_referenced_in_metric_stays_as_dimension(self) -> None:
        """Fields referenced in metric expressions are no longer promoted to measures."""
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("revenue", "SUM(amount)")],
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.measures) == 0
        assert len(sm.dimensions) == 1
        assert sm.dimensions[0].name == "amount"

    def test_expr_different_from_name_is_preserved(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    fields=[_osi_field("order_id", expression="id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.entities[0].expr == "id"

    def test_expr_same_as_name_is_stored_as_none(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    fields=[_osi_field("order_id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.entities[0].expr is None

    def test_description_and_label_carried_over_to_dimension(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    fields=[_osi_field("status", description="Order status", label="Status")],
                )
            ]
        )
        sm = OSIToMSIConverter().convert(doc).output.semantic_models[0]

        dim = sm.dimensions[0]
        assert dim.description == "Order status"
        assert dim.label == "Status"


class TestOSIToMSIMetricConversion:
    def test_sum_expression_produces_simple_metric(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("revenue", "SUM(amount)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        assert len(result.metrics) == 1
        m = result.metrics[0]
        assert m.name == "revenue"
        assert m.type == MetricType.SIMPLE
        assert m.type_params.measure is None
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.SUM
        assert m.type_params.expr == "amount"
        assert m.type_params.metric_aggregation_params.semantic_model == "orders"

    def test_count_distinct_expression(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("user_id")])],
            metrics=[_osi_metric("unique_users", "COUNT(DISTINCT user_id)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.measure is None
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.COUNT_DISTINCT
        assert m.type_params.expr == "user_id"
        sm = result.semantic_models[0]
        assert len(sm.measures) == 0

    def test_ratio_expression_produces_ratio_metric(self) -> None:
        doc = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    fields=[_osi_field("amount"), _osi_field("order_id")],
                )
            ],
            metrics=[_osi_metric("arpu", "(SUM(amount)) / (COUNT(order_id))")],
        )
        result = OSIToMSIConverter().convert(doc).output

        ratio = next(m for m in result.metrics if m.type == MetricType.RATIO)
        assert ratio.name == "arpu"
        assert ratio.type_params.numerator is not None
        assert ratio.type_params.denominator is not None
        assert ratio.type_params.numerator.name == "arpu__numerator"
        assert ratio.type_params.denominator.name == "arpu__denominator"

    def test_ratio_sub_metrics_are_simple(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount"), _osi_field("cnt")])],
            metrics=[_osi_metric("ratio", "(SUM(amount)) / (COUNT(cnt))")],
        )
        result = OSIToMSIConverter().convert(doc).output

        simple_metrics = [m for m in result.metrics if m.type == MetricType.SIMPLE]
        assert len(simple_metrics) == 2
        names = {m.name for m in simple_metrics}
        assert names == {"ratio__numerator", "ratio__denominator"}

    def test_complex_expression_falls_back_to_simple_with_raw_expr(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders")],
            metrics=[_osi_metric("complex", "SUM(a) + SUM(b)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        assert len(result.metrics) == 1
        m = result.metrics[0]
        assert m.type == MetricType.SIMPLE
        assert m.type_params.measure is None
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.expr == "SUM(a) + SUM(b)"

    def test_metric_description_carried_over(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("revenue", "SUM(amount)", description="Total revenue")],
        )
        result = OSIToMSIConverter().convert(doc).output

        assert result.metrics[0].description == "Total revenue"

    def test_no_metrics_produces_empty_list(self) -> None:
        doc = _osi_doc(datasets=[_osi_dataset("orders")])
        result = OSIToMSIConverter().convert(doc).output

        assert result.metrics == []

    def test_dataset_qualified_column_reference(self) -> None:
        """A metric referencing 'dataset.col' should resolve the semantic_model to that dataset."""
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("revenue", "SUM(orders.amount)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.semantic_model == "orders"
        assert m.type_params.expr == "amount"

    def test_percentile_cont_0_5_produces_median(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("median_amount", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.MEDIAN
        assert m.type_params.metric_aggregation_params.agg_params is None
        assert m.type_params.expr == "amount"

    def test_percentile_cont_non_median_carries_percentile_param(self) -> None:
        doc = _osi_doc(
            datasets=[_osi_dataset("orders", fields=[_osi_field("amount")])],
            metrics=[_osi_metric("p95_amount", "PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY amount)")],
        )
        result = OSIToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.PERCENTILE
        assert m.type_params.metric_aggregation_params.agg_params is not None
        assert m.type_params.metric_aggregation_params.agg_params.percentile == 0.95
        assert m.type_params.expr == "amount"


class TestOSIToMSIRoundTrip:
    def test_osi_to_msi_to_osi_preserves_structure(self, snapshot: SnapshotAssertion) -> None:
        """Ossie → MSI → Ossie preserves dataset names, fields, and metric expressions."""
        original = _osi_doc(
            datasets=[
                _osi_dataset(
                    "orders",
                    source="analytics.orders",
                    fields=[
                        _osi_field("order_id"),
                        _osi_field("status"),
                        _osi_field("created_at", is_time=True),
                        _osi_field("amount"),
                    ],
                    primary_key=["order_id"],
                )
            ],
            metrics=[_osi_metric("revenue", "SUM(orders.amount)")],
        )

        msi = OSIToMSIConverter().convert(original).output
        assert msi.semantic_models[0].measures == []

        osi_doc = MSIToOSIConverter().convert(msi).output

        dataset = osi_doc.semantic_model[0].datasets[0]
        assert dataset.name == "orders"

        field_names = {f.name for f in dataset.fields or []}
        assert "order_id" in field_names
        assert "status" in field_names
        assert "created_at" in field_names
        assert "amount" in field_names

        metrics = osi_doc.semantic_model[0].metrics or []
        assert len(metrics) == 1
        assert metrics[0].name == "revenue"
        assert metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"
        assert osi_doc.to_osi_yaml() == snapshot
