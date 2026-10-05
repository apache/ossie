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
-- ThoughtSpot BI SQL examples: an LoD declared in the model
--
-- The direct counterpart is tableau/02-lod-two-stage-aggregation.sql, where a
-- FIXED level-of-detail calculation pins an inner aggregation to a chosen
-- grain so a second aggregation can run over it. Here the level-of-detail
-- column lives in the model rather than in the Answer: this model ships
-- category_quantity, and the three captures read it - as the grain of a
-- group_aggregate, as a plain grouping column, and as a filter predicate.
--
-- The finding in this file is in the model rather than in the SQL. setup.sql
-- defines category_quantity twice over the same star, once per
-- warehouse-native layer, with the same window expression both times -
-- SUM(QUANTITY) OVER (PARTITION BY <category name>), each layer spelling the
-- columns its own way - and declares it in a different section each time.
-- Each definition carries its own comment.
--
-- Databricks Metric View, under `dimensions:`
--
--   Total units sold at category grain, independent of query GROUP BY.
--
-- Snowflake Semantic View, under `metrics`
--
--   Units sold at category grain. LOD; translated from group_aggregate
--   with query_filters(), so it RESPECTS query filters -- unlike the DBX
--   source which suppresses them.
--
-- Same star, same name, same window expression, declared in opposite sections.
-- The Databricks comment speaks only of the GROUP BY; the Snowflake one goes
-- further and claims its copy respects query filters where the Databricks source
-- suppresses them.
--
-- That claim is correct, and the divergence is measurable rather than a matter of
-- two comments disagreeing. Asking both deployed layers the same question under
-- the same filter - one product, reported at product and category grain - the
-- Databricks Metric View returns the whole category's units for
-- category_quantity while its quantity measure narrows to the product; the
-- Snowflake Semantic View narrows both. So the window is evaluated before the
-- filter on one layer and after it on the other.
--
-- None of that is visible in the statements below, which carry no filter on a
-- dimension outside the LoD, and no SQL in this folder was captured against
-- Snowflake. It was established by querying both layers directly. It is recorded
-- here because a measure that silently means two different things across two
-- layers of the same star is the kind of divergence a portable semantics spec has
-- to name: neither layer errors, and the numbers simply differ.
--
-- That section also decides what the generated SQL may do with the column.
-- As a dimension it can be selected bare, grouped, and filtered in WHERE -
-- all three appear below. Declared as a metric it is not a column a query
-- groups by or names in WHERE at all; it is reached through the layer's
-- metric syntax. The predicate in Query 3 cannot sit inside a Snowflake
-- SEMANTIC_VIEW clause - a metric is not a DIMENSION or FACT there - though an
-- equivalent query can be written by filtering outside it.
--
-- On the ThoughtSpot side the filter question is written into the formula
-- rather than left to the calculation type: group_aggregate takes it as a
-- third argument, and both group_aggregate formulas saved on these Answers
-- pass query_filters(). A Tableau FIXED expression takes a grain and an
-- aggregation, and no filter argument at all.
--
-- ThoughtSpot features: flexible aggregation, and filters within
-- group_aggregate.
-- https://docs.thoughtspot.com/cloud/latest/formulas-aggregation-flexible
-- https://docs.thoughtspot.com/cloud/latest/aggregation-filters
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: a group_aggregate whose grain is the LoD column.
--
-- The formula saved on the Answer is
--
--   fxQuantityCategory =
--     group_aggregate ( [Quantity] , { [Category Quantity] } ,
--                       query_filters ( ) )
--
-- so the inner aggregation is grouped by category_quantity - a
-- level-of-detail column used as the grain of another level of detail.
--
-- Four CTEs and a final SELECT. qt_0 evaluates MEASURE(quantity) per grain
-- value, qt_1 maps the grain to the displayed dimensions, qt_3 joins those
-- two with EQUAL_NULL and re-aggregates with IFNULL(sum(...), 0), and qt_2
-- computes the rows the answer displays - both dimensions plus
-- MEASURE(quantity), since Quantity is a column of this answer too - which
-- the final SELECT left-joins the qt_3 result onto.
--
-- tableau/02 builds its two-stage result from inline derived tables joined
-- with IS NOT DISTINCT FROM. ThoughtSpot names each stage as a CTE, spells
-- the null-safe join EQUAL_NULL, and uses LEFT OUTER JOIN where Tableau
-- used an inner join. qt_2 is not a fourth difference of that kind: it is
-- the display-grain stage this Answer needs because it also shows Quantity,
-- and the Tableau example has no such column.
--
-- The Answer also saves fxAggregate = average ( [Category Quantity] ), which
-- this search does not reference; it leaves no trace in the SQL.
--
-- Watch which columns MEASURE() wraps. quantity always; category_quantity
-- never - it is a dimension on this model, so it is selected bare and
-- grouped, both in qt_1 and as the GROUP BY key of qt_0.
--
-- An observation about this capture rather than documented behaviour:
-- because the grain is an aggregate-valued column, the qt_3 join key is a
-- units total rather than a business key. qt_0 groups by that total, so two
-- categories whose totals coincided would collapse into a single qt_0 row
-- holding the sum of both, and qt_3 would attach that row to every product
-- in either category. Grouping by a measure-derived column is not the same
-- as grouping by the column it partitions by; the two agree only while the
-- totals stay distinct, which nothing in the model enforces.
--
-- Search: [formula_fxQuantityCategory] [Product Category] [Product Name] [Quantity]
-- ----------------------------------------------------------------------------
WITH
  `qt_2` AS (
    SELECT
      `ta_1`.`product_category` AS `ca_6`,
      `ta_1`.`product_name` AS `ca_7`,
      MEASURE(`ta_1`.`quantity`) AS `ca_8`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    GROUP BY
      `ca_6`,
      `ca_7`
  ),
  `qt_1` AS (
    SELECT
      `ta_1`.`category_quantity` AS `ca_3`,
      `ta_1`.`product_category` AS `ca_4`,
      `ta_1`.`product_name` AS `ca_5`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    GROUP BY
      `ca_3`,
      `ca_4`,
      `ca_5`
  ),
  `qt_0` AS (
    SELECT
      MEASURE(`ta_1`.`quantity`) AS `ca_1`,
      `ta_1`.`category_quantity` AS `ca_2`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    GROUP BY `ca_2`
  ),
  `qt_3` AS (
    SELECT
      IFNULL(sum(`ta_2`.`ca_1`), 0) AS `ca_9`,
      `ta_3`.`ca_4` AS `ca_10`,
      `ta_3`.`ca_5` AS `ca_11`
    FROM `qt_1` AS `ta_3`
      LEFT OUTER JOIN `qt_0` AS `ta_2`
        ON (EQUAL_NULL(`ta_3`.`ca_3`,`ta_2`.`ca_2`))
    GROUP BY
      `ca_10`,
      `ca_11`
  )
