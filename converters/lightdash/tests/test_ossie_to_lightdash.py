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

from ossie import (
    OssieAIContextObject,
    OssieCustomExtension,
    OssieDataType,
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
)

from ossie_lightdash import (
    ConverterIssueType,
    LightdashToOssieConverter,
    OssieToLightdashConverter,
)


def _ansi(expression: str) -> OssieExpression:
    return OssieExpression(
        dialects=[
            OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression=expression)
        ]
    )


def _document() -> OssieDocument:
    orders = OssieDataset(
        name="orders",
        source="analytics_db.marts.orders",
        description="One row per order",
        fields=[
            OssieField(
                name="order_date",
                expression=_ansi("order_date"),
                dimension=OssieDimension(is_time=True),
                label="Order date",
            ),
            OssieField(
                name="status",
                expression=_ansi("status"),
                dimension=OssieDimension(is_time=False),
            ),
            OssieField(name="amount", expression=_ansi("amount")),
            OssieField(name="customer_id", expression=_ansi("customer_id")),
        ],
    )
    customers = OssieDataset(
        name="customers",
        source="analytics_db.marts.customers",
        fields=[OssieField(name="customer_id", expression=_ansi("customer_id"))],
    )
    metrics = [
        OssieMetric(
            name="total_amount",
            expression=_ansi("SUM(orders.amount)"),
            description="Sum of order amounts",
            custom_extensions=[
                OssieCustomExtension(
                    vendor_name="LIGHTDASH",
                    data=json.dumps({"label": "Total amount", "format": "usd"}),
                )
            ],
        ),
        OssieMetric(
            name="conversion_rate",
            expression=_ansi(
                "SUM(orders.completed_count) / NULLIF(SUM(orders.total_count), 0)"
            ),
            custom_extensions=[
                OssieCustomExtension(
                    vendor_name="LIGHTDASH",
                    data=json.dumps({"format": "percent", "round": 1}),
                )
            ],
        ),
        OssieMetric(
            name="p90_amount",
            expression=_ansi("PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY orders.amount)"),
        ),
        OssieMetric(
            name="distinct_amount",
            expression=_ansi("SUM(DISTINCT orders.amount)"),
        ),
        OssieMetric(
            name="completed_rate",
            expression=_ansi("AVG(CASE WHEN orders.status = 'completed' THEN 1 ELSE 0 END)"),
        ),
        OssieMetric(
            name="cross_dataset",
            expression=_ansi("SUM(orders.amount) / COUNT(customers.customer_id)"),
        ),
        OssieMetric(
            name="foreign_vendor_metric",
            expression=_ansi("SUM(orders.amount)"),
            custom_extensions=[
                OssieCustomExtension(vendor_name="somebi", data='{"x": 1}')
            ],
        ),
    ]
    relationships = [
        OssieRelationship.model_validate(
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
            }
        )
    ]
    return OssieDocument(
        version="0.2.0.dev0",
        semantic_model=[
            OssieSemanticModel(
                name="sales",
                datasets=[orders, customers],
                metrics=metrics,
                relationships=relationships,
            )
        ],
    )


def _model(output, name):
    return next(m for m in output["models"] if m["name"] == name)


def _column(model, name):
    return next(c for c in model["columns"] if c["name"] == name)


