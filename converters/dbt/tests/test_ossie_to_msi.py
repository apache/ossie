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

"""Tests for OssieToMSIConverter."""

import pytest
from syrupy.assertion import SnapshotAssertion

from ossie import OssieDataType, OssieDimension, OssieFileSource
from ossie_dbt.msi_to_ossie import MSIToOssieConverter
from ossie_dbt.ossie_to_msi import OssieToMSIConverter
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    MetricType,
)
from tests.helpers import (
    _ossie_dataset,
    _ossie_doc,
    _ossie_field,
    _ossie_metric,
    _ossie_relationship,
)


def test_structured_dataset_source_is_rejected() -> None:
    source = OssieFileSource(kind="file", format="parquet", locations=["s3://bucket/orders.parquet"])

    with pytest.raises(TypeError, match="Structured dataset source kind.*file.*not supported"):
        OssieToMSIConverter._parse_source(source)


class TestOssieToMSIBasicConversion:
    def test_empty_document_produces_empty_manifest(self) -> None:
        result = OssieToMSIConverter().convert(_ossie_doc()).output

        assert result.semantic_models == []
        assert result.metrics == []

    def test_single_dataset_becomes_semantic_model(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("orders", source="analytics.orders_table")])
        result = OssieToMSIConverter().convert(doc).output

        assert len(result.semantic_models) == 1
        sm = result.semantic_models[0]
        assert sm.name == "orders"
        assert sm.node_relation.schema_name == "analytics"
        assert sm.node_relation.alias == "orders_table"
        assert sm.node_relation.database is None

    def test_description_carried_over(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("orders", description="Order data")])
        result = OssieToMSIConverter().convert(doc).output

        assert result.semantic_models[0].description == "Order data"

    def test_source_two_parts(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("t", source="myschema.mytable")])
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.schema_name == "myschema"
        assert sm.node_relation.alias == "mytable"
        assert sm.node_relation.database is None

    def test_source_three_parts(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("t", source="mydb.myschema.mytable")])
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.database == "mydb"
        assert sm.node_relation.schema_name == "myschema"
        assert sm.node_relation.alias == "mytable"

    def test_source_bare_name(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("t", source="mytable")])
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.node_relation.alias == "mytable"
        assert sm.node_relation.schema_name == ""

    def test_multiple_datasets_become_multiple_models(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("orders"), _ossie_dataset("users")])
        result = OssieToMSIConverter().convert(doc).output

        names = [sm.name for sm in result.semantic_models]
        assert names == ["orders", "users"]


