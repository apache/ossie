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

from ossie_lightdash import ConverterIssueType, LightdashToOSIConverter

SCHEMA_YML = {
    "version": 2,
    "models": [
        {
            "name": "orders",
            "description": "One row per order",
            "meta": {
                "joins": [
                    {
                        "join": "customers",
                        "sql_on": "${orders.customer_id} = ${customers.customer_id}",
                    }
                ],
                "metrics": {
                    "conversion_rate": {
                        "type": "number",
                        "label": "Conversion rate",
                        "format": "percent",
                        "round": 1,
                        "sql": "SUM(${TABLE}.completed_count) / NULLIF(SUM(${TABLE}.total_count), 0)",
                    }
                },
            },
            "columns": [
                {
                    "name": "order_date",
                    "description": "Date the order was placed",
                    "meta": {"dimension": {"label": "Order date", "type": "date"}},
                },
                {
                    "name": "status",
                    "meta": {"dimension": {"label": "Status", "type": "string"}},
                },
                {
                    "name": "amount",
                    "description": "Order amount",
                    "meta": {
                        "metrics": {
                            "total_amount": {
                                "type": "sum",
                                "label": "Total amount",
                                "format": "usd",
                            },
                            "median_amount": {"type": "median"},
                            "p90_amount": {"type": "percentile", "percentile": 90},
                        }
                    },
                },
                {"name": "completed_count"},
                {"name": "total_count"},
                {
                    "name": "customer_id",
                    "meta": {
                        "metrics": {
                            "unique_customers": {"type": "count_distinct"},
                        }
                    },
                },
            ],
        },
        {
            "name": "customers",
            "columns": [{"name": "customer_id"}],
        },
    ],
}


def _metric(document, name):
    return next(m for m in document.semantic_model[0].metrics if m.name == name)


def _lightdash_data(element):
    for extension in element.custom_extensions or []:
        if extension.vendor_name == "lightdash":
            return json.loads(extension.data)
    return {}


class TestLightdashToOSI:
    def test_dataset_source_is_qualified(self):
        result = LightdashToOSIConverter().convert(
            SCHEMA_YML, database="analytics_db", schema="marts"
        )
        dataset = result.output.semantic_model[0].datasets[0]
        assert dataset.source == "analytics_db.marts.orders"
        assert not any(
            issue.issue_type is ConverterIssueType.SOURCE_UNQUALIFIED
            for issue in result.issues
        )

    def test_missing_schema_is_reported(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML)
        dataset = result.output.semantic_model[0].datasets[0]
        assert dataset.source == "orders"
        assert any(
            issue.issue_type is ConverterIssueType.SOURCE_UNQUALIFIED
            for issue in result.issues
        )

    def test_time_dimension(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert field.name == "order_date"
        assert field.label == "Order date"
        assert field.dimension is not None and field.dimension.is_time
        assert field.description == "Date the order was placed"

    def test_typed_metric_becomes_aggregation_expression(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "total_amount")
        assert metric.expression.dialects[0].expression == "SUM(orders.amount)"
        assert _lightdash_data(metric) == {"label": "Total amount", "format": "usd"}

    def test_count_distinct_metric(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "unique_customers")
        assert (
            metric.expression.dialects[0].expression
            == "COUNT(DISTINCT orders.customer_id)"
        )

    def test_percentile_metric_keeps_type_in_extension(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "p90_amount")
        assert _lightdash_data(metric) == {"type": "percentile", "percentile": 90}

    def test_sql_metric_expression_is_rewritten(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "conversion_rate")
        assert (
            metric.expression.dialects[0].expression
            == "SUM(orders.completed_count) / NULLIF(SUM(orders.total_count), 0)"
        )
        assert _lightdash_data(metric) == {
            "label": "Conversion rate",
            "format": "percent",
            "round": 1,
        }

    def test_join_becomes_relationship(self):
        result = LightdashToOSIConverter().convert(SCHEMA_YML, schema="marts")
        relationship = result.output.semantic_model[0].relationships[0]
        assert relationship.from_dataset == "orders"
        assert relationship.to == "customers"
        assert relationship.from_columns == ["customer_id"]
        assert relationship.to_columns == ["customer_id"]

    def test_percentile_with_sql_keeps_type_in_extension(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "metrics": {
                            "p90_custom": {
                                "type": "percentile",
                                "percentile": 90,
                                "sql": "${TABLE}.amount - ${TABLE}.discount",
                            }
                        }
                    },
                    "columns": [],
                }
            ]
        }
        result = LightdashToOSIConverter().convert(schema_yml, schema="marts")
        metric = _metric(result.output, "p90_custom")
        assert (
            metric.expression.dialects[0].expression
            == "orders.amount - orders.discount"
        )
        assert _lightdash_data(metric) == {"type": "percentile", "percentile": 90}

    def test_joined_table_references_become_cross_dataset(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "metrics": {
                            "orders_per_customer": {
                                "type": "number",
                                "sql": "COUNT(${TABLE}.order_id) / COUNT(DISTINCT ${customers.customer_id})",
                            }
                        }
                    },
                    "columns": [],
                }
            ]
        }
        result = LightdashToOSIConverter().convert(schema_yml, schema="marts")
        metric = _metric(result.output, "orders_per_customer")
        assert (
            metric.expression.dialects[0].expression
            == "COUNT(orders.order_id) / COUNT(DISTINCT customers.customer_id)"
        )

    def test_unparseable_join_is_reported(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "joins": [{"join": "customers", "sql_on": "1 = 1"}],
                    },
                    "columns": [],
                }
            ]
        }
        result = LightdashToOSIConverter().convert(schema_yml, schema="marts")
        assert result.output.semantic_model[0].relationships is None
        assert any(
            issue.issue_type is ConverterIssueType.JOIN_SQL_UNPARSED
            for issue in result.issues
        )
