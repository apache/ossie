import json
from typing import List, Optional

import pytest
from syrupy.assertion import SnapshotAssertion

from osi_dbt.converter_issues import ConverterIssueType
from osi_dbt.filter_utils import _render_filter_template
from osi import OSIDialect, OSIDocument
from osi_dbt.msi_to_osi import MSIToOSIConverter
from metricflow_semantic_interfaces.implementations.metric import (
    PydanticConversionTypeParams,
    PydanticCumulativeTypeParams,
    PydanticMetric,
    PydanticMetricAggregationParams,
    PydanticMetricInput,
    PydanticMetricInputMeasure,
    PydanticMetricTimeWindow,
    PydanticMetricTypeParams,
)
from metricflow_semantic_interfaces.implementations.semantic_model import (
    PydanticNodeRelation,
    PydanticSemanticModel,
)
from metricflow_semantic_interfaces.test_utils import default_meta, semantic_model_with_guaranteed_meta
from metricflow_semantic_interfaces.type_enums import (
    AggregationType,
    DimensionType,
    EntityType,
    MetricType,
    TimeGranularity,
)
from tests.helpers import (
    _dimension,
    _entity,
    _filter,
    _manifest,
    _measure,
    _simple_metric,
)

# ---------------------------------------------------------------------------
# Result navigation helpers
# ---------------------------------------------------------------------------


def _fields(result: OSIDocument, dataset_idx: int = 0) -> list:
    """Return fields for a dataset, asserting they exist."""
    fields = result.semantic_model[0].datasets[dataset_idx].fields
    assert fields is not None
    return fields


def _field_expr(result: OSIDocument, field_idx: int = 0) -> str:
    """Return the ANSI SQL expression for a field by index."""
    return _fields(result)[field_idx].expression.dialects[0].expression


def _osi_metrics(result: OSIDocument) -> list:
    """Return OSI metrics for the first semantic model, asserting they exist."""
    metrics = result.semantic_model[0].metrics
    assert metrics is not None
    return metrics


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasicConversion:
    def test_empty_manifest_produces_empty_datasets(self) -> None:
        result = MSIToOSIConverter().convert(_manifest(), osi_model_name="test").output

        assert result.version == "0.2.0.dev0"
        assert len(result.semantic_model) == 1
        assert result.semantic_model[0].name == "test"
        assert result.semantic_model[0].datasets == []
        assert result.semantic_model[0].metrics is None
        assert result.semantic_model[0].relationships is None

    def test_semantic_model_becomes_dataset(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            description="Order data",
            node_relation=PydanticNodeRelation(schema_name="analytics", alias="orders_table"),
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        dataset = result.semantic_model[0].datasets[0]
        assert dataset.name == "orders"
        assert dataset.source == "analytics.orders_table"
        assert dataset.description == "Order data"

    def test_source_includes_database_when_present(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            node_relation=PydanticNodeRelation(schema_name="analytics", alias="orders_table", database="prod"),
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert result.semantic_model[0].datasets[0].source == "prod.analytics.orders_table"

    def test_multiple_semantic_models_become_multiple_datasets(self) -> None:
        sm_a = semantic_model_with_guaranteed_meta(name="orders")
        sm_b = semantic_model_with_guaranteed_meta(name="users")
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm_a, sm_b])).output

        names = [ds.name for ds in result.semantic_model[0].datasets]
        assert names == ["orders", "users"]