class TestOssieToMSIFieldClassification:
    def test_primary_key_field_becomes_primary_entity(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    fields=[_ossie_field("order_id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.entities) == 1
        assert sm.entities[0].name == "order_id"
        assert sm.entities[0].type.value == "primary"

    def test_unique_key_field_becomes_unique_entity(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "users",
                    fields=[_ossie_field("email")],
                    unique_keys=[["email"]],
                )
            ]
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.entities) == 1
        assert sm.entities[0].name == "email"
        assert sm.entities[0].type.value == "unique"

    def test_relationship_from_column_becomes_foreign_entity(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset("orders", fields=[_ossie_field("user_id")]),
                _ossie_dataset("users", primary_key=["user_id"]),
            ],
            relationships=[_ossie_relationship("r", "orders", "users", ["user_id"], ["user_id"])],
        )
        orders_sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

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
        doc = _ossie_doc(datasets=[_ossie_dataset("orders", fields=[_ossie_field(field_name, is_time=is_time)])])
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.dimensions) == 1
        assert sm.dimensions[0].name == field_name
        assert sm.dimensions[0].type == expected_type

    @pytest.mark.parametrize(
        ("dimension", "datatype", "expected_type"),
        [
            (OssieDimension(), OssieDataType.DATE, DimensionType.TIME),
            (OssieDimension(is_time=False), OssieDataType.DATE_TIME_TZ, DimensionType.CATEGORICAL),
            (None, OssieDataType.DATE, DimensionType.CATEGORICAL),
        ],
    )
    def test_field_becomes_dimension_by_effective_time_role(
        self,
        dimension: OssieDimension | None,
        datatype: OssieDataType,
        expected_type: DimensionType,
    ) -> None:
        field = _ossie_field("occurred_at").model_copy(
            update={"dimension": dimension, "datatype": datatype}
        )
        doc = _ossie_doc(datasets=[_ossie_dataset("events", fields=[field])])

        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.dimensions[0].type == expected_type

    def test_field_referenced_in_metric_stays_as_dimension(self) -> None:
        """Fields referenced in metric expressions are no longer promoted to measures."""
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("revenue", "SUM(amount)")],
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert len(sm.measures) == 0
        assert len(sm.dimensions) == 1
        assert sm.dimensions[0].name == "amount"

    def test_expr_different_from_name_is_preserved(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    fields=[_ossie_field("order_id", expression="id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.entities[0].expr == "id"

    def test_expr_same_as_name_is_stored_as_none(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    fields=[_ossie_field("order_id")],
                    primary_key=["order_id"],
                )
            ]
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        assert sm.entities[0].expr is None

    def test_description_and_label_carried_over_to_dimension(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    fields=[_ossie_field("status", description="Order status", label="Status")],
                )
            ]
        )
        sm = OssieToMSIConverter().convert(doc).output.semantic_models[0]

        dim = sm.dimensions[0]
        assert dim.description == "Order status"
        assert dim.label == "Status"


