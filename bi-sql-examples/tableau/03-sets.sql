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
-- Tableau BI SQL examples: sets (LoD calculation vs. built-in Set feature)
--
-- Two different low-code paths to the same result, producing different SQL.
-- Goal: graph Total Quantity split by high-revenue vs. low-revenue categories,
-- where a high-revenue category has Total Revenue >= 5000.
--
-- Key takeaway: BI tools can generate very different SQL for similar user-facing
-- capabilities, so a SQL interface to reusable semantics must be robust across
-- query shapes.
--
-- Tableau feature: Create Sets.
-- https://help.tableau.com/current/pro/desktop/en-us/sortgroup_sets_create.htm
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query A: FIXED LoD calculation used as a dimension.
-- Tableau computes Total Revenue per category in a subquery, joins it back to
-- the main table (IS NOT DISTINCT FROM handles NULL categories), and derives
-- the split dimension by applying the >= 5000 comparison to the measure.
-- ----------------------------------------------------------------------------
SELECT
  (`t0`.`x_measure__0` >= 5000) AS `is_top_category`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`
FROM
  `orders_metrics` `orders_metrics`
    JOIN (
      SELECT
        `orders_metrics`.`category` AS `category`,
        (MEASURE(`orders_metrics`.`total_revenue`)) AS `x_measure__0`
      FROM
        `orders_metrics` `orders_metrics`
      GROUP BY
        1
    ) `t0`
      ON (`orders_metrics`.`category` IS NOT DISTINCT FROM `t0`.`category`)
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query B: the built-in Set feature (in/out membership).
-- Tableau computes the high-revenue categories in a subquery that filters with
-- HAVING and emits the category plus a constant flag column. The main table
-- LEFT JOINs that subquery (the left join keeps all rows) and derives set
-- membership by testing whether the flag is non-NULL.
-- ----------------------------------------------------------------------------
SELECT
  (NOT (`t0`.`xtemp1_output` IS NULL)) AS `io_high_revenue_categories`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`
FROM
  `orders_metrics` `orders_metrics`
    LEFT OUTER JOIN (
      SELECT
        `orders_metrics`.`category` AS `category`,
        1 AS `xtemp1_output`,
        (MEASURE(`orders_metrics`.`total_revenue`)) AS `x_measure__0`
      FROM
        `orders_metrics` `orders_metrics`
      GROUP BY
        1
      HAVING
        ((MEASURE(`orders_metrics`.`total_revenue`)) >= 5000.)
    ) `t0`
      ON (`orders_metrics`.`category` IS NOT DISTINCT FROM `t0`.`category`)
GROUP BY
  1;
