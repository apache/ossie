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
-- Data: store_sales, store_returns, item, date_dim, store, and customers
--
-- Shared input for the Appendix B.3 and unmatched-home-row cases under
-- ../cases/. Load this file before a stores case runs. Do not change the
-- values: the expected results depend on them.
--
-- Appendix B.3 names the datasets and relationships but gives no rows, so
-- the rows here are chosen for the cases. SS4 has no customer, and C3 has a
-- NULL region (Section 6, S1). The year column is d_year because YEAR is a
-- reserved word in standard SQL; the model names the field year.
-- stores_ambiguous_model adds store → date_dim via closing_date_id.
-- ============================================================================

DROP SCHEMA IF EXISTS stores CASCADE;
CREATE SCHEMA stores;

CREATE TABLE stores.date_dim (
  date_id VARCHAR NOT NULL PRIMARY KEY,
  d_year  INTEGER NOT NULL
);

INSERT INTO stores.date_dim VALUES
  ('D2023', 2023),
  ('D2024a', 2024),
  ('D2024b', 2024);

CREATE TABLE stores.item (
  item_id VARCHAR NOT NULL PRIMARY KEY,
  brand   VARCHAR NOT NULL
);

INSERT INTO stores.item VALUES
  ('I1', 'Acme'),
  ('I2', 'Beta');

CREATE TABLE stores.store (
  store_id        VARCHAR NOT NULL PRIMARY KEY,
  state           VARCHAR NOT NULL,
  closing_date_id VARCHAR NOT NULL
);

INSERT INTO stores.store VALUES
  ('S1', 'CA', 'D2024b'),
  ('S2', 'NY', 'D2024a');

CREATE TABLE stores.customers (
  customer_id VARCHAR NOT NULL PRIMARY KEY,
  region      VARCHAR
);

INSERT INTO stores.customers VALUES
  ('C1', 'EAST'),
  ('C2', 'WEST'),
  ('C3', NULL);

CREATE TABLE stores.store_sales (
  sale_id      VARCHAR NOT NULL PRIMARY KEY,
  item_id      VARCHAR NOT NULL,
  sale_date_id VARCHAR NOT NULL,
  store_id     VARCHAR NOT NULL,
  customer_id  VARCHAR,
  sales_amount INTEGER NOT NULL
);

INSERT INTO stores.store_sales VALUES
  ('SS1', 'I1', 'D2023',  'S1', 'C1',  100),
  ('SS2', 'I1', 'D2024a', 'S1', 'C2',  200),
  ('SS3', 'I2', 'D2024a', 'S2', 'C1',   50),
  ('SS4', 'I1', 'D2023',  'S1', NULL,   25),
  ('SS5', 'I2', 'D2024a', 'S2', 'C3',   40);

CREATE TABLE stores.store_returns (
  return_id      VARCHAR NOT NULL PRIMARY KEY,
  item_id        VARCHAR NOT NULL,
  return_date_id VARCHAR NOT NULL,
  return_amount  INTEGER NOT NULL
);

INSERT INTO stores.store_returns VALUES
  ('SR1', 'I1', 'D2023',  10),
  ('SR2', 'I2', 'D2024a',  5);
