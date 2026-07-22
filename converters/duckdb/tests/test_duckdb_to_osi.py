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

import jsonschema
import pytest
import yaml

from ossie_duckdb import ConversionError, convert_duckdb_to_osi, convert_duckdb_to_osi_yaml


def test_import_validates_against_schema(orders_db, osi_schema):
    document = convert_duckdb_to_osi(orders_db)
    jsonschema.validate(document, osi_schema)
    assert document["version"] == "0.2.0.dev0"


def test_import_datasets_and_constraints(orders_db):
    model = convert_duckdb_to_osi(orders_db)["semantic_model"][0]
    datasets = {d["name"]: d for d in model["datasets"]}

    assert set(datasets) == {"customers", "orders"}
    assert datasets["customers"]["primary_key"] == ["id"]
    assert datasets["customers"]["unique_keys"] == [["email"]]
    assert datasets["orders"]["primary_key"] == ["order_id"]
    assert datasets["orders"]["source"] == "memory.main.orders"

    assert model["relationships"] == [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]


def test_import_comments_and_dialect(orders_db):
    model = convert_duckdb_to_osi(orders_db)["semantic_model"][0]
    orders = next(d for d in model["datasets"] if d["name"] == "orders")

    assert orders["description"] == "Order transactions"
    amount = next(f for f in orders["fields"] if f["name"] == "amount")
    assert amount["description"] == "Order amount in USD"
    assert amount["expression"]["dialects"] == [{"dialect": "DUCKDB", "expression": "amount"}]


def test_import_includes_user_views(orders_db):
    orders_db.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE amount > 15")
    model = convert_duckdb_to_osi(orders_db)["semantic_model"][0]
    names = {d["name"] for d in model["datasets"]}
    assert "big_orders" in names


def test_schema_filter(orders_db):
    orders_db.execute("CREATE SCHEMA staging")
    orders_db.execute("CREATE TABLE staging.raw_events (id INTEGER)")
    main_model = convert_duckdb_to_osi(orders_db)["semantic_model"][0]
    assert "raw_events" not in {d["name"] for d in main_model["datasets"]}

    staging_model = convert_duckdb_to_osi(orders_db, schema="staging")["semantic_model"][0]
    assert {d["name"] for d in staging_model["datasets"]} == {"raw_events"}


def test_model_name_default_and_override(orders_db):
    assert convert_duckdb_to_osi(orders_db)["semantic_model"][0]["name"] == "memory_main"
    named = convert_duckdb_to_osi(orders_db, model_name="orders_analytics")
    assert named["semantic_model"][0]["name"] == "orders_analytics"


def test_empty_schema_errors(orders_db):
    orders_db.execute("CREATE SCHEMA empty_schema")
    with pytest.raises(ConversionError, match="No tables or views"):
        convert_duckdb_to_osi(orders_db, schema="empty_schema")


def test_yaml_output_parses(orders_db):
    text = convert_duckdb_to_osi_yaml(orders_db)
    assert yaml.safe_load(text) == convert_duckdb_to_osi(orders_db)