class TestDimensionConversion:
    def test_categorical_dimension_has_is_time_false(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("status", dim_type=DimensionType.CATEGORICAL)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.name == "status"
        assert field.dimension is not None
        assert field.dimension.is_time is False

    def test_time_dimension_has_is_time_true(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("ds", dim_type=DimensionType.TIME, granularity=TimeGranularity.DAY)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.dimension is not None
        assert field.dimension.is_time is True

    def test_dimension_with_expr_uses_expr(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("order_date", expr="DATE(created_at)")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert _field_expr(result) == "DATE(created_at)"

    def test_dimension_without_expr_falls_back_to_name(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("status")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert _field_expr(result) == "status"

    def test_dimension_description_and_label_carried_over(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("status", description="Order status", label="Status")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.description == "Order status"
        assert field.label == "Status"


class TestMeasureConversion:
    def test_measure_becomes_field_without_dimension_metadata(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.name == "revenue"
        assert field.expression.dialects[0].expression == "amount"
        assert field.dimension is None

    def test_measure_without_expr_falls_back_to_name(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("num_orders", agg=AggregationType.SUM)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert _field_expr(result) == "num_orders"

    def test_measure_description_and_label_carried_over(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", description="Total revenue", label="Revenue")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.description == "Total revenue"
        assert field.label == "Revenue"


class TestEntityConversion:
    def test_entity_becomes_field(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("order_id", entity_type=EntityType.PRIMARY)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.name == "order_id"
        assert field.expression.dialects[0].expression == "order_id"
        assert field.dimension is None

    def test_entity_with_expr_uses_expr(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("order_id", entity_type=EntityType.PRIMARY, expr="id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        field = _fields(result)[0]
        assert field.name == "order_id"
        assert field.expression.dialects[0].expression == "id"

    def test_foreign_entity_also_becomes_field(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("user_id", entity_type=EntityType.FOREIGN)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert _fields(result)[0].name == "user_id"


class TestEntityKeyExtraction:
    @pytest.mark.parametrize(
        "entity_type, name, expr, expected_pk, expected_uk",
        [
            (EntityType.PRIMARY, "order_id", None, ["order_id"], None),
            (EntityType.PRIMARY, "order_id", "id", ["id"], None),
            (EntityType.UNIQUE, "email", None, None, [["email"]]),
            (EntityType.FOREIGN, "user_id", None, None, None),
        ],
    )
    def test_key_extraction(
        self,
        entity_type: EntityType,
        name: str,
        expr: Optional[str],
        expected_pk: Optional[List[str]],
        expected_uk: Optional[List[List[str]]],
    ) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity(name, entity_type=entity_type, expr=expr)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        dataset = result.semantic_model[0].datasets[0]
        assert dataset.primary_key == expected_pk
        assert dataset.unique_keys == expected_uk


class TestFieldOrdering:
    def test_entities_then_dimensions_then_measures(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("order_id", entity_type=EntityType.PRIMARY)],
            dimensions=[_dimension("status")],
            measures=[_measure("revenue", expr="amount")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        fields = _fields(result)
        assert fields[0].name == "order_id"
        assert fields[1].name == "status"
        assert fields[2].name == "revenue"


class TestDialectConfiguration:
    def test_default_dialect_is_ansi_sql(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("status")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert _fields(result)[0].expression.dialects[0].dialect == OSIDialect.ANSI_SQL

    def test_configurable_dialect(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            dimensions=[_dimension("status")],
        )
        result = MSIToOSIConverter(dialect=OSIDialect.SNOWFLAKE).convert(_manifest(semantic_models=[sm])).output

        assert _fields(result)[0].expression.dialects[0].dialect == OSIDialect.SNOWFLAKE


class TestRelationshipConversion:
    def test_shared_entity_name_produces_relationship(self) -> None:
        listings = semantic_model_with_guaranteed_meta(
            name="listings",
            entities=[_entity("listing", entity_type=EntityType.PRIMARY, expr="listing_id")],
        )
        bookings = semantic_model_with_guaranteed_meta(
            name="bookings",
            entities=[_entity("listing", entity_type=EntityType.FOREIGN, expr="listing_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[listings, bookings])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert len(rels) == 1
        rel = rels[0]
        assert rel.from_dataset == "bookings"
        assert rel.to == "listings"
        assert rel.from_columns == ["listing_id"]
        assert rel.to_columns == ["listing_id"]

    def test_same_type_entities_produce_relationship(self) -> None:
        users_a = semantic_model_with_guaranteed_meta(
            name="users_a",
            entities=[_entity("user", entity_type=EntityType.PRIMARY, expr="user_id")],
        )
        users_b = semantic_model_with_guaranteed_meta(
            name="users_b",
            entities=[_entity("user", entity_type=EntityType.PRIMARY, expr="uid")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[users_a, users_b])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert len(rels) == 1
        assert rels[0].from_columns == ["user_id"]
        assert rels[0].to_columns == ["uid"]

    def test_single_dataset_with_entity_produces_no_relationship(self) -> None:
        bookings = semantic_model_with_guaranteed_meta(
            name="bookings",
            entities=[_entity("listing", entity_type=EntityType.FOREIGN, expr="listing_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[bookings])).output

        assert result.semantic_model[0].relationships is None

    def test_same_dataset_entities_excluded(self) -> None:
        orders = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[
                _entity("order", entity_type=EntityType.PRIMARY, expr="order_id"),
                _entity("order", entity_type=EntityType.FOREIGN, expr="order_id"),
            ],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[orders])).output

        assert result.semantic_model[0].relationships is None

    def test_three_datasets_produce_all_pairs(self, snapshot: SnapshotAssertion) -> None:
        users_a = semantic_model_with_guaranteed_meta(
            name="users_a",
            entities=[_entity("user", entity_type=EntityType.PRIMARY, expr="user_id")],
        )
        users_b = semantic_model_with_guaranteed_meta(
            name="users_b",
            entities=[_entity("user", entity_type=EntityType.UNIQUE, expr="user_id")],
        )
        orders = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("user", entity_type=EntityType.FOREIGN, expr="user_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[users_a, users_b, orders])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert len(rels) == 3
        pairs = {(r.from_dataset, r.to) for r in rels}
        assert pairs == {("users_a", "users_b"), ("orders", "users_a"), ("orders", "users_b")}
        assert result.to_osi_yaml() == snapshot

    def test_columns_use_expr_when_present(self) -> None:
        listings = semantic_model_with_guaranteed_meta(
            name="listings",
            entities=[_entity("listing", entity_type=EntityType.PRIMARY, expr="lid")],
        )
        bookings = semantic_model_with_guaranteed_meta(
            name="bookings",
            entities=[_entity("listing", entity_type=EntityType.FOREIGN, expr="fk_lid")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[listings, bookings])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        rel = rels[0]
        assert rel.from_columns == ["fk_lid"]
        assert rel.to_columns == ["lid"]

    def test_columns_fall_back_to_name_without_expr(self) -> None:
        listings = semantic_model_with_guaranteed_meta(
            name="listings",
            entities=[_entity("listing", entity_type=EntityType.PRIMARY)],
        )
        bookings = semantic_model_with_guaranteed_meta(
            name="bookings",
            entities=[_entity("listing", entity_type=EntityType.FOREIGN)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[listings, bookings])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert rels[0].from_columns == ["listing"]
        assert rels[0].to_columns == ["listing"]

    def test_primary_entity_shorthand_does_not_produce_relationship(self) -> None:
        bookings = PydanticSemanticModel(
            name="bookings",
            description=None,
            node_relation=PydanticNodeRelation(schema_name="schema", alias="table"),
            primary_entity="booking",
            entities=[],
            metadata=default_meta(),
        )
        orders = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("booking", entity_type=EntityType.FOREIGN, expr="booking_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[bookings, orders])).output

        assert result.semantic_model[0].relationships is None

    def test_relationship_name_format(self) -> None:
        listings = semantic_model_with_guaranteed_meta(
            name="listings",
            entities=[_entity("listing", entity_type=EntityType.PRIMARY, expr="listing_id")],
        )
        bookings = semantic_model_with_guaranteed_meta(
            name="bookings",
            entities=[_entity("listing", entity_type=EntityType.FOREIGN, expr="listing_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[listings, bookings])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert rels[0].name == "bookings__listings__listing"

    def test_natural_entity_excluded(self) -> None:
        users = semantic_model_with_guaranteed_meta(
            name="users",
            entities=[_entity("user", entity_type=EntityType.NATURAL, expr="user_id")],
        )
        orders = semantic_model_with_guaranteed_meta(
            name="orders",
            entities=[_entity("user", entity_type=EntityType.FOREIGN, expr="user_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[users, orders])).output

        assert result.semantic_model[0].relationships is None

    def test_direction_based_on_entity_type_not_manifest_order(self) -> None:
        beta = semantic_model_with_guaranteed_meta(
            name="beta",
            entities=[_entity("shared", entity_type=EntityType.PRIMARY, expr="col_b")],
        )
        alpha = semantic_model_with_guaranteed_meta(
            name="alpha",
            entities=[_entity("shared", entity_type=EntityType.FOREIGN, expr="col_a")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[beta, alpha])).output

        rels = result.semantic_model[0].relationships
        assert rels is not None
        assert rels[0].from_dataset == "alpha"
        assert rels[0].to == "beta"


class TestMetricConversion:
    # --- SIMPLE ---

    def test_simple_metric_resolves_through_measure(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = _simple_metric("revenue", measure_name="revenue")
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        metrics = _osi_metrics(result)
        assert len(metrics) == 1
        assert metrics[0].name == "revenue"
        assert metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"

    def test_simple_metric_description_carried_over(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = _simple_metric("revenue", measure_name="revenue", description="Total revenue")
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert _osi_metrics(result)[0].description == "Total revenue"

    def test_simple_metric_with_metric_aggregation_params(self) -> None:
        sm = semantic_model_with_guaranteed_meta(name="orders")
        metric = PydanticMetric(
            name="avg_price",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(
                expr="price",
                metric_aggregation_params=PydanticMetricAggregationParams(
                    semantic_model="orders",
                    agg=AggregationType.AVERAGE,
                    agg_params=None,
                    agg_time_dimension=None,
                    non_additive_dimension=None,
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert _osi_metrics(result)[0].expression.dialects[0].expression == "AVG(orders.price)"

    # --- RATIO ---

    def test_ratio_metric_inlines_sub_expressions(self, snapshot: SnapshotAssertion) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("order_count", agg=AggregationType.COUNT, expr="order_id"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        order_count_m = _simple_metric("order_count", "order_count")
        arpu = PydanticMetric(
            name="arpu",
            description=None,
            type=MetricType.RATIO,
            type_params=PydanticMetricTypeParams(
                numerator=PydanticMetricInput(name="revenue"),
                denominator=PydanticMetricInput(name="order_count"),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter()
            .convert(_manifest(semantic_models=[sm], metrics=[revenue_m, order_count_m, arpu]))
            .output
        )

        arpu_osi = next(m for m in _osi_metrics(result) if m.name == "arpu")
        assert arpu_osi.expression.dialects[0].expression == (
            "(SUM(orders.amount)) / (SUM(CASE WHEN orders.order_id IS NOT NULL THEN 1 ELSE 0 END))"
        )
        assert result.to_osi_yaml() == snapshot

    # --- DERIVED ---

    def test_derived_metric_inlines_sub_expressions(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("cost", agg=AggregationType.SUM, expr="cost_amount"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        cost_m = _simple_metric("cost", "cost")
        profit = PydanticMetric(
            name="profit",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="revenue - cost",
                metrics=[
                    PydanticMetricInput(name="revenue"),
                    PydanticMetricInput(name="cost"),
                ],
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[revenue_m, cost_m, profit])).output
        )

        profit_osi = next(m for m in _osi_metrics(result) if m.name == "profit")
        assert profit_osi.expression.dialects[0].expression == "SUM(orders.amount) - SUM(orders.cost_amount)"

    def test_derived_metric_uses_alias_for_substitution(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("cost", agg=AggregationType.SUM, expr="cost_amount"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        cost_m = _simple_metric("cost", "cost")
        profit = PydanticMetric(
            name="profit",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="r - c",
                metrics=[
                    PydanticMetricInput(name="revenue", alias="r"),
                    PydanticMetricInput(name="cost", alias="c"),
                ],
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[revenue_m, cost_m, profit])).output
        )

        profit_osi = next(m for m in _osi_metrics(result) if m.name == "profit")
        assert profit_osi.expression.dialects[0].expression == "SUM(orders.amount) - SUM(orders.cost_amount)"

    def test_derived_metric_nested(self, snapshot: SnapshotAssertion) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("cost", agg=AggregationType.SUM, expr="cost_amount"),
                _measure("expenses", agg=AggregationType.SUM, expr="expense_amount"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        cost_m = _simple_metric("cost", "cost")
        expenses_m = _simple_metric("expenses", "expenses")
        gross_profit = PydanticMetric(
            name="gross_profit",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="revenue - cost",
                metrics=[
                    PydanticMetricInput(name="revenue"),
                    PydanticMetricInput(name="cost"),
                ],
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        net_profit = PydanticMetric(
            name="net_profit",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="gross_profit - expenses",
                metrics=[
                    PydanticMetricInput(name="gross_profit"),
                    PydanticMetricInput(name="expenses"),
                ],
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter()
            .convert(
                _manifest(
                    semantic_models=[sm],
                    metrics=[revenue_m, cost_m, expenses_m, gross_profit, net_profit],
                )
            )
            .output
        )

        net_osi = next(m for m in _osi_metrics(result) if m.name == "net_profit")
        assert (
            net_osi.expression.dialects[0].expression
            == "(SUM(orders.amount) - SUM(orders.cost_amount)) - SUM(orders.expense_amount)"
        )
        assert result.to_osi_yaml() == snapshot

    def test_derived_metric_ref_not_corrupted_by_prefix_match(self) -> None:
        """Substituting 'revenue' must not corrupt 'revenue_adjusted' when it appears in the same expr."""
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("revenue_adjusted", agg=AggregationType.SUM, expr="adjusted_amount"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        revenue_adjusted_m = _simple_metric("revenue_adjusted", "revenue_adjusted")
        derived = PydanticMetric(
            name="revenue_delta",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="revenue - revenue_adjusted",
                metrics=[
                    PydanticMetricInput(name="revenue"),
                    PydanticMetricInput(name="revenue_adjusted"),
                ],
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter()
            .convert(_manifest(semantic_models=[sm], metrics=[revenue_m, revenue_adjusted_m, derived]))
            .output
        )

        derived_osi = next(m for m in _osi_metrics(result) if m.name == "revenue_delta")
        assert derived_osi.expression.dialects[0].expression == "SUM(orders.amount) - SUM(orders.adjusted_amount)"

    # --- CUMULATIVE ---

    def test_cumulative_metric_uses_base_aggregation(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        cumulative = PydanticMetric(
            name="cumulative_revenue",
            description=None,
            type=MetricType.CUMULATIVE,
            type_params=PydanticMetricTypeParams(
                measure=PydanticMetricInputMeasure(name="revenue"),
                cumulative_type_params=PydanticCumulativeTypeParams(
                    window=PydanticMetricTimeWindow(count=7, granularity="day"),
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[cumulative])).output

        metrics = _osi_metrics(result)
        assert len(metrics) == 1
        assert metrics[0].name == "cumulative_revenue"
        assert metrics[0].expression.dialects[0].expression == "SUM(orders.amount)"

    def test_cumulative_metric_via_sub_metric_reference(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        base = PydanticMetric(
            name="total_revenue",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(measure=PydanticMetricInputMeasure(name="revenue")),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        cumulative = PydanticMetric(
            name="cumulative_revenue",
            description=None,
            type=MetricType.CUMULATIVE,
            type_params=PydanticMetricTypeParams(
                cumulative_type_params=PydanticCumulativeTypeParams(
                    metric=PydanticMetricInput(name="total_revenue"),
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[base, cumulative])).output

        cumulative_osi = next(m for m in _osi_metrics(result) if m.name == "cumulative_revenue")
        assert cumulative_osi.expression.dialects[0].expression == "SUM(orders.amount)"

    # --- Edge cases ---

    def test_no_metrics_produces_no_osi_metrics(self) -> None:
        sm = semantic_model_with_guaranteed_meta(name="orders")
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output

        assert result.semantic_model[0].metrics is None

    def test_multiple_metrics_all_converted(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("order_count", agg=AggregationType.COUNT, expr="order_id"),
            ],
        )
        result = (
            MSIToOSIConverter()
            .convert(
                _manifest(
                    semantic_models=[sm],
                    metrics=[
                        _simple_metric("revenue", "revenue"),
                        _simple_metric("order_count", "order_count"),
                    ],
                )
            )
            .output
        )

        metrics = _osi_metrics(result)
        assert len(metrics) == 2
        assert {m.name for m in metrics} == {"revenue", "order_count"}

    def test_conversion_metric_skipped(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("visits", agg=AggregationType.COUNT, expr="visit_id"),
                _measure("purchases", agg=AggregationType.COUNT, expr="purchase_id"),
            ],
        )
        conversion = PydanticMetric(
            name="purchase_rate",
            description=None,
            type=MetricType.CONVERSION,
            type_params=PydanticMetricTypeParams(
                conversion_type_params=PydanticConversionTypeParams(
                    base_measure=PydanticMetricInputMeasure(name="visits"),
                    conversion_measure=PydanticMetricInputMeasure(name="purchases"),
                    entity="user",
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[conversion])).output

        assert result.semantic_model[0].metrics is None


class TestConverterIssues:
    def test_conversion_metric_emits_issue(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("visits", agg=AggregationType.COUNT, expr="visit_id"),
                _measure("purchases", agg=AggregationType.COUNT, expr="purchase_id"),
            ],
        )
        conversion = PydanticMetric(
            name="purchase_rate",
            description=None,
            type=MetricType.CONVERSION,
            type_params=PydanticMetricTypeParams(
                conversion_type_params=PydanticConversionTypeParams(
                    base_measure=PydanticMetricInputMeasure(name="visits"),
                    conversion_measure=PydanticMetricInputMeasure(name="purchases"),
                    entity="user",
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[conversion]))

        dropped = [i for i in result.issues if i.issue_type == ConverterIssueType.CONVERSION_METRIC_DROPPED]
        assert len(dropped) == 1
        assert dropped[0].element_name == "purchase_rate"

    def test_private_metric_emits_issue(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        private_metric = PydanticMetric(
            name="revenue_internal",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(
                measure=PydanticMetricInputMeasure(name="revenue"),
                is_private=True,
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[private_metric]))

        assert len(result.issues) == 1
        assert result.issues[0].issue_type == ConverterIssueType.PRIVATE_METRIC_DROPPED
        assert result.issues[0].element_name == "revenue_internal"

    def test_natural_entity_emits_issue(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="users",
            entities=[_entity("user", entity_type=EntityType.NATURAL, expr="user_id")],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm]))

        assert len(result.issues) == 1
        assert result.issues[0].issue_type == ConverterIssueType.NATURAL_ENTITY_DROPPED
        assert result.issues[0].element_name == "user"

    def test_cumulative_metric_emits_issue(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        base = _simple_metric("revenue", "revenue")
        cumulative = PydanticMetric(
            name="cumulative_revenue",
            description=None,
            type=MetricType.CUMULATIVE,
            type_params=PydanticMetricTypeParams(
                measure=PydanticMetricInputMeasure(name="revenue"),
                cumulative_type_params=PydanticCumulativeTypeParams(
                    window=PydanticMetricTimeWindow(count=7, granularity="day"),
                ),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[base, cumulative]))

        cumulative_issues = [i for i in result.issues if i.issue_type == ConverterIssueType.CUMULATIVE_SEMANTICS_LOSS]
        assert len(cumulative_issues) == 1
        assert cumulative_issues[0].element_name == "cumulative_revenue"


class TestFilterRendering:
    """Unit tests for the Jinja → SQL rendering of where-filter templates."""

    def test_plain_sql_passthrough(self) -> None:
        assert _render_filter_template("status = 'paid'") == "status = 'paid'"

    def test_dimension_reference(self) -> None:
        assert _render_filter_template("{{ Dimension('order__status') }} = 'paid'") == "order__status = 'paid'"

    def test_dimension_with_grain(self) -> None:
        result = _render_filter_template("{{ Dimension('order__ds').grain('day') }} >= '2023-01-01'")
        assert result == "order__ds__day >= '2023-01-01'"

    def test_time_dimension_with_grain_arg(self) -> None:
        result = _render_filter_template("{{ TimeDimension('order__ds', 'week') }} >= '2023-01-01'")
        assert result == "order__ds__week >= '2023-01-01'"

    def test_time_dimension_without_grain(self) -> None:
        assert _render_filter_template("{{ TimeDimension('metric_time') }} IS NOT NULL") == "metric_time IS NOT NULL"

    def test_entity_reference(self) -> None:
        assert _render_filter_template("{{ Entity('user') }} != 'bot'") == "user != 'bot'"

    def test_metric_reference(self) -> None:
        assert _render_filter_template("{{ Metric('revenue') }} > 0") == "revenue > 0"


class TestMetricFilterFlattening:
    def test_metric_level_filter_inlines_case_when(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = PydanticMetric(
            name="paid_revenue",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(measure=PydanticMetricInputMeasure(name="revenue")),
            filter=_filter("status = 'paid'"),
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert (
            _osi_metrics(result)[0].expression.dialects[0].expression
            == "SUM(CASE WHEN status = 'paid' THEN orders.amount END)"
        )

    def test_measure_level_filter_inlines_case_when(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = PydanticMetric(
            name="paid_revenue",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(
                measure=PydanticMetricInputMeasure(name="revenue", filter=_filter("status = 'paid'")),
            ),
            filter=None,
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert (
            _osi_metrics(result)[0].expression.dialects[0].expression
            == "SUM(CASE WHEN status = 'paid' THEN orders.amount END)"
        )

    def test_metric_and_measure_filters_combined_with_and(self, snapshot: SnapshotAssertion) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = PydanticMetric(
            name="paid_intl_revenue",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(
                measure=PydanticMetricInputMeasure(name="revenue", filter=_filter("status = 'paid'")),
            ),
            filter=_filter("region = 'intl'"),
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert _osi_metrics(result)[0].expression.dialects[0].expression == (
            "SUM(CASE WHEN (status = 'paid') AND (region = 'intl') THEN orders.amount END)"
        )
        assert result.to_osi_yaml() == snapshot

    def test_jinja_dimension_reference_rendered(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        metric = PydanticMetric(
            name="us_revenue",
            description=None,
            type=MetricType.SIMPLE,
            type_params=PydanticMetricTypeParams(measure=PydanticMetricInputMeasure(name="revenue")),
            filter=_filter("{{ Dimension('order__country') }} = 'US'"),
            metadata=default_meta(),
            config=None,
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[metric])).output

        assert (
            _osi_metrics(result)[0].expression.dialects[0].expression
            == "SUM(CASE WHEN order__country = 'US' THEN orders.amount END)"
        )

    def test_ratio_metric_filter_propagated_to_both_sides(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("order_count", agg=AggregationType.COUNT, expr="order_id"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        order_count_m = _simple_metric("order_count", "order_count")
        arpu = PydanticMetric(
            name="paid_arpu",
            description=None,
            type=MetricType.RATIO,
            type_params=PydanticMetricTypeParams(
                numerator=PydanticMetricInput(name="revenue"),
                denominator=PydanticMetricInput(name="order_count"),
            ),
            filter=_filter("status = 'paid'"),
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter()
            .convert(_manifest(semantic_models=[sm], metrics=[revenue_m, order_count_m, arpu]))
            .output
        )

        paid_arpu = next(m for m in _osi_metrics(result) if m.name == "paid_arpu")
        assert paid_arpu.expression.dialects[0].expression == (
            "(SUM(CASE WHEN status = 'paid' THEN orders.amount END))"
            " / "
            "(SUM(CASE WHEN status = 'paid' THEN CASE WHEN orders.order_id IS NOT NULL THEN 1 ELSE 0 END END))"
        )

    def test_derived_metric_filter_propagated_to_sub_expressions(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[
                _measure("revenue", agg=AggregationType.SUM, expr="amount"),
                _measure("cost", agg=AggregationType.SUM, expr="cost_amount"),
            ],
        )
        revenue_m = _simple_metric("revenue", "revenue")
        cost_m = _simple_metric("cost", "cost")
        profit = PydanticMetric(
            name="paid_profit",
            description=None,
            type=MetricType.DERIVED,
            type_params=PydanticMetricTypeParams(
                expr="revenue - cost",
                metrics=[PydanticMetricInput(name="revenue"), PydanticMetricInput(name="cost")],
            ),
            filter=_filter("status = 'paid'"),
            metadata=default_meta(),
            config=None,
        )
        result = (
            MSIToOSIConverter().convert(_manifest(semantic_models=[sm], metrics=[revenue_m, cost_m, profit])).output
        )

        paid_profit = next(m for m in _osi_metrics(result) if m.name == "paid_profit")
        assert paid_profit.expression.dialects[0].expression == (
            "SUM(CASE WHEN status = 'paid' THEN orders.amount END)"
            " - "
            "SUM(CASE WHEN status = 'paid' THEN orders.cost_amount END)"
        )

    def test_no_filter_produces_plain_expression(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            measures=[_measure("revenue", agg=AggregationType.SUM, expr="amount")],
        )
        result = (
            MSIToOSIConverter()
            .convert(_manifest(semantic_models=[sm], metrics=[_simple_metric("revenue", "revenue")]))
            .output
        )

        assert _osi_metrics(result)[0].expression.dialects[0].expression == "SUM(orders.amount)"


class TestOSIJsonSerialization:
    def test_to_osi_json_produces_valid_json(self) -> None:
        sm = semantic_model_with_guaranteed_meta(
            name="orders",
            description="Order data",
            dimensions=[_dimension("ds", dim_type=DimensionType.TIME, granularity=TimeGranularity.DAY)],
            measures=[_measure("revenue", expr="amount")],
            entities=[_entity("order_id", entity_type=EntityType.PRIMARY)],
        )
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm]), osi_model_name="my_project").output
        parsed = json.loads(result.to_osi_json())

        assert parsed["version"] == "0.2.0.dev0"
        assert len(parsed["semantic_model"]) == 1
        assert parsed["semantic_model"][0]["name"] == "my_project"

    def test_to_osi_json_excludes_none_fields(self) -> None:
        sm = semantic_model_with_guaranteed_meta(name="orders")
        result = MSIToOSIConverter().convert(_manifest(semantic_models=[sm])).output
        parsed = json.loads(result.to_osi_json())

        dataset = parsed["semantic_model"][0]["datasets"][0]
        assert "primary_key" not in dataset
        assert "unique_keys" not in dataset
        assert "fields" not in dataset