SELECT
  `ta_4`.`ca_6` AS `ca_12`,
  `ta_4`.`ca_7` AS `ca_13`,
  `ta_5`.`ca_9` AS `ca_14`,
  `ta_4`.`ca_8` AS `ca_15`
FROM `qt_2` AS `ta_4`
  LEFT OUTER JOIN `qt_3` AS `ta_5`
    ON (
      (EQUAL_NULL(`ta_4`.`ca_6`,`ta_5`.`ca_10`))
      AND (EQUAL_NULL(`ta_4`.`ca_7`,`ta_5`.`ca_11`))
    );

-- ----------------------------------------------------------------------------
-- Query 2: the same LoD read with no formula at all.
--
-- One statement, no CTE, no join. category_quantity, product_name and
-- product_category are selected bare and grouped; quantity and the
-- contribution ratio come through MEASURE(). tableau/03-sets.sql needed a
-- subquery and a join to get a category-grain value usable as a dimension,
-- because there the LoD lived in the workbook. Here it lives in the model,
-- and the whole two-stage apparatus of Query 1 collapses to this.
--
-- Grouping by category_quantity does not change the grain, because it is
-- functionally determined by product_category - the column it partitions
-- by - which is grouped as well. That is a property of this definition and
-- not a general licence: a GROUP BY key whose value is an aggregate leaves
-- the grain unchanged only while the column it partitions by is grouped
-- too. Written out in plain SQL the column could not be a GROUP BY key at
-- the same query level at all, since a window function cannot be one; it
-- would take a subquery. Declaring it as a model dimension is what lets the
-- generated statement group by it directly.
--
-- product_to_category_contribution_ratio, the other measure here, shows the
-- divergence from a second angle. The Databricks measure names the LoD
-- column:
--
--   COALESCE(MEASURE(quantity) / NULLIF(ANY_VALUE(category_quantity), 0), 0)
--
-- The Snowflake metric of the same name does not reference its own
-- category_quantity metric; it writes the window expression out a second
-- time. One layer has a single definition of the category total, the other
-- has two copies that have to be kept in step.
--
-- Search: [Category Quantity] [Quantity] [Product Name] [Product Category] [Product To Category Contribution Ratio] sort by [Product Category] sort by [Product Name]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`category_quantity` AS `ca_1`,
  `ta_1`.`product_name` AS `ca_2`,
  `ta_1`.`product_category` AS `ca_3`,
  MEASURE(`ta_1`.`quantity`) AS `ca_4`,
  MEASURE(`ta_1`.`product_to_category_contribution_ratio`) AS `ca_5`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY
  `ca_1`,
  `ca_2`,
  `ca_3`
