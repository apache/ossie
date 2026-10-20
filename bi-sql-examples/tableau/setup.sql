-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

-- ============================================================================
-- Tableau BI SQL examples: setup
--
-- This file is NOT meant to be run. It shows the shape of the tables and the
-- metrics the example queries assume.
--
-- The measure-declaration syntax below is illustrative only - it is not real,
-- runnable SQL. The proof of concept used Databricks Metric View DDL; this file
-- is written generally to show the model shape, so the captured queries are
-- examples of SQL shape rather than something to run against this setup.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Single-table model: orders_metrics over orders_table
-- ----------------------------------------------------------------------------

CREATE TABLE orders_table (
  order_id    INT,
  order_date  DATE,
  category    STRING,
  amount      DECIMAL(10, 2),
  quantity    INT,
  state_id    INT
);

CREATE OR REPLACE VIEW orders_metrics AS
SELECT
  *,
  SUM(amount)   TO MEASURE total_revenue,
  SUM(quantity) TO MEASURE total_quantity
FROM orders_table;

-- ----------------------------------------------------------------------------
-- Two-table model: sales and tickets
-- ----------------------------------------------------------------------------

CREATE TABLE sales_table (
  sale_id  INT,
  region   STRING,
  amount   DECIMAL(10, 2)
);

CREATE TABLE tickets_table (
  ticket_id  INT,
  region     STRING
);

CREATE OR REPLACE VIEW sales AS
SELECT
  *,
  SUM(amount) TO MEASURE total_sales
FROM sales_table;

CREATE OR REPLACE VIEW tickets AS
SELECT
  *,
  COUNT(*) TO MEASURE ticket_count
FROM tickets_table;
