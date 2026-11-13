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
-- Data: customers, orders, returns, visits, and tickets
--
-- Shared input data for the wide table compliance cases in ../cases/. Load
-- this file before a case runs. Do not change the values: the expected results
-- of all cases depend on them.
--
-- The rows of customers, orders, returns, and visits are the tables of
-- core-spec/wide_table_semantics.md, Appendix B.1 and B.2. The specification
-- gives no rows for tickets (Appendix B.4), so the table is empty.
-- ============================================================================

DROP SCHEMA IF EXISTS retail CASCADE;
CREATE SCHEMA retail;

CREATE TABLE retail.customers (
  customer_id VARCHAR NOT NULL PRIMARY KEY,
  region      VARCHAR NOT NULL
);

INSERT INTO retail.customers VALUES
  ('C1', 'EAST'),
  ('C2', 'EAST'),
  ('C3', 'WEST'),
  ('C4', 'NORTH');

CREATE TABLE retail.orders (
  order_id    VARCHAR NOT NULL PRIMARY KEY,
  customer_id VARCHAR NOT NULL,
  amount      INTEGER NOT NULL
);

INSERT INTO retail.orders VALUES
  ('O1', 'C1', 100),
  ('O2', 'C1', 200),
  ('O3', 'C2',  50),
  ('O4', 'C3', 300);

CREATE TABLE retail.returns (
  return_id   VARCHAR NOT NULL PRIMARY KEY,
  customer_id VARCHAR NOT NULL,
  amount      INTEGER NOT NULL
);

INSERT INTO retail.returns VALUES
  ('R1', 'C1', 25),
  ('R2', 'C4', 10);

CREATE TABLE retail.visits (
  visit_id    VARCHAR NOT NULL PRIMARY KEY,
  customer_id VARCHAR NOT NULL
);

INSERT INTO retail.visits VALUES
  ('V1', 'C4'),
  ('V2', 'C4'),
  ('V3', 'C3');

CREATE TABLE retail.tickets (
  ticket_id VARCHAR NOT NULL PRIMARY KEY
);