ORDER BY
  `ca_3` ASC,
  `ca_2` ASC;

-- ----------------------------------------------------------------------------
-- Query 3: filtering on the LoD.
--
-- The predicate lands in WHERE:
--
--   WHERE `ta_1`.`category_quantity` >= 2006000
--
-- Plain SQL would not take that at the same query level either - WHERE is
-- evaluated before windows are computed, so a window function cannot be
-- referenced there, and the filter would need a subquery. The predicate is
-- expressible here only because the model declares the column as a
-- dimension. How the engine then orders the window against the filter is
-- settled inside the model. This statement does not show it, because it filters on the LoD column itself; a filter on any other dimension does.
--
-- Which is the column the two definitions quoted in the header disagree
-- about: the Databricks one calls it "independent of query GROUP BY", the
-- Snowflake one says its copy "RESPECTS query filters -- unlike the DBX
-- source which suppresses them". This capture cannot adjudicate that - it
-- filters on the LoD column itself, and no SQL in this folder was captured
-- against Snowflake. What it does show is how little the query says about
-- it. The predicate compiles, the statement runs, and which rows reached
-- the category total was decided by a definition the SQL never names.
--
-- No MEASURE() appears in this statement. The answer is three dimension
-- columns, one of which is an aggregate the model computed, sorted by it.
--
-- The saved Answer also carries two formulas the search never references, so
-- they leave no trace in the SQL:
--
--   lodQTY  = group_aggregate ( [Quantity] , { [Product Category] } ,
--                               query_filters ( ) )
--   fxRatio = [Quantity] / [formula_lodQTY]
--
-- lodQTY is the formula-side counterpart of the model's LoD column, written
-- with the partition key as its grain rather than, as in Query 1, the LoD
-- column itself.
--
-- Search: [Product Name] [Product Category] [Category Quantity] >= 2006000 [Category Quantity] sort by [Category Quantity]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`product_name` AS `ca_1`,
  `ta_1`.`product_category` AS `ca_2`,
  `ta_1`.`category_quantity` AS `ca_3`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE `ta_1`.`category_quantity` >= 2006000
GROUP BY
  `ca_1`,
  `ca_2`,
  `ca_3`
ORDER BY `ca_3` ASC;