class TestOssieToLightdash:
    def test_time_dimension_exports_date_type(self):
        result = OssieToLightdashConverter().convert(_document())
        column = _column(_model(result.output, "orders"), "order_date")
        assert column["meta"]["dimension"] == {"label": "Order date", "type": "date"}

    def test_dimension_with_nothing_to_say_needs_no_meta(self):
        # Every Lightdash column is a dimension by default.
        result = OssieToLightdashConverter().convert(_document())
        column = _column(_model(result.output, "orders"), "status")
        assert "meta" not in column

    def test_measure_only_field_becomes_a_hidden_dimension(self):
        result = OssieToLightdashConverter().convert(_document())
        column = _column(_model(result.output, "orders"), "amount")
        assert column["meta"]["dimension"] == {"hidden": True}

    def test_simple_aggregation_becomes_column_metric(self):
        result = OssieToLightdashConverter().convert(_document())
        column = _column(_model(result.output, "orders"), "amount")
        metric = column["meta"]["metrics"]["total_amount"]
        assert metric["type"] == "sum"
        assert metric["label"] == "Total amount"
        assert metric["format"] == "usd"
        assert metric["description"] == "Sum of order amounts"
        assert "sql" not in metric

    def test_complex_expression_becomes_model_metric(self):
        result = OssieToLightdashConverter().convert(_document())
        metric = _model(result.output, "orders")["meta"]["metrics"]["conversion_rate"]
        assert metric["type"] == "number"
        assert (
            metric["sql"]
            == "SUM(${TABLE}.completed_count) / NULLIF(SUM(${TABLE}.total_count), 0)"
        )
        assert metric["format"] == "percent"
        assert metric["round"] == 1

    def test_metric_over_a_joined_dataset_lives_on_the_joining_model(self):
        result = OssieToLightdashConverter().convert(_document())
        metric = _model(result.output, "orders")["meta"]["metrics"]["cross_dataset"]
        assert metric == {
            "type": "number",
            "sql": "SUM(${TABLE}.amount) / COUNT(${customers.customer_id})",
        }
        assert not any(
            issue.issue_type is ConverterIssueType.CROSS_DATASET_METRIC_DROPPED
            for issue in result.issues
        )

    def test_metric_over_an_unjoined_dataset_is_dropped_with_issue(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        # customers does not join orders, so no model can host the metric.
        tampered.semantic_model[0] = tampered.semantic_model[0].model_copy(
            update={
                "relationships": [],
                "metrics": [
                    *tampered.semantic_model[0].metrics,
                    OssieMetric(
                        name="reversed",
                        expression=_ansi("COUNT(customers.customer_id) / SUM(orders.amount)"),
                    ),
                ],
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        assert "metrics" not in _model(result.output, "customers").get("meta", {})
        assert {
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.CROSS_DATASET_METRIC_DROPPED
        } == {"cross_dataset", "reversed"}

    def test_field_references_resolve_through_declared_joins(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        orders = tampered.semantic_model[0].datasets[0]
        orders.fields.append(
            OssieField(
                name="customer_key",
                expression=_ansi("UPPER(customers.customer_id)"),
                dimension=OssieDimension(),
            )
        )
        customers = tampered.semantic_model[0].datasets[1]
        customers.fields.append(
            OssieField(
                name="last_order_status",
                expression=_ansi("orders.status"),
                dimension=OssieDimension(),
            )
        )
        result = OssieToLightdashConverter().convert(tampered)
        joined = _column(_model(result.output, "orders"), "customer_key")
        assert joined["meta"]["dimension"]["sql"] == "UPPER(${customers.customer_id})"
        unjoined = _column(_model(result.output, "customers"), "last_order_status")
        assert unjoined["meta"]["dimension"]["sql"] == "orders.status"
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.FIELD_REFERENCE_UNJOINED
        ] == ["last_order_status"]

    def test_preferred_dialect_falls_back_to_ansi_then_reports(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        orders = tampered.semantic_model[0].datasets[0]
        orders.fields[1] = OssieField(
            name="status",
            expression=OssieExpression(
                dialects=[
                    OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression="status"),
                    OssieDialectExpression(
                        dialect=OssieDialect.BIGQUERY, expression="LOWER(status)"
                    ),
                ]
            ),
            dimension=OssieDimension(),
        )
        orders.fields.append(
            OssieField(
                name="snowflake_only",
                expression=OssieExpression(
                    dialects=[
                        OssieDialectExpression(
                            dialect=OssieDialect.SNOWFLAKE, expression="status::VARCHAR"
                        )
                    ]
                ),
                dimension=OssieDimension(),
            )
        )
        result = OssieToLightdashConverter(OssieDialect.BIGQUERY).convert(tampered)
        model = _model(result.output, "orders")
        assert _column(model, "status")["meta"]["dimension"]["sql"] == "LOWER(status)"
        assert (
            _column(model, "snowflake_only")["meta"]["dimension"]["sql"] == "status::VARCHAR"
        )
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.DIALECT_UNAVAILABLE
        ] == ["snowflake_only"]

    def test_foreign_extension_is_reported(self):
        result = OssieToLightdashConverter().convert(_document())
        assert any(
            issue.issue_type is ConverterIssueType.FOREIGN_EXTENSION_IGNORED
            and issue.element_name == "foreign_vendor_metric"
            for issue in result.issues
        )

    def test_extension_vendor_name_is_the_registered_token(self):
        result = LightdashToOssieConverter().convert(
            {"models": [{"name": "t", "columns": [{"name": "c", "meta": {"dimension": {"format": "usd"}}}]}]},
            schema="s",
        )
        field = result.output.semantic_model[0].datasets[0].fields[0]
        assert field.custom_extensions[0].vendor_name == "LIGHTDASH"
        # Documents written before the registration used the lowercase name.
        legacy = OssieToLightdashConverter().convert(
            result.output.model_copy(
                update={
                    "semantic_model": [
                        result.output.semantic_model[0].model_copy(
                            update={
                                "datasets": [
                                    result.output.semantic_model[0].datasets[0].model_copy(
                                        update={
                                            "fields": [
                                                field.model_copy(
                                                    update={
                                                        "custom_extensions": [
                                                            OssieCustomExtension(vendor_name="lightdash", data=field.custom_extensions[0].data)
                                                        ]
                                                    }
                                                )
                                            ]
                                        }
                                    )
                                ]
                            }
                        )
                    ]
                }
            )
        )
        assert _column(_model(legacy.output, "t"), "c")["meta"]["dimension"]["format"] == "usd"
        assert not any(
            issue.issue_type is ConverterIssueType.FOREIGN_EXTENSION_IGNORED for issue in legacy.issues
        )

    def test_extension_cannot_override_structural_keys(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        metric = tampered.semantic_model[0].metrics[0].model_copy(
            update={
                "custom_extensions": [
                    OssieCustomExtension(
                        vendor_name="LIGHTDASH",
                        data=json.dumps(
                            {"label": "Total amount", "sql": "1 + 1", "description": "stale"}
                        ),
                    )
                ]
            }
        )
        tampered.semantic_model[0].metrics[0] = metric
        result = OssieToLightdashConverter().convert(tampered)
        column = _column(_model(result.output, "orders"), "amount")
        exported = column["meta"]["metrics"]["total_amount"]
        assert exported["label"] == "Total amount"
        assert "sql" not in exported
        assert exported["description"] == "Sum of order amounts"

    def test_mismatched_relationship_columns_are_skipped(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        relationship = OssieRelationship.model_validate(
            {
                "name": "broken",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id", "order_id"],
                "to_columns": ["customer_id"],
            }
        )
        tampered.semantic_model[0].relationships[0] = relationship
        result = OssieToLightdashConverter().convert(tampered)
        assert "joins" not in _model(result.output, "orders").get("meta", {})
        assert any(
            issue.issue_type is ConverterIssueType.RELATIONSHIP_COLUMNS_MISMATCHED
            and issue.element_name == "broken"
            for issue in result.issues
        )

    def test_invalid_extension_json_is_reported(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        metric = tampered.semantic_model[0].metrics[0].model_copy(
            update={
                "custom_extensions": [
                    OssieCustomExtension(vendor_name="LIGHTDASH", data="{not json")
                ]
            }
        )
        tampered.semantic_model[0].metrics[0] = metric
        result = OssieToLightdashConverter().convert(tampered)
        assert any(
            issue.issue_type is ConverterIssueType.EXTENSION_DATA_INVALID
            and issue.element_name == "total_amount"
            for issue in result.issues
        )

    def test_relationship_becomes_join(self):
        result = OssieToLightdashConverter().convert(_document())
        joins = _model(result.output, "orders")["meta"]["joins"]
        assert joins == [
            {
                "join": "customers",
                "sql_on": "${orders.customer_id} = ${customers.customer_id}",
                "relationship": "many-to-one",
            }
        ]

    def test_percentile_cont_becomes_percentile_metric(self):
        result = OssieToLightdashConverter().convert(_document())
        column = _column(_model(result.output, "orders"), "amount")
        assert column["meta"]["metrics"]["p90_amount"] == {
            "type": "percentile",
            "percentile": 90,
        }
        assert column["meta"]["metrics"]["distinct_amount"] == {"type": "sum_distinct"}

    def test_aggregation_over_expression_becomes_typed_model_metric(self):
        result = OssieToLightdashConverter().convert(_document())
        metric = _model(result.output, "orders")["meta"]["metrics"]["completed_rate"]
        assert metric == {
            "type": "average",
            "sql": "CASE WHEN ${TABLE}.status = 'completed' THEN 1 ELSE 0 END",
        }

    def test_repeated_relationship_gets_an_alias(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        tampered.semantic_model[0].relationships.append(
            OssieRelationship.model_validate(
                {
                    "name": "orders_to_referrer",
                    "from": "orders",
                    "to": "customers",
                    "from_columns": ["amount"],
                    "to_columns": ["customer_id"],
                }
            )
        )
        result = OssieToLightdashConverter().convert(tampered)
        assert _model(result.output, "orders")["meta"]["joins"] == [
            {
                "join": "customers",
                "sql_on": "${orders.customer_id} = ${customers.customer_id}",
                "relationship": "many-to-one",
            },
            {
                "join": "customers",
                "alias": "orders_to_referrer",
                "sql_on": "${orders.amount} = ${orders_to_referrer.customer_id}",
                "relationship": "many-to-one",
            },
        ]

    def test_join_alias_and_attributes_restore_from_extension(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        tampered.semantic_model[0].relationships[0] = OssieRelationship.model_validate(
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
                "custom_extensions": [
                    {
                        "vendor_name": "LIGHTDASH",
                        "data": json.dumps(
                            {"alias": "buyer", "relationship": "many-to-one", "sql_on": "1 = 1"}
                        ),
                    }
                ],
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        assert _model(result.output, "orders")["meta"]["joins"] == [
            {
                "join": "customers",
                "alias": "buyer",
                "sql_on": "${orders.customer_id} = ${buyer.customer_id}",
                "relationship": "many-to-one",
            }
        ]

    def test_primary_key_and_ai_context_become_model_meta(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        datasets = tampered.semantic_model[0].datasets
        datasets[0] = datasets[0].model_copy(
            update={
                "primary_key": ["order_id"],
                "ai_context": "Orders placed in the web shop.\nOne row per order.",
            }
        )
        datasets[1] = datasets[1].model_copy(
            update={
                "primary_key": ["customer_id", "region"],
                "ai_context": OssieAIContextObject(
                    instructions="Customer master data.",
                    synonyms=("clients", "buyers"),
                ),
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        orders_meta = _model(result.output, "orders")["meta"]
        assert orders_meta["primary_key"] == "order_id"
        assert orders_meta["ai_hint"] == [
            "Orders placed in the web shop.",
            "One row per order.",
        ]
        customers_meta = _model(result.output, "customers")["meta"]
        assert customers_meta["primary_key"] == ["customer_id", "region"]
        assert customers_meta["ai_hint"] == [
            "Customer master data.",
            "Also known as: clients, buyers",
        ]

    def test_field_and_metric_ai_context_become_ai_hints(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        orders = tampered.semantic_model[0].datasets[0]
        orders.fields[1] = orders.fields[1].model_copy(update={"ai_context": "Order lifecycle stage."})
        orders.fields[2] = orders.fields[2].model_copy(
            update={"ai_context": "Gross amount.", "datatype": OssieDataType.DECIMAL}
        )
        metrics = tampered.semantic_model[0].metrics
        metrics[0] = metrics[0].model_copy(update={"ai_context": "Revenue before refunds."})
        result = OssieToLightdashConverter().convert(tampered)
        model = _model(result.output, "orders")
        assert _column(model, "status")["meta"]["dimension"]["ai_hint"] == "Order lifecycle stage."
        # A hidden dimension keeps its hint and its type.
        assert _column(model, "amount")["meta"]["dimension"] == {
            "hidden": True,
            "ai_hint": "Gross amount.",
            "type": "number",
        }
        assert _column(model, "amount")["meta"]["metrics"]["total_amount"]["ai_hint"] == (
            "Revenue before refunds."
        )

    def test_time_axis_withdrawn_becomes_time_intervals_off(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        orders = tampered.semantic_model[0].datasets[0]
        orders.fields[0] = orders.fields[0].model_copy(
            update={"datatype": OssieDataType.DATE, "dimension": OssieDimension(is_time=False)}
        )
        result = OssieToLightdashConverter().convert(tampered)
        column = _column(_model(result.output, "orders"), "order_date")
        assert column["meta"]["dimension"] == {
            "label": "Order date",
            "type": "date",
            "time_intervals": "OFF",
        }

    def test_meta_can_be_placed_under_config(self):
        result = OssieToLightdashConverter(meta_under_config=True).convert(_document())
        model = _model(result.output, "orders")
        assert "meta" not in model
        assert model["config"]["meta"]["joins"][0]["join"] == "customers"
        column = _column(model, "order_date")
        assert "meta" not in column
        assert column["config"]["meta"]["dimension"]["type"] == "date"

    def test_lightdash_metric_name_comes_from_the_stash_then_the_prefix(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        tampered.semantic_model[0] = tampered.semantic_model[0].model_copy(
            update={
                "metrics": [
                    OssieMetric(
                        name="orders_total_amount",
                        expression=_ansi("SUM(orders.amount)"),
                        custom_extensions=[
                            OssieCustomExtension(
                                vendor_name="LIGHTDASH",
                                data=json.dumps({"name": "total_amount", "label": "Total"}),
                            )
                        ],
                    ),
                    OssieMetric(name="orders_max_amount", expression=_ansi("MAX(orders.amount)")),
                    OssieMetric(name="min_amount", expression=_ansi("MIN(orders.amount)")),
                ]
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        metrics = _column(_model(result.output, "orders"), "amount")["meta"]["metrics"]
        assert metrics == {
            "total_amount": {"type": "sum", "label": "Total"},
            "max_amount": {"type": "max"},
            "min_amount": {"type": "min"},
        }

    def test_unqualified_metric_is_placed_on_the_stashed_model(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        tampered.semantic_model[0] = tampered.semantic_model[0].model_copy(
            update={
                "metrics": [
                    OssieMetric(
                        name="customers_row_count",
                        expression=_ansi("COUNT(*)"),
                        custom_extensions=[
                            OssieCustomExtension(
                                vendor_name="LIGHTDASH",
                                data=json.dumps({"name": "row_count", "model": "customers"}),
                            )
                        ],
                    ),
                    OssieMetric(name="unplaceable", expression=_ansi("COUNT(*)")),
                ]
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        assert _model(result.output, "customers")["meta"]["metrics"] == {
            "row_count": {"type": "number", "sql": "COUNT(*)"}
        }
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.CROSS_DATASET_METRIC_DROPPED
        ] == ["unplaceable"]

    def test_stashed_joins_and_meta_restore_the_explore(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        datasets = tampered.semantic_model[0].datasets
        datasets[0] = datasets[0].model_copy(
            update={
                "custom_extensions": [
                    OssieCustomExtension(
                        vendor_name="LIGHTDASH",
                        data=json.dumps(
                            {
                                "sql_filter": "${TABLE}.deleted = false",
                                "joins": [
                                    {
                                        "join": "customers",
                                        "sql_on": "${orders.customer_id} = ${customers.customer_id} AND ${customers.active}",
                                    },
                                    {"join": "regions", "sql_on": "${customers.region_id} = ${regions.id}"},
                                ],
                            }
                        ),
                    )
                ]
            }
        )
        datasets[1] = datasets[1].model_copy(
            update={
                "fields": [
                    datasets[1].fields[0].model_copy(
                        update={
                            "custom_extensions": [
                                OssieCustomExtension(
                                    vendor_name="LIGHTDASH",
                                    data=json.dumps({"column_meta": {"additional_dimensions": {"id_prefix": {"type": "string", "sql": "LEFT(${TABLE}.customer_id, 2)"}}}}),
                                )
                            ]
                        }
                    )
                ]
            }
        )
        regions = OssieDataset(
            name="regions",
            source="analytics_db.marts.regions",
            fields=[OssieField(name="id", expression=_ansi("id"))],
        )
        tampered.semantic_model[0] = tampered.semantic_model[0].model_copy(
            update={
                "datasets": [*datasets, regions],
                "metrics": [
                    OssieMetric(
                        name="regional_spread",
                        expression=_ansi("COUNT(orders.customer_id) / COUNT(DISTINCT regions.id)"),
                    ),
                ],
            }
        )
        result = OssieToLightdashConverter().convert(tampered)
        orders = _model(result.output, "orders")
        # The stashed join replaces the generated one to the same target and
        # the chained join is appended; the model meta comes back as is.
        assert orders["meta"]["joins"] == [
            {
                "join": "customers",
                "sql_on": "${orders.customer_id} = ${customers.customer_id} AND ${customers.active}",
            },
            {"join": "regions", "sql_on": "${customers.region_id} = ${regions.id}"},
        ]
        assert orders["meta"]["sql_filter"] == "${TABLE}.deleted = false"
        # A metric over the chained target resolves through the stashed join.
        assert orders["meta"]["metrics"]["regional_spread"] == {
            "type": "number",
            "sql": "COUNT(${TABLE}.customer_id) / COUNT(DISTINCT ${regions.id})",
        }
        customer_id = _column(_model(result.output, "customers"), "customer_id")
        assert customer_id["meta"]["additional_dimensions"] == {
            "id_prefix": {"type": "string", "sql": "LEFT(${TABLE}.customer_id, 2)"}
        }

    def test_lightdash_model_files_are_deployable_as_they_are(self):
        document = _document()
        tampered = document.model_copy(deep=True)
        datasets = tampered.semantic_model[0].datasets
        datasets[0] = datasets[0].model_copy(
            update={
                "primary_key": ["order_id"],
                "custom_extensions": [
                    OssieCustomExtension(
                        vendor_name="LIGHTDASH",
                        data=json.dumps({"sql_filter": "${TABLE}.deleted = false", "label": "Orders"}),
                    )
                ],
            }
        )
        result = OssieToLightdashConverter().convert_models(tampered)
        orders = next(m for m in result.output if m["name"] == "orders")
        assert list(orders)[:4] == ["type", "name", "label", "description"]
        assert orders["type"] == "model"
        assert orders["sql_from"] == "analytics_db.marts.orders"
        assert orders["primary_key"] == "order_id"
        assert orders["sql_filter"] == "${TABLE}.deleted = false"
        assert orders["joins"][0]["join"] == "customers"
        assert orders["metrics"]["conversion_rate"]["type"] == "number"
        dimensions = {d["name"]: d for d in orders["dimensions"]}
        # Every dimension carries its own type and sql; a measure-only field
        # is a hidden one; column metrics sit under their dimension.
        assert dimensions["order_date"] == {
            "name": "order_date",
            "type": "date",
            "label": "Order date",
            "sql": "${TABLE}.order_date",
        }
        assert dimensions["amount"]["hidden"] is True
        assert dimensions["amount"]["sql"] == "${TABLE}.amount"
        assert dimensions["amount"]["metrics"]["total_amount"]["type"] == "sum"
        # No datatype on `status`: the type is assumed and reported.
        assert dimensions["status"]["type"] == "string"
        assert [
            issue.element_name
            for issue in result.issues
            if issue.issue_type is ConverterIssueType.DIMENSION_TYPE_DEFAULTED
        ] == ["orders.status", "orders.amount", "orders.customer_id", "customers.customer_id"]
