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

import warnings

import pytest
import yaml

from ossie_duckdb import ConversionError, convert_osi_to_duckdb


def _base_model(metrics=None, relationships=None):
    """Minimal two-dataset model for error-path tests."""
    return yaml.safe_dump(
        {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "test_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "memory.main.orders",
                            "fields": [
                                {
                                    "name": "amount",
                                    "expression": {"dialects": [{"dialect": "DUCKDB", "expression": "amount"}]},
                                }
                            ],
                        },
                        {
                            "name": "customers",
                            "source": "memory.main.customers",
                            "fields": [
                                {
                                    "name": "id",
                                    "expression": {"dialects": [{"dialect": "DUCKDB", "expression": "id"}]},
                                }
                            ],
                        },
                    ],
                    "relationships": relationships or [],
                    "metrics": metrics or [],
                }
            ],
        },
        sort_keys=False,
    )


def test_generates_dataset_and_metric_views(retail_model_yaml):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sql = convert_osi_to_duckdb(retail_model_yaml)
    assert "CREATE OR REPLACE VIEW store_sales AS" in sql
    assert "CREATE OR REPLACE VIEW date_dim AS" in sql
    assert "CREATE OR REPLACE VIEW customer AS" in sql
    assert "CREATE OR REPLACE VIEW metric_total_sales AS" in sql
    assert "CREATE OR REPLACE VIEW metric_sales_per_customer AS" in sql
    assert "FROM tpcds.public.store_sales" in sql
    # Cross-dataset metric derives its join from the declared relationship.
    assert "LEFT JOIN customer AS customer ON store_sales.ss_customer_sk = customer.c_customer_sk" in sql


def test_comments_emitted(retail_model_yaml):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sql = convert_osi_to_duckdb(retail_model_yaml)
    assert "COMMENT ON VIEW store_sales IS 'Store sales fact table'" in sql
    assert "COMMENT ON COLUMN store_sales.ss_ext_sales_price IS 'Extended sales price'" in sql
    assert "COMMENT ON VIEW metric_total_sales IS 'Total store sales revenue'" in sql


def test_generated_sql_executes(retail_model_yaml, retail_source_db):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sql = convert_osi_to_duckdb(retail_model_yaml)
    retail_source_db.execute(sql)
    assert retail_source_db.execute("SELECT total_sales FROM metric_total_sales").fetchone()[0] == 60
    assert retail_source_db.execute("SELECT sales_per_customer FROM metric_sales_per_customer").fetchone()[0] == 30
    full_names = {r[0] for r in retail_source_db.execute("SELECT c_full_name FROM customer").fetchall()}
    assert full_names == {"Ada Lovelace", "Grace Hopper"}


def test_view_schema_option(retail_model_yaml, retail_source_db):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sql = convert_osi_to_duckdb(retail_model_yaml, view_schema="semantic")
    assert "CREATE SCHEMA IF NOT EXISTS semantic" in sql
    assert "CREATE OR REPLACE VIEW semantic.store_sales AS" in sql
    retail_source_db.execute(sql)
    assert retail_source_db.execute("SELECT total_sales FROM semantic.metric_total_sales").fetchone()[0] == 60


def test_ansi_fallback_warns(retail_model_yaml):
    with pytest.warns(UserWarning, match="falling back to ANSI_SQL"):
        convert_osi_to_duckdb(retail_model_yaml)


def test_missing_dialect_errors():
    model = _base_model(
        metrics=[
            {
                "name": "broken",
                "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "SUM(orders.amount)"}]},
            }
        ]
    )
    with pytest.raises(ConversionError, match="no DUCKDB or ANSI_SQL expression"):
        convert_osi_to_duckdb(model)


def test_disconnected_metric_errors():
    model = _base_model(
        metrics=[
            {
                "name": "orphan",
                "expression": {
                    "dialects": [{"dialect": "DUCKDB", "expression": "SUM(orders.amount) / COUNT(customers.id)"}]
                },
            }
        ]
    )
    with pytest.raises(ConversionError, match="orphan.*not connected"):
        convert_osi_to_duckdb(model)


def test_join_column_not_exposed_errors():
    model = _base_model(
        metrics=[
            {
                "name": "avg_per_customer",
                "expression": {
                    "dialects": [{"dialect": "DUCKDB", "expression": "SUM(orders.amount) / COUNT(customers.id)"}]
                },
            }
        ],
        relationships=[
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            }
        ],
    )
    with pytest.raises(ConversionError, match="customer_id.*does not expose"):
        convert_osi_to_duckdb(model)


def test_not_an_ossie_document_errors():
    with pytest.raises(ConversionError, match="missing 'semantic_model'"):
        convert_osi_to_duckdb("just: some yaml")
