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
-- ThoughtSpot BI SQL examples: setup
--
-- Creates the model the captured queries in this folder run against: a six-table
-- star and two warehouse-native semantic layers defined over it.
--
-- Sections:
--   1. Base tables (Databricks dialect).
--   2. The Databricks Metric View the captured SQL queries.
--   3. A Snowflake Semantic View over the same star, for contrast.
--
-- The two semantic layers are reduced to the columns these examples exercise or
-- discuss; their top-level comments were rewritten to say so, and the Snowflake
-- definition's Cortex Analyst extension clause was dropped. Otherwise both are as
-- deployed. Section 1 is not reduced - it is the deployed table shape, so it
-- declares columns no example reaches.
--
-- The two layers are not the same surface: category_quantity is a dimension on
-- Databricks and a metric on Snowflake, and under a filter they return different
-- numbers for it - see 04-lod-two-stage-aggregation.sql.
--
-- Sections 1 and 2 are Databricks and run there given an existing agent_skills
-- catalog holding the dunder_mifflin schema. No CREATE SCHEMA is included and no
-- CREATE TABLE uses IF NOT EXISTS, so the six tables must be absent - a run
-- against a schema that already holds them fails. Section 3 is Snowflake DDL
-- against a database this file does not create. The file is two dialects and is not
-- meant to run end to end on one engine.
--
-- No data ships with it. The captured queries are reproduced for their SQL shape
-- rather than their results, so none of them depends on the original rows.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Base tables
-- ----------------------------------------------------------------------------

CREATE TABLE agent_skills.dunder_mifflin.dm_order_detail (
  DM_ORDER_DETAIL_ORDER_ID   BIGINT,
  DM_ORDER_DETAIL_PRODUCT_ID BIGINT,
  UNIT_PRICE                 DOUBLE,
  QUANTITY                   BIGINT,
  DISCOUNT                   STRING,
  LINE_TOTAL                 DOUBLE
);

CREATE TABLE agent_skills.dunder_mifflin.dm_order (
  ORDER_ID             BIGINT,
  DM_ORDER_CUSTOMER_ID BIGINT,
  DM_ORDER_EMPLOYEE_ID BIGINT,
  DM_ORDER_ORDER_DATE  DATE,
  REQUIRED_DATE        DATE,
  SHIPPED_DATE         DATE,
  SHIPPER_ID           BIGINT,
  FREIGHT              DOUBLE,
  SHIP_NAME            STRING,
  SHIP_ADDRESS         STRING,
  SHIP_CITY            STRING,
  SHIP_REGION          STRING,
  SHIP_POSTAL_CODE     BIGINT,
  SHIP_COUNTRY         STRING
);

CREATE TABLE agent_skills.dunder_mifflin.dm_customer (
  CUSTOMER_ID   BIGINT,
  CUSTOMER_CODE STRING,
  COMPANY_NAME  STRING,
  CONTACT_NAME  STRING,
  CONTACT_TITLE STRING,
  ADDRESS       STRING,
  CITY          STRING,
  STATE         STRING,
  ZIPCODE       BIGINT,
  COUNTRY       STRING,
  TELEPHONE     STRING,
  FAX           STRING
);

CREATE TABLE agent_skills.dunder_mifflin.dm_employee (
  EMPLOYEE_ID       BIGINT,
  LAST_NAME         STRING,
  FIRST_NAME        STRING,
  MIDDLE_NAME       STRING,
  TITLE             STRING,
  TITLE_OF_COURTESY STRING,
  BIRTH_DATE        STRING,
  HIRE_DATE         DATE,
  TERMINATION_DATE  DATE,
  REHIRE_DATE       DATE,
  ADDRESS           STRING,
  CITY              STRING,
  STATE             STRING,
  ZIPCODE           BIGINT,
  COUNTRY           STRING,
  TELEPHONE         STRING,
  EXTENSION         BIGINT,
  NOTES             STRING,
  REPORTS_TO        BIGINT,
  PHOTO_PATH        STRING,
  STATUS_ID         BIGINT
);

CREATE TABLE agent_skills.dunder_mifflin.dm_product (
  PRODUCT_ID             BIGINT,
  PRODUCT_NAME           STRING,
  PRODUCT_DESCRIPTION    STRING,
  SUPPLIER_ID            BIGINT,
  DM_PRODUCT_CATEGORY_ID BIGINT,
  QUANTITY_PER_UNIT      STRING,
  DM_PRODUCT_UNIT_PRICE  DOUBLE,
  UNITS_IN_STOCK         BIGINT,
  UNITS_ON_ORDER         BIGINT,
  REORDER_LEVEL          BIGINT,
  DISCONTINUED_FLAG      BOOLEAN
);

CREATE TABLE agent_skills.dunder_mifflin.dm_category (
  CATEGORY_ID   BIGINT,
  CATEGORY_NAME STRING,
  DESCRIPTION   STRING,
  PICTURE       STRING
);

