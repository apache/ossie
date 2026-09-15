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

from ossie import OssieDataType, OssieDialect

from ossie_lightdash import ConverterIssueType, LightdashToOssieConverter

SCHEMA_YML = {
    "version": 2,
    "models": [
        {
            "name": "orders",
            "description": "One row per order",
            "meta": {
                "primary_key": "order_id",
                "ai_hint": ["Orders placed in the web shop.", "One row per order."],
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
                    "meta": {
                        "dimension": {
                            "label": "Status",
                            "type": "string",
                            "ai_hint": "Order lifecycle stage.",
                        }
                    },
                },
                {
                    "name": "updated_at",
                    "meta": {
                        "dimension": {"type": "timestamp", "time_intervals": "OFF"}
                    },
                },
                {
                    "name": "shipped_at",
                    "meta": {
                        "dimension": {"type": "timestamp", "time_intervals": ["DAY", "MONTH"]}
                    },
                },
                {
                    "name": "amount",
                    "description": "Order amount",
                    "meta": {
                        "dimension": {"type": "number"},
                        "metrics": {
                            "total_amount": {
                                "type": "sum",
                                "label": "Total amount",
                                "format": "usd",
                                "ai_hint": "Revenue before refunds.",
                            },
                            "latest_amount": {"type": "max"},
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
            "meta": {"primary_key": ["customer_id", "region"]},
            "columns": [{"name": "customer_id"}],
        },
    ],
}


def _raw_lightdash_data(element):
    for extension in element.custom_extensions or []:
        if extension.vendor_name.upper() == "LIGHTDASH":
            return json.loads(extension.data)
    return {}


def _metric(document, name):
    """Look a metric up by its Lightdash name (stashed in the extension)."""
    return next(
        m
        for m in document.semantic_model[0].metrics
        if _raw_lightdash_data(m).get("name") == name
    )


def _lightdash_data(element):
    data = _raw_lightdash_data(element)
    data.pop("name", None)
    data.pop("model", None)
    return data


class TestLightdashToOssie:
    def test_dataset_source_is_qualified(self):
        result = LightdashToOssieConverter().convert(
            SCHEMA_YML, database="analytics_db", schema="marts"
        )
        dataset = result.output.semantic_model[0].datasets[0]
        assert dataset.source == "analytics_db.marts.orders"
        assert not any(
            issue.issue_type is ConverterIssueType.SOURCE_UNQUALIFIED
            for issue in result.issues
        )

    def test_missing_schema_is_reported(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML)
        dataset = result.output.semantic_model[0].datasets[0]
        assert dataset.source == "orders"
        assert any(
            issue.issue_type is ConverterIssueType.SOURCE_UNQUALIFIED
            for issue in result.issues
        )

    def test_time_dimension(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert field.name == "order_date"
        assert field.label == "Order date"
        assert field.description == "Date the order was placed"
        assert field.dimension is not None
        # The Lightdash type becomes a datatype; `is_time` is an Ossie role
        # marker with no Lightdash source, so it stays unset.
        assert field.datatype is OssieDataType.DATE
        assert field.dimension.is_time is None
        withdrawn = result.output.semantic_model[0].datasets[0].fields[2]
        assert withdrawn.name == "updated_at"
        assert withdrawn.dimension.is_time is False
        assert _lightdash_data(withdrawn) == {"type": "timestamp"}
        # A custom interval list is not a role marker: it stays in the extension.
        custom = result.output.semantic_model[0].datasets[0].fields[3]
        assert custom.dimension.is_time is None
        assert _lightdash_data(custom) == {"type": "timestamp", "time_intervals": ["DAY", "MONTH"]}

    def test_dimension_types_become_datatypes(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        by_name = {
            field.name: field
            for field in result.output.semantic_model[0].datasets[0].fields
        }
        assert by_name["status"].datatype is OssieDataType.STRING
        assert by_name["order_date"].datatype is OssieDataType.DATE

    def test_typed_metric_becomes_aggregation_expression(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "total_amount")
        assert metric.expression.dialects[0].expression == "SUM(orders.amount)"
        assert _lightdash_data(metric) == {"label": "Total amount", "format": "usd"}
        assert metric.ai_context == "Revenue before refunds."

    def test_count_distinct_metric(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "unique_customers")
        assert (
            metric.expression.dialects[0].expression
            == "COUNT(DISTINCT orders.customer_id)"
        )

    def test_percentile_metric_becomes_percentile_cont(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "p90_amount")
        assert (
            metric.expression.dialects[0].expression
            == "PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY orders.amount)"
        )
        assert _lightdash_data(metric) == {}

    def test_sql_metric_expression_is_rewritten(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
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
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        relationship = result.output.semantic_model[0].relationships[0]
        assert relationship.from_dataset == "orders"
        assert relationship.to == "customers"
        assert relationship.from_columns == ["customer_id"]
        assert relationship.to_columns == ["customer_id"]

    def test_percentile_with_sql_orders_by_the_expression(self):
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
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        metric = _metric(result.output, "p90_custom")
        assert (
            metric.expression.dialects[0].expression
            == "PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY orders.amount - orders.discount)"
        )
        assert _lightdash_data(metric) == {}

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
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        metric = _metric(result.output, "orders_per_customer")
        assert (
            metric.expression.dialects[0].expression
            == "COUNT(orders.order_id) / COUNT(DISTINCT customers.customer_id)"
        )

    def test_model_metric_without_sql_is_skipped(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {"metrics": {"broken_metric": {"type": "number"}}},
                    "columns": [],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert result.output.semantic_model[0].metrics is None
        assert any(
            issue.issue_type is ConverterIssueType.METRIC_SQL_MISSING
            and issue.element_name == "broken_metric"
            for issue in result.issues
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
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert result.output.semantic_model[0].relationships is None
        assert any(
            issue.issue_type is ConverterIssueType.JOIN_SQL_UNPARSED
            for issue in result.issues
        )

    def test_typed_metric_with_sql_aggregates_the_expression(self):
        schema_yml = {
            "models": [
                {
                    "name": "work_orders",
                    "columns": [
                        {
                            "name": "status",
                            "meta": {
                                "metrics": {
                                    "completion_rate": {
                                        "type": "average",
                                        "sql": "CASE WHEN ${status} = 'Completed' THEN 1 ELSE 0 END",
                                    },
                                    "distinct_total": {"type": "sum_distinct"},
                                }
                            },
                        }
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert (
            _metric(result.output, "completion_rate").expression.dialects[0].expression
            == "AVG(CASE WHEN work_orders.status = 'Completed' THEN 1 ELSE 0 END)"
        )
        distinct_total = _metric(result.output, "distinct_total")
        assert (
            distinct_total.expression.dialects[0].expression
            == "SUM(DISTINCT work_orders.status)"
        )
        assert _lightdash_data(distinct_total) == {}

    def test_metric_reference_is_inlined(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "metrics": {
                            "amount_per_customer": {
                                "type": "number",
                                "sql": "${total_amount} / NULLIF(${unique_customers}, 0)",
                            }
                        }
                    },
                    "columns": [
                        {"name": "amount", "meta": {"metrics": {"total_amount": {"type": "sum"}}}},
                        {
                            "name": "customer_id",
                            "meta": {"metrics": {"unique_customers": {"type": "count_distinct"}}},
                        },
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert (
            _metric(result.output, "amount_per_customer").expression.dialects[0].expression
            == "(SUM(orders.amount)) / NULLIF((COUNT(DISTINCT orders.customer_id)), 0)"
        )
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.METRIC_REFERENCE_INLINED
        ] == ["amount_per_customer", "amount_per_customer"]

    def test_bare_field_references_resolve_to_the_dataset(self):
        schema_yml = {
            "models": [
                {
                    "name": "customers",
                    "columns": [
                        {"name": "first_name", "meta": {"dimension": {"type": "string"}}},
                        {
                            "name": "full_name",
                            "meta": {
                                "dimension": {
                                    "type": "string",
                                    "sql": "${first_name} || ' ' || ${TABLE}.last_name",
                                }
                            },
                        },
                        {
                            "name": "order_count",
                            "meta": {
                                "dimension": {
                                    "type": "number",
                                    "sql": "(SELECT COUNT(*) FROM orders WHERE orders.customer_id = ${TABLE}.customer_id)",
                                }
                            },
                        },
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        by_name = {
            field.name: field.expression.dialects[0].expression
            for field in result.output.semantic_model[0].datasets[0].fields
        }
        assert by_name["full_name"] == "customers.first_name || ' ' || customers.last_name"
        assert by_name["order_count"] == (
            "(SELECT COUNT(*) FROM orders WHERE orders.customer_id = customers.customer_id)"
        )

    def test_parameter_references_skip_the_element(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "metrics": {
                            "my_orders": {
                                "type": "number",
                                "sql": "SUM(CASE WHEN ${TABLE}.owner = ${ld.user.email} THEN 1 END)",
                            }
                        }
                    },
                    "columns": [
                        {
                            "name": "is_recent",
                            "meta": {
                                "dimension": {
                                    "type": "boolean",
                                    "sql": "${TABLE}.order_date >= ${lightdash.parameters.start_date}",
                                }
                            },
                        },
                        {
                            "name": "status_label",
                            "meta": {
                                "dimension": {
                                    "type": "string",
                                    "sql": "{% if ld.query.filters contains 'orders.status' %} 'filtered' {% else %} ${TABLE}.status {% endif %}",
                                }
                            },
                        },
                        {"name": "order_date", "meta": {"dimension": {"type": "date"}}},
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        dataset = result.output.semantic_model[0].datasets[0]
        assert [field.name for field in dataset.fields] == ["order_date"]
        assert result.output.semantic_model[0].metrics is None
        assert sorted(
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.EXPRESSION_NOT_PORTABLE
        ) == ["is_recent", "my_orders", "status_label"]

    def test_aliased_joins_become_relationships(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "joins": [
                            {
                                "join": "date_dim",
                                "alias": "sold_date",
                                "sql_on": "${orders.sold_date_id} = ${sold_date.date_id}",
                                "relationship": "many-to-one",
                            },
                            {
                                "join": "date_dim",
                                "alias": "return_date",
                                "sql_on": "${orders.return_date_id} = ${return_date.date_id}",
                            },
                        ]
                    },
                    "columns": [
                        {
                            "name": "sold_year",
                            "meta": {"dimension": {"type": "number", "sql": "${sold_date.year}"}},
                        }
                    ],
                },
                {"name": "date_dim", "columns": [{"name": "date_id"}, {"name": "year"}]},
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        relationships = result.output.semantic_model[0].relationships
        assert [(r.name, r.to, r.from_columns, r.to_columns) for r in relationships] == [
            ("orders_to_sold_date", "date_dim", ["sold_date_id"], ["date_id"]),
            ("orders_to_return_date", "date_dim", ["return_date_id"], ["date_id"]),
        ]
        assert _lightdash_data(relationships[0]) == {
            "alias": "sold_date",
            "relationship": "many-to-one",
        }
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert field.expression.dialects[0].expression == "date_dim.year"
        assert any(
            issue.issue_type is ConverterIssueType.ALIAS_REFERENCE_FLATTENED
            and issue.element_name == "sold_year"
            for issue in result.issues
        )

    def test_expressions_carry_the_warehouse_dialect(self):
        result = LightdashToOssieConverter(OssieDialect.BIGQUERY).convert(
            SCHEMA_YML, schema="marts"
        )
        metric = _metric(result.output, "total_amount")
        assert [d.dialect for d in metric.expression.dialects] == [OssieDialect.BIGQUERY]
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert field.expression.dialects[0].dialect is OssieDialect.BIGQUERY

    def test_primary_key_and_ai_hint_become_dataset_attributes(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        orders, customers = result.output.semantic_model[0].datasets
        assert orders.primary_key == ["order_id"]
        assert orders.ai_context == "Orders placed in the web shop.\nOne row per order."
        assert customers.primary_key == ["customer_id", "region"]
        status = next(field for field in orders.fields if field.name == "status")
        assert status.ai_context == "Order lifecycle stage."
        assert _lightdash_data(status) == {"type": "string"}

    def test_metric_datatypes_follow_the_aggregation(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        assert _metric(result.output, "unique_customers").datatype is OssieDataType.INTEGER
        assert _metric(result.output, "total_amount").datatype is OssieDataType.DECIMAL
        assert _metric(result.output, "latest_amount").datatype is OssieDataType.DECIMAL
        assert _metric(result.output, "conversion_rate").datatype is None

    def test_config_meta_is_read_and_wins_over_meta(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {"primary_key": "legacy_id"},
                    "config": {
                        "meta": {
                            "primary_key": "order_id",
                            "joins": [
                                {
                                    "join": "customers",
                                    "sql_on": "${orders.customer_id} = ${customers.customer_id}",
                                }
                            ],
                        }
                    },
                    "columns": [
                        {
                            "name": "amount",
                            "meta": {"dimension": {"type": "number", "label": "Amount"}},
                            "config": {
                                "meta": {
                                    "dimension": {"label": "Order amount"},
                                    "metrics": {"total_amount": {"type": "sum"}},
                                }
                            },
                        }
                    ],
                },
                {"name": "customers", "columns": [{"name": "customer_id"}]},
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        orders = result.output.semantic_model[0].datasets[0]
        assert orders.primary_key == ["order_id"]
        assert orders.fields[0].label == "Order amount"
        assert orders.fields[0].datatype is OssieDataType.DECIMAL
        assert _metric(result.output, "total_amount").expression.dialects[0].expression == (
            "SUM(orders.amount)"
        )
        assert result.output.semantic_model[0].relationships[0].to == "customers"

    def test_metric_names_are_qualified_with_the_model(self):
        result = LightdashToOssieConverter().convert(SCHEMA_YML, schema="marts")
        metric = _metric(result.output, "total_amount")
        assert metric.name == "orders_total_amount"
        assert _raw_lightdash_data(metric)["name"] == "total_amount"
        assert _raw_lightdash_data(metric)["model"] == "orders"
        assert [m.name for m in result.output.semantic_model[0].metrics][:3] == [
            "orders_total_amount",
            "orders_latest_amount",
            "orders_median_amount",
        ]

    def test_qualified_names_that_still_collide_are_suffixed(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "columns": [{"name": "amount", "meta": {"metrics": {"x_total": {"type": "sum"}}}}],
                },
                {
                    "name": "orders_x",
                    "columns": [{"name": "amount", "meta": {"metrics": {"total": {"type": "sum"}}}}],
                },
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert [m.name for m in result.output.semantic_model[0].metrics] == [
            "orders_x_total",
            "orders_x_total_2",
        ]
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.METRIC_NAME_COLLISION
        ] == ["total"]

    def test_seeds_are_datasets_and_joins_to_missing_models_are_skipped(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {
                        "joins": [
                            {"join": "order_statuses", "sql_on": "${orders.status_id} = ${order_statuses.id}"},
                            {"join": "not_in_this_file", "sql_on": "${orders.x} = ${not_in_this_file.x}"},
                        ]
                    },
                    "columns": [{"name": "status_id"}],
                }
            ],
            "seeds": [{"name": "order_statuses", "columns": [{"name": "id"}]}],
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        semantic_model = result.output.semantic_model[0]
        assert [dataset.name for dataset in semantic_model.datasets] == ["orders", "order_statuses"]
        assert [relationship.to for relationship in semantic_model.relationships] == ["order_statuses"]
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.JOIN_TARGET_UNKNOWN
        ] == ["orders -> not_in_this_file"]

    def test_bare_column_names_in_sql_are_qualified(self):
        schema_yml = {
            "models": [
                {
                    "name": "budgets",
                    "meta": {
                        "metrics": {
                            "use_percentage": {
                                "type": "number",
                                "sql": "SUM(budget_use) / NULLIF(SUM(budget_total), 0)",
                            },
                            "mobile_share": {
                                "type": "number",
                                "sql": "SUM(CASE WHEN device_type = 'device_type' THEN 1 END) / COUNT(*)",
                            },
                        }
                    },
                    "columns": [
                        {"name": "budget_use"},
                        {"name": "budget_total"},
                        {"name": "device_type"},
                        {"name": "count", "meta": {"dimension": {"sql": "COUNT(budget_use) OVER ()"}}},
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        assert _metric(result.output, "use_percentage").expression.dialects[0].expression == (
            "SUM(budgets.budget_use) / NULLIF(SUM(budgets.budget_total), 0)"
        )
        # The literal is untouched; the column reference is qualified.
        assert _metric(result.output, "mobile_share").expression.dialects[0].expression == (
            "SUM(CASE WHEN budgets.device_type = 'device_type' THEN 1 END) / COUNT(*)"
        )
        # A column named like a function is not qualified when called.
        fields = {f.name: f.expression.dialects[0].expression for f in result.output.semantic_model[0].datasets[0].fields}
        assert fields["count"] == "COUNT(budgets.budget_use) OVER ()"

    def test_chained_and_expression_joins_are_stashed_and_edges_derived(self):
        schema_yml = {
            "models": [
                {
                    "name": "queries",
                    "meta": {
                        "sql_filter": "${TABLE}.deleted = false",
                        "group_details": {"usage": {"label": "Usage"}},
                        "joins": [
                            {"join": "projects", "sql_on": "${queries.project_id} = ${projects.project_id}"},
                            {
                                "join": "organizations",
                                "sql_on": "${projects.organization_id} = ${organizations.organization_id}",
                            },
                            {
                                "join": "users",
                                "sql_on": "LOWER(${queries.user_email}) = ${users.email}",
                            },
                            {
                                "join": "warehouses",
                                "sql_on": "${queries.warehouse_id} = ${warehouses.id} AND ${warehouses.active}",
                            },
                        ],
                    },
                    "columns": [{"name": "project_id"}],
                },
                {
                    "name": "projects",
                    "meta": {
                        "joins": [
                            {
                                "join": "organizations",
                                "sql_on": "${projects.organization_id} = ${organizations.organization_id}",
                                "relationship": "many-to-one",
                            }
                        ]
                    },
                    "columns": [{"name": "project_id"}, {"name": "organization_id"}],
                },
                {"name": "organizations", "columns": [{"name": "organization_id"}]},
                {"name": "users", "columns": [{"name": "email"}]},
                {"name": "warehouses", "columns": [{"name": "id"}, {"name": "active"}]},
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        semantic_model = result.output.semantic_model[0]
        edges = [(r.from_dataset, r.to, r.from_columns, r.to_columns) for r in semantic_model.relationships]
        # The chained edge projects -> organizations is declared on projects
        # too; the declared one wins and carries its extras.
        assert edges == [
            ("queries", "projects", ["project_id"], ["project_id"]),
            ("queries", "warehouses", ["warehouse_id"], ["id"]),
            ("projects", "organizations", ["organization_id"], ["organization_id"]),
        ]
        assert _raw_lightdash_data(semantic_model.relationships[2]) == {"relationship": "many-to-one"}
        stash = _raw_lightdash_data(semantic_model.datasets[0])
        assert stash["sql_filter"] == "${TABLE}.deleted = false"
        assert stash["group_details"] == {"usage": {"label": "Usage"}}
        assert [join["join"] for join in stash["joins"]] == ["organizations", "users", "warehouses"]
        assert {
            (issue.issue_type.value, issue.element_name)
            for issue in result.issues
            if issue.issue_type in (ConverterIssueType.JOIN_STASHED, ConverterIssueType.JOIN_SQL_UNPARSED)
        } == {
            ("JOIN_STASHED", "queries -> organizations"),
            ("JOIN_SQL_UNPARSED", "queries -> users"),
            ("JOIN_STASHED", "queries -> warehouses"),
        }

    def test_chained_join_without_a_declared_edge_derives_one(self):
        schema_yml = {
            "models": [
                {
                    "name": "users",
                    "meta": {
                        "joins": [
                            {"join": "roles", "sql_on": "${users.id} = ${roles.user_id}"},
                            {"join": "projects", "sql_on": "${roles.project_id} = ${projects.id}"},
                        ]
                    },
                    "columns": [{"name": "id"}],
                },
                {"name": "roles", "columns": [{"name": "user_id"}, {"name": "project_id"}]},
                {"name": "projects", "columns": [{"name": "id"}]},
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        edges = [(r.from_dataset, r.to) for r in result.output.semantic_model[0].relationships]
        assert edges == [("users", "roles"), ("roles", "projects")]

    def test_column_meta_outside_dimension_is_stashed(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "columns": [
                        {
                            "name": "amount",
                            "meta": {
                                "dimension": {"type": "number"},
                                "additional_dimensions": {"amount_bucket": {"type": "string", "sql": "CASE WHEN ${TABLE}.amount > 100 THEN 'big' ELSE 'small' END"}},
                            },
                        }
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert _lightdash_data(field) == {
            "type": "number",
            "column_meta": {
                "additional_dimensions": {
                    "amount_bucket": {"type": "string", "sql": "CASE WHEN ${TABLE}.amount > 100 THEN 'big' ELSE 'small' END"}
                }
            },
        }

    def test_every_column_is_a_dimension_unless_hidden(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "columns": [
                        {"name": "plain"},
                        {"name": "shown", "meta": {"dimension": {"hidden": False, "type": "string"}}},
                        {"name": "hidden_key", "meta": {"dimension": {"hidden": True, "type": "number", "label": "Key"}}},
                    ],
                }
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        fields = {f.name: f for f in result.output.semantic_model[0].datasets[0].fields}
        assert fields["plain"].dimension is not None
        assert fields["shown"].dimension is not None
        assert _lightdash_data(fields["shown"]) == {"type": "string"}
        assert fields["hidden_key"].dimension is None
        assert fields["hidden_key"].datatype is OssieDataType.DECIMAL
        assert fields["hidden_key"].label == "Key"
        assert _lightdash_data(fields["hidden_key"]) == {"type": "number"}

    def test_filters_that_other_consumers_cannot_see_are_reported(self):
        schema_yml = {
            "models": [
                {
                    "name": "orders",
                    "meta": {"sql_filter": "${TABLE}.deleted = false"},
                    "columns": [
                        {
                            "name": "amount",
                            "meta": {
                                "metrics": {
                                    "paid_amount": {"type": "sum", "filters": [{"status": "paid"}]},
                                    "total_amount": {"type": "sum"},
                                }
                            },
                        }
                    ],
                },
                {"name": "customers", "meta": {"required_filters": [{"region": "EU"}]}, "columns": []},
            ]
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts")
        # The values are stashed for Lightdash, and the loss for everyone else is reported.
        paid = _metric(result.output, "paid_amount")
        assert paid.expression.dialects[0].expression == "SUM(orders.amount)"
        assert _lightdash_data(paid) == {"filters": [{"status": "paid"}]}
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.METRIC_FILTER_NOT_PORTABLE
        ] == ["paid_amount"]
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.ROW_FILTER_NOT_PORTABLE
        ] == ["orders", "customers"]

    def test_catalog_types_fill_the_gaps_but_never_override_authored_types(self):
        from ossie_lightdash.catalog import warehouse_type_to_datatype

        assert warehouse_type_to_datatype("INT64") is OssieDataType.INTEGER
        assert warehouse_type_to_datatype("NUMBER(38,0)") is OssieDataType.INTEGER
        assert warehouse_type_to_datatype("NUMBER(12,2)") is OssieDataType.DECIMAL
        assert warehouse_type_to_datatype("NUMERIC") is OssieDataType.DECIMAL
        assert warehouse_type_to_datatype("character varying(255)") is OssieDataType.STRING
        assert warehouse_type_to_datatype("TIMESTAMP_TZ(9)") is OssieDataType.DATE_TIME_TZ
        assert warehouse_type_to_datatype("ARRAY<STRING>") is None

        schema_yml = {
            "models": [
                {
                    "name": "results",
                    "columns": [
                        {"name": "points", "meta": {"dimension": {"type": "string"}}},
                        {"name": "position", "meta": {"dimension": {"label": "Position"}}},
                        {"name": "race_date"},
                        {"name": "payload"},
                        {"name": "laps", "meta": {"metrics": {"total_laps": {"type": "sum"}}}},
                    ],
                },
                {"name": "orphan", "columns": [{"name": "id"}]},
            ]
        }
        catalog = {
            "results": {
                "points": "FLOAT64",
                "position": "INT64",
                "race_date": "DATE",
                "payload": "STRUCT<a INT64>",
                "laps": "INT64",
            }
        }
        result = LightdashToOssieConverter().convert(schema_yml, schema="marts", catalog=catalog)
        fields = {f.name: f for f in result.output.semantic_model[0].datasets[0].fields}
        assert fields["points"].datatype is OssieDataType.STRING  # authored wins
        assert fields["position"].datatype is OssieDataType.INTEGER  # gap filled
        assert fields["race_date"].datatype is OssieDataType.DATE  # no meta at all
        assert fields["payload"].datatype is None  # outside the vocabulary
        # A catalog type also feeds the metric datatype.
        assert _metric(result.output, "total_laps").datatype is OssieDataType.DECIMAL
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.CATALOG_MODEL_MISSING
        ] == ["orphan"]

    def test_lightdash_model_file_is_read_like_dbt_meta(self):
        from ossie_lightdash.dbt_project import model_file_to_dbt_model

        model_file = {
            "type": "model",
            "name": "orders",
            "label": "Orders",
            "description": "One row per order",
            "sql_from": "SELECT * FROM marts.orders WHERE deleted = false",
            "primary_key": "order_id",
            "sql_filter": "${TABLE}.season >= 2025",
            "joins": [{"join": "customers", "sql_on": "${orders.customer_id} = ${customers.customer_id}"}],
            "metrics": {"aov": {"type": "number", "sql": "${total_amount} / NULLIF(${order_count}, 0)"}},
            "dimensions": [
                {"name": "order_id", "type": "number", "sql": "${TABLE}.order_id", "hidden": True,
                 "metrics": {"order_count": {"type": "count_distinct"}}},
                {"name": "amount", "type": "number", "sql": "${TABLE}.amount", "format": "usd",
                 "metrics": {"total_amount": {"type": "sum"}}},
                {"name": "customer_id", "type": "string", "sql": "${TABLE}.customer_id"},
                {"name": "amount_bucket", "type": "string", "sql": "CASE WHEN ${amount} > 100 THEN 'big' ELSE 'small' END"},
            ],
        }
        schema_yml = {"models": [model_file_to_dbt_model(model_file),
                                 {"name": "customers", "columns": [{"name": "customer_id"}]}]}
        result = LightdashToOssieConverter().convert(schema_yml, schema="ignored")
        orders = result.output.semantic_model[0].datasets[0]
        assert orders.source == "SELECT * FROM marts.orders WHERE deleted = false"
        assert orders.primary_key == ["order_id"]
        assert _raw_lightdash_data(orders) == {"label": "Orders", "sql_filter": "${TABLE}.season >= 2025"}
        fields = {f.name: f for f in orders.fields}
        assert fields["order_id"].dimension is None  # hidden
        assert fields["amount"].datatype is OssieDataType.DECIMAL
        assert fields["amount"].expression.dialects[0].expression == "amount"  # ${TABLE}.amount collapses
        assert _lightdash_data(fields["amount"]) == {"type": "number", "format": "usd"}
        assert fields["amount_bucket"].expression.dialects[0].expression == (
            "CASE WHEN orders.amount > 100 THEN 'big' ELSE 'small' END"
        )
        assert _metric(result.output, "aov").expression.dialects[0].expression == (
            "(SUM(orders.amount)) / NULLIF((COUNT(DISTINCT orders.order_id)), 0)"
        )
        assert result.output.semantic_model[0].relationships[0].to == "customers"
        assert not any(i.issue_type is ConverterIssueType.SOURCE_UNQUALIFIED for i in result.issues)
