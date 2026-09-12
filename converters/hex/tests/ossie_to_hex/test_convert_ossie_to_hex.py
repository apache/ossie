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

from pathlib import Path

from inline_snapshot import snapshot

from ossie_hex.ossie_to_hex.convert_ossie_to_hex import convert_ossie_to_hex
from tests.utils import hex_project_snapshot, problems_snapshot

TPCDS = Path(__file__).resolve().parents[4] / "examples" / "tpcds_semantic_model.yaml"


def test_convert_tpcds() -> None:
    hex_projects, problems = convert_ossie_to_hex(
        TPCDS,
        None,
        dialect=None,
    )
    assert len(hex_projects) == 1
    assert hex_project_snapshot(hex_projects[0]) == snapshot("""\
name: tpcds_retail_model
dialect: duckdb
resources:
- id: store_sales
  description: Fact table containing all store sales transactions
  base_sql_table: tpcds.public.store_sales
  dimensions:
  - id: ss_sold_date_sk
    description: Foreign key to date dimension
    type: number
    expr_sql: ss_sold_date_sk
  - id: ss_item_sk
    description: Foreign key to item dimension
    type: number
    expr_sql: ss_item_sk
  - id: ss_customer_sk
    description: Foreign key to customer dimension
    type: number
    expr_sql: ss_customer_sk
  - id: ss_store_sk
    description: Foreign key to store dimension
    type: number
    expr_sql: ss_store_sk
  - id: ss_quantity
    description: Quantity of items sold
    type: number
    expr_sql: ss_quantity
  - id: ss_sales_price
    description: Sales price per unit
    type: number
    expr_sql: ss_sales_price
  - id: ss_ext_sales_price
    description: Extended sales price (quantity * price)
    type: number
    expr_sql: ss_ext_sales_price
  - id: ss_net_profit
    description: Net profit from the sale
    type: number
    expr_sql: ss_net_profit
  measures:
  - id: total_sales
    description: Total sales revenue across all transactions
    func_sql: SUM(${ss_ext_sales_price})
  - id: total_profit
    description: Total net profit from store sales
    func_sql: SUM(${ss_net_profit})
  - id: customer_lifetime_value
    description: Average lifetime sales value per customer
    func_sql: SUM(${ss_ext_sales_price}) / COUNT(DISTINCT ${store_sales_to_customer.c_customer_sk})
  - id: sales_by_brand
    description: Total sales by brand (requires grouping by item.i_brand)
    func_sql: SUM(${ss_ext_sales_price})
  - id: store_productivity
    description: Sales per employee across stores
    func_sql: SUM(${ss_ext_sales_price}) / NULLIF(SUM(${store_sales_to_store.s_number_employees}),
      0)
  relations:
  - id: store_sales_to_date
    target: date_dim
    type: many_to_one
    join_sql: ss_sold_date_sk = ${store_sales_to_date}.d_date_sk
  - id: store_sales_to_customer
    target: customer
    type: many_to_one
    join_sql: ss_customer_sk = ${store_sales_to_customer}.c_customer_sk
  - id: store_sales_to_item
    target: item
    type: many_to_one
    join_sql: ss_item_sk = ${store_sales_to_item}.i_item_sk
  - id: store_sales_to_store
    target: store
    type: many_to_one
    join_sql: ss_store_sk = ${store_sales_to_store}.s_store_sk
- id: date_dim
  description: Date dimension with calendar attributes
  base_sql_table: tpcds.public.date_dim
  dimensions:
  - id: d_date_sk
    description: Surrogate key for date
    type: number
    expr_sql: d_date_sk
    unique: true
  - id: d_date
    description: Actual date value
    type: date
    expr_sql: d_date
  - id: d_year
    description: Year
    type: number
    expr_sql: d_year
  - id: d_quarter_name
    description: Quarter name (e.g., 2024Q1)
    type: string
    expr_sql: d_quarter_name
  - id: d_month_name
    description: Month name
    type: string
    expr_sql: d_month_name
- id: customer
  description: Customer dimension with demographic information
  base_sql_table: tpcds.public.customer
  dimensions:
  - id: c_customer_sk
    description: Surrogate key for customer
    type: number
    expr_sql: c_customer_sk
    unique: true
  - id: c_customer_id
    description: Business key for customer
    type: string
    expr_sql: c_customer_id
  - id: c_first_name
    description: Customer first name
    type: string
    expr_sql: c_first_name
  - id: c_last_name
    description: Customer last name
    type: string
    expr_sql: c_last_name
  - id: customer_full_name
    description: Customer full name (computed field)
    type: string
    expr_sql: c_first_name || ' ' || c_last_name
  - id: c_email_address
    description: Customer email address
    type: string
    expr_sql: c_email_address
- id: item
  description: Item/Product dimension with product attributes
  base_sql_table: tpcds.public.item
  dimensions:
  - id: i_item_sk
    description: Surrogate key for item
    type: number
    expr_sql: i_item_sk
    unique: true
  - id: i_item_id
    description: Business key for item
    type: string
    expr_sql: i_item_id
  - id: i_item_desc
    description: Item description
    type: string
    expr_sql: i_item_desc
  - id: i_brand
    description: Brand name
    type: string
    expr_sql: i_brand
  - id: i_category
    description: Item category
    type: string
    expr_sql: i_category
  - id: i_current_price
    description: Current price of the item
    type: number
    expr_sql: i_current_price
- id: store
  description: Store dimension with location and store attributes
  base_sql_table: tpcds.public.store
  dimensions:
  - id: s_store_sk
    description: Surrogate key for store
    type: number
    expr_sql: s_store_sk
    unique: true
  - id: s_store_id
    description: Business key for store
    type: string
    expr_sql: s_store_id
    unique: true
  - id: s_store_name
    description: Store name
    type: string
    expr_sql: s_store_name
  - id: s_city
    description: City where store is located
    type: string
    expr_sql: s_city
  - id: s_state
    description: State where store is located
    type: string
    expr_sql: s_state
  - id: s_number_employees
    description: Number of employees at the store
    type: number
    expr_sql: s_number_employees
""")
    assert problems_snapshot(problems, include_causes=True) == snapshot("""\
[WARNING] Missing. Hex requires a datatype. Using default 'String'.
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_quarter_name', 'datatype']

[WARNING] Missing. Hex requires a datatype. Using default 'String'.
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_month_name', 'datatype']

[INFO] No Ossie dialect specified; using ANSI_SQL
Cause: []

[WARNING] Composite primary key is not supported: ['ss_item_sk', 'ss_ticket_number']
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'primary_key']

[WARNING] Composite unique key is not supported: ['ss_item_sk', 'ss_ticket_number']
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'unique_keys']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_sold_date_sk', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_item_sk', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_customer_sk', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_store_sk', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_quantity', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_sales_price', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_ext_sales_price', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'fields', 'ss_net_profit', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store_sales', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_date', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_year', 'dimension', 'is_time']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_year', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_quarter_name', 'dimension', 'is_time']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_quarter_name', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_month_name', 'dimension', 'is_time']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'fields', 'd_month_name', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'date_dim', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'customer', 'fields', 'c_customer_id', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'customer', 'fields', 'customer_full_name', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'customer', 'fields', 'c_email_address', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'customer', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'fields', 'i_item_id', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'fields', 'i_item_desc', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'fields', 'i_brand', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'fields', 'i_category', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'fields', 'i_current_price', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'item', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'fields', 's_store_id', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'fields', 's_store_name', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'fields', 's_city', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'fields', 's_state', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'fields', 's_number_employees', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'datasets', 'store', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'relationships', 'store_sales_to_date', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'relationships', 'store_sales_to_customer', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'relationships', 'store_sales_to_item', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'relationships', 'store_sales_to_store', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'metrics', 'total_sales', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'metrics', 'total_profit', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'metrics', 'customer_lifetime_value', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'metrics', 'sales_by_brand', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'metrics', 'store_productivity', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'description']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'ai_context']

[WARNING] Not supported
Cause: ['semantic_model', 'tpcds_retail_model', 'custom_extensions']\
""")