-- ----------------------------------------------------------------------------
-- 2. Databricks Metric View
--
-- The measures the captured SQL reads through MEASURE(). category_quantity is
-- declared here as a DIMENSION: its comment records that it is evaluated
-- independently of the query GROUP BY.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW agent_skills.dunder_mifflin.dunder_mifflin_sales_mv
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: agent_skills.dunder_mifflin.DM_ORDER_DETAIL
joins:
- name: orders
  source: agent_skills.dunder_mifflin.DM_ORDER
  'on': source.DM_ORDER_DETAIL_ORDER_ID = orders.ORDER_ID
  joins:
  - name: customers
    source: agent_skills.dunder_mifflin.DM_CUSTOMER
    'on': orders.DM_ORDER_CUSTOMER_ID = customers.CUSTOMER_ID
    rely:
      at_most_one_match: true
  - name: employees
    source: agent_skills.dunder_mifflin.DM_EMPLOYEE
    'on': orders.DM_ORDER_EMPLOYEE_ID = employees.EMPLOYEE_ID
    rely:
      at_most_one_match: true
  rely:
    at_most_one_match: true
- name: products
  source: agent_skills.dunder_mifflin.DM_PRODUCT
  'on': source.DM_ORDER_DETAIL_PRODUCT_ID = products.PRODUCT_ID
  joins:
  - name: category
    source: agent_skills.dunder_mifflin.DM_CATEGORY
    'on': products.DM_PRODUCT_CATEGORY_ID = category.CATEGORY_ID
    rely:
      at_most_one_match: true
  rely:
    at_most_one_match: true
comment: Dunder Mifflin Sales metrics. Reduced to the columns the captured queries in this folder exercise.
dimensions:
- name: order_date
  expr: orders.DM_ORDER_ORDER_DATE
  comment: Date the order was placed. The primary time anchor for sales activity.
  display_name: Order Date
  synonyms:
  - order placed
  - purchase date
- name: product_name
  expr: products.PRODUCT_NAME
  comment: Display name of the product.
  display_name: Product Name
  synonyms:
  - product
  - item
- name: product_category
  expr: products.category.CATEGORY_NAME
  comment: Category name the product belongs to.
  display_name: Product Category
  synonyms:
  - category
  - product line
- name: customer_name
  expr: orders.customers.COMPANY_NAME
  comment: The customer display name.
  display_name: Customer Name
  synonyms:
  - customer
  - client
  - buyer
- name: customer_state
  expr: orders.customers.STATE
  comment: The customer state of residence.
  display_name: Customer State
  synonyms:
  - state
- name: customer_zipcode
  expr: orders.customers.ZIPCODE
  comment: Postal code on the customer billing address.
  display_name: Customer Zipcode
  synonyms:
  - zip
  - zip code
  - postal code
- name: employee_name
  expr: CONCAT(orders.employees.LAST_NAME, ', ', orders.employees.FIRST_NAME)
  comment: Employee name as Last, First.
  display_name: Employee
  synonyms:
  - sales rep
  - rep
  - salesperson
- name: category_quantity
  expr: SUM(source.QUANTITY) OVER (PARTITION BY products.category.CATEGORY_NAME)
  comment: Total units sold at category grain, independent of query GROUP BY.
  display_name: Category Quantity
measures:
- name: amount
  expr: SUM(source.LINE_TOTAL)
  comment: Dollar value of an order-line item. The primary revenue measure.
  display_name: Amount
  synonyms:
  - revenue
  - sales
  - sales revenue
- name: quantity
  expr: SUM(source.QUANTITY)
  comment: Number of units sold on one order line.
  display_name: Quantity
  synonyms:
  - units
  - units sold
- name: employee_count
  expr: COUNT(DISTINCT orders.employees.EMPLOYEE_ID)
  comment: Distinct count of employees referenced on order records.
  display_name: '# Employees'
  synonyms:
  - employee count
  - rep count
- name: product_to_category_contribution_ratio
  expr: COALESCE(MEASURE(quantity) / NULLIF(ANY_VALUE(category_quantity), 0), 0)
  comment: Share of a product category total units by an individual product.
  display_name: Product to Category Contribution Ratio
$$;

-- ----------------------------------------------------------------------------
-- 3. Snowflake Semantic View
--
-- The same star as a Snowflake Semantic View. category_quantity is a METRIC here,
-- not a dimension, and its comment records that it respects query filters where
-- the Databricks source suppresses them. Querying both deployed layers confirms
-- that: under the same filter the Databricks copy returns the whole category's
-- units and the Snowflake copy returns the filtered total. No SQL in this folder
-- was captured against Snowflake; the definition is included so the two surfaces
-- can be compared.
--
-- The RRDER_ID spelling is a real typo, and Snowflake-side only - the Databricks
-- foreign key is DM_ORDER_DETAIL_ORDER_ID. It is kept because it is a real artefact
-- of the deployed model and worth seeing - not because this file matches that model
-- in every other respect, since the trimming above means it does not.
-- ----------------------------------------------------------------------------

