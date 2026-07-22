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

import duckdb
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def retail_model_yaml() -> str:
    return (FIXTURES / "osi_retail.yaml").read_text()


@pytest.fixture
def osi_schema() -> dict:
    return json.loads((REPO_ROOT / "core-spec" / "osi-schema.json").read_text())


@pytest.fixture
def retail_source_db():
    """In-memory DuckDB with the tpcds.public stub tables the fixture model points at."""
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS tpcds")
    conn.execute("CREATE SCHEMA tpcds.public")
    conn.execute(
        """
        CREATE TABLE tpcds.public.store_sales (
            ss_item_sk INTEGER,
            ss_ticket_number INTEGER,
            ss_sold_date_sk INTEGER,
            ss_customer_sk INTEGER,
            ss_ext_sales_price DECIMAL(10, 2)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tpcds.public.date_dim (
            d_date_sk INTEGER,
            d_year INTEGER,
            d_date DATE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tpcds.public.customer (
            c_customer_sk INTEGER,
            c_first_name VARCHAR,
            c_last_name VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tpcds.public.store_sales VALUES
            (1, 100, 20260101, 1, 10.00),
            (2, 100, 20260101, 1, 20.00),
            (3, 101, 20260102, 2, 30.00)
        """
    )
    conn.execute(
        """
        INSERT INTO tpcds.public.date_dim VALUES
            (20260101, 2026, DATE '2026-01-01'),
            (20260102, 2026, DATE '2026-01-02')
        """
    )
    conn.execute(
        """
        INSERT INTO tpcds.public.customer VALUES
            (1, 'Ada', 'Lovelace'),
            (2, 'Grace', 'Hopper')
        """
    )
    yield conn
    conn.close()


@pytest.fixture
def orders_db():
    """In-memory DuckDB with PK / UNIQUE / FK constraints and comments, for import tests."""
    conn = duckdb.connect()
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email VARCHAR UNIQUE, name VARCHAR)")
    conn.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            amount DECIMAL(10, 2)
        )
        """
    )
    conn.execute("COMMENT ON TABLE orders IS 'Order transactions'")
    conn.execute("COMMENT ON COLUMN orders.amount IS 'Order amount in USD'")
    conn.execute("INSERT INTO customers VALUES (1, 'ada@example.com', 'Ada'), (2, 'grace@example.com', 'Grace')")
    conn.execute("INSERT INTO orders VALUES (10, 1, 10.00), (11, 1, 20.00), (12, 2, 30.00)")
    yield conn
    conn.close()