class TestOssieToMSIMetricConversion:
    def test_sum_expression_produces_simple_metric(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("revenue", "SUM(amount)")],
        )
        result = OssieToMSIConverter().convert(doc).output

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
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("user_id")])],
            metrics=[_ossie_metric("unique_users", "COUNT(DISTINCT user_id)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.measure is None
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.COUNT_DISTINCT
        assert m.type_params.expr == "user_id"
        sm = result.semantic_models[0]
        assert len(sm.measures) == 0

    def test_ratio_expression_produces_ratio_metric(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    fields=[_ossie_field("amount"), _ossie_field("order_id")],
                )
            ],
            metrics=[_ossie_metric("arpu", "(SUM(amount)) / (COUNT(order_id))")],
        )
        result = OssieToMSIConverter().convert(doc).output

        ratio = next(m for m in result.metrics if m.type == MetricType.RATIO)
        assert ratio.name == "arpu"
        assert ratio.type_params.numerator is not None
        assert ratio.type_params.denominator is not None
        assert ratio.type_params.numerator.name == "arpu__numerator"
        assert ratio.type_params.denominator.name == "arpu__denominator"

    def test_ratio_sub_metrics_are_simple(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount"), _ossie_field("cnt")])],
            metrics=[_ossie_metric("ratio", "(SUM(amount)) / (COUNT(cnt))")],
        )
        result = OssieToMSIConverter().convert(doc).output

        simple_metrics = [m for m in result.metrics if m.type == MetricType.SIMPLE]
        assert len(simple_metrics) == 2
        names = {m.name for m in simple_metrics}
        assert names == {"ratio__numerator", "ratio__denominator"}

    def test_complex_expression_falls_back_to_simple_with_raw_expr(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders")],
            metrics=[_ossie_metric("complex", "SUM(a) + SUM(b)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        assert len(result.metrics) == 1
        m = result.metrics[0]
        assert m.type == MetricType.SIMPLE
        assert m.type_params.measure is None
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.expr == "SUM(a) + SUM(b)"

    def test_metric_description_carried_over(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("revenue", "SUM(amount)", description="Total revenue")],
        )
        result = OssieToMSIConverter().convert(doc).output

        assert result.metrics[0].description == "Total revenue"

    def test_no_metrics_produces_empty_list(self) -> None:
        doc = _ossie_doc(datasets=[_ossie_dataset("orders")])
        result = OssieToMSIConverter().convert(doc).output

        assert result.metrics == []

    def test_dataset_qualified_column_reference(self) -> None:
        """A metric referencing 'dataset.col' should resolve the semantic_model to that dataset."""
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("revenue", "SUM(orders.amount)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.semantic_model == "orders"
        assert m.type_params.expr == "amount"

    def test_sum_boolean_qualified_column_resolves_semantic_model(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset("customers", fields=[_ossie_field("customer_id")]),
                _ossie_dataset("orders", fields=[_ossie_field("order_id")]),
            ],
            metrics=[
                _ossie_metric(
                    "has_order",
                    "SUM(CASE WHEN orders.order_id IS NOT NULL THEN 1 ELSE 0 END)",
                )
            ],
        )
        result = OssieToMSIConverter().convert(doc).output

        metric = result.metrics[0]
        assert metric.type_params.metric_aggregation_params is not None
        assert metric.type_params.metric_aggregation_params.agg == AggregationType.SUM_BOOLEAN
        assert metric.type_params.metric_aggregation_params.semantic_model == "orders"

    def test_fully_qualified_column_preserves_dataset_name(self) -> None:
        doc = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "analytics.orders",
                    fields=[_ossie_field("order_id")],
                )
            ],
            metrics=[_ossie_metric("order_count", "COUNT(analytics.orders.order_id)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        metric = result.metrics[0]
        assert metric.type_params.metric_aggregation_params is not None
        assert metric.type_params.metric_aggregation_params.semantic_model == "analytics.orders"

    def test_percentile_cont_0_5_produces_median(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("median_amount", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.MEDIAN
        assert m.type_params.metric_aggregation_params.agg_params is None
        assert m.type_params.expr == "amount"

    def test_percentile_cont_non_median_carries_percentile_param(self) -> None:
        doc = _ossie_doc(
            datasets=[_ossie_dataset("orders", fields=[_ossie_field("amount")])],
            metrics=[_ossie_metric("p95_amount", "PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY amount)")],
        )
        result = OssieToMSIConverter().convert(doc).output

        m = result.metrics[0]
        assert m.type_params.metric_aggregation_params is not None
        assert m.type_params.metric_aggregation_params.agg == AggregationType.PERCENTILE
        assert m.type_params.metric_aggregation_params.agg_params is not None
        assert m.type_params.metric_aggregation_params.agg_params.percentile == 0.95
        assert m.type_params.expr == "amount"


class TestOssieToMSIRoundTrip:
    def test_ossie_to_msi_to_ossie_preserves_structure(self, snapshot: SnapshotAssertion) -> None:
        """Ossie → MSI → Ossie preserves dataset names, fields, and metric expressions."""
        original = _ossie_doc(
            datasets=[
                _ossie_dataset(
                    "orders",
                    source="analytics.orders",
                    fields=[
                        _ossie_field("order_id"),
                        _ossie_field("status"),
                        _ossie_field("created_at", is_time=True),
                        _ossie_field("amount"),
                    ],
                    primary_key=["order_id"],
                )
            ],
            metrics=[_ossie_metric("revenue", "SUM(orders.amount)")],
        )

        msi = OssieToMSIConverter().convert(original).output
        assert msi.semantic_models[0].measures == []

        ossie_doc = MSIToOssieConverter().convert(msi).output

        dataset = ossie_doc.semantic_model[0].datasets[0]
        assert dataset.name == "orders"

        field_names = {f.name for f in dataset.fields or []}
        assert "order_id" in field_names
        assert "status" in field_names
        assert "created_at" in field_names
        assert "amount" in field_names

        metrics = ossie_doc.semantic_model[0].metrics or []
        assert len(metrics) == 1
        assert metrics[0].name == "revenue"
        assert metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"
        assert ossie_doc.to_ossie_yaml() == snapshot