create or replace semantic view SV_DUNDER_MIFFLIN_SALES
	tables (
		DUNDERMIFFLIN.PUBLIC_SV.DM_ORDER_DETAIL,
		DUNDERMIFFLIN.PUBLIC_SV.DM_ORDER primary key (ORDER_ID),
		DUNDERMIFFLIN.PUBLIC_SV.DM_CUSTOMER primary key (CUSTOMER_ID),
		DUNDERMIFFLIN.PUBLIC_SV.DM_EMPLOYEE primary key (EMPLOYEE_ID),
		DUNDERMIFFLIN.PUBLIC_SV.DM_PRODUCT primary key (PRODUCT_ID),
		DUNDERMIFFLIN.PUBLIC_SV.DM_CATEGORY primary key (CATEGORY_ID)
	)
	relationships (
		DM_ORDER_DETAIL_TO_DM_ORDER as DM_ORDER_DETAIL(RRDER_ID) references DM_ORDER(ORDER_ID),
		DM_ORDER_DETAIL_TO_DM_PRODUCT as DM_ORDER_DETAIL(DM_ORDER_DETAIL_PRODUCT_ID) references DM_PRODUCT(PRODUCT_ID),
		DM_ORDER_TO_DM_CUSTOMER as DM_ORDER(DM_ORDER_CUSTOMER_ID) references DM_CUSTOMER(CUSTOMER_ID),
		DM_ORDER_TO_DM_EMPLOYEE as DM_ORDER(DM_ORDER_EMPLOYEE_ID) references DM_EMPLOYEE(EMPLOYEE_ID),
		DM_PRODUCT_TO_DM_CATEGORY as DM_PRODUCT(DM_PRODUCT_CATEGORY_ID) references DM_CATEGORY(CATEGORY_ID)
	)
	dimensions (
		DM_ORDER.ORDER_DATE as dm_order.ORDER_DATE with synonyms=('Order Date','order placed','purchase date') comment='Date the order was placed. The primary time anchor for sales activity.',
		DM_CUSTOMER.CUSTOMER_NAME as dm_customer.COMPANY_NAME with synonyms=('Customer Name','customer','client','buyer') comment='The customer display name.',
		DM_CUSTOMER.CUSTOMER_STATE as dm_customer.STATE with synonyms=('Customer State','state') comment='The customer state of residence.',
		DM_CUSTOMER.CUSTOMER_ZIPCODE as dm_customer.ZIPCODE with synonyms=('Customer Zipcode','zip','zip code','postal code') comment='Postal code on the customer billing address.',
		DM_EMPLOYEE.EMPLOYEE_NAME as CONCAT(dm_employee.LAST_NAME, ', ', dm_employee.FIRST_NAME) with synonyms=('Employee Name','sales rep','rep','salesperson') comment='Employee name as Last, First.',
		DM_PRODUCT.PRODUCT_NAME as dm_product.PRODUCT_NAME with synonyms=('Product Name','product','item') comment='Display name of the product.',
		DM_CATEGORY.PRODUCT_CATEGORY as dm_category.CATEGORY_NAME with synonyms=('Product Category','category','product line') comment='Category name the product belongs to.'
	)
	metrics (
		DM_ORDER.EMPLOYEE_COUNT as COUNT(DISTINCT dm_order.DM_ORDER_EMPLOYEE_ID) with synonyms=('Employee Count','employee count','rep count') comment='Count of employees referenced on order records.',
		DM_ORDER_DETAIL.AMOUNT as SUM(dm_order_detail.LINE_TOTAL) with synonyms=('Amount','revenue','sales','sales revenue') comment='Dollar value of an order-line item. The primary revenue measure.',
		DM_ORDER_DETAIL.QUANTITY as SUM(dm_order_detail.QUANTITY) with synonyms=('Quantity','units','units sold') comment='Number of units sold on one order line.',
		DM_ORDER_DETAIL.CATEGORY_QUANTITY as SUM(dm_order_detail.QUANTITY) OVER (PARTITION BY dm_category.product_category) with synonyms=('Category Quantity') comment='Units sold at category grain. LOD; translated from group_aggregate with query_filters(), so it RESPECTS query filters -- unlike the DBX source which suppresses them.',
		DM_ORDER_DETAIL.PRODUCT_TO_CATEGORY_CONTRIBUTION_RATIO as DIV0(dm_order_detail.QUANTITY, SUM(dm_order_detail.QUANTITY) OVER (PARTITION BY dm_category.product_category)) with synonyms=('Product To Category Contribution Ratio') comment='Share of category units contributed by a product. DIV0 guards divide-by-zero.'
	)
	comment='Dunder Mifflin Sales metrics. Reduced to the columns the captured queries in this Ossie example folder exercise.';
