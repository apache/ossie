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
-- Tableau BI SQL examples: Top N filters
--
-- Shows how Tableau's low-code filter UI turns into SQL, from a plain aggregate
-- up to a top-N filter that needs a join and subquery. The last query shows an
-- optimization Tableau applies when the filter dimension matches the
-- visualization dimension.
--
-- Tableau feature: Filter Data from Your Views (the "Top" tab).
-- https://help.tableau.com/current/pro/desktop/en-us/filtering.htm
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: simple aggregation.
-- Visualize Category by the Total Quantity measure. A plain aggregate that
-- calls the MEASURE function.
-- ----------------------------------------------------------------------------
SELECT
  `orders_metrics`.`category` AS `category`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`
FROM
 `orders_metrics` `orders_metrics`
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 2: simple WHERE filter.
-- A low-code date-range filter (years 2023 and 2024) becomes a plain WHERE.
-- Filters expressible as a simple WHERE clause tend to port across BI vendors.
-- ----------------------------------------------------------------------------
SELECT
  `orders_metrics`.`category` AS `category`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`
FROM
  `orders_metrics` `orders_metrics`
WHERE
  (YEAR(`orders_metrics`.`order_date`) IN (2023, 2024))
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 3: top-N filter on a different dimension (join + subquery).
-- Keep the top 2 states by Total Revenue while visualizing Category by Total
-- Quantity. A subquery ranks states by the revenue measure; the main query
-- joins to it to apply the filter before aggregating.
-- ----------------------------------------------------------------------------
SELECT
  `orders_metrics`.`category` AS `category`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`
FROM
  `orders_metrics` `orders_metrics`
    JOIN (
      SELECT
        `orders_metrics`.`state_id` AS `state_id`,
        (MEASURE(`orders_metrics`.`total_revenue`)) AS `x__alias__0`
      FROM
        `orders_metrics` `orders_metrics`
      GROUP BY
        1
      ORDER BY
        `x__alias__0` DESC,
        `state_id` ASC
      LIMIT 2
    ) `t0`
      ON (`orders_metrics`.`state_id` = `t0`.`state_id`)
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 4: top-N filter on the same dimension (folded).
-- Keep the top 2 categories by Total Revenue while visualizing Category. Because
-- the filter dimension equals the visualization dimension, Tableau folds the
-- filter subquery into the main query - no join needed.
--
-- Note: this fold corresponds to Query 3's join-and-subquery technique, not to
-- its specific result (Query 3 filters top-2 states; this filters top-2
-- categories). The fold is valid only under the stable-domain assumption: that a
-- dimension's domain is fixed regardless of the other measures and dimensions in
-- the query.
--
-- Separately, Query 3 joins the top-N subquery with an equality predicate
-- (state_id = t0.state_id), not IS NOT DISTINCT FROM. That matches a null-safe
-- join only when state_id has no NULLs. This is a distinct assumption from the
-- fold's: the fold relies on a stable domain, while this plain-= join relies on
-- a NULL-free join key.
--
-- Both optimizations are unsafe against multi-table models behind opaque
-- interfaces, where adding or removing a measure can change the dimension domain.
-- ----------------------------------------------------------------------------
SELECT
  `orders_metrics`.`category` AS `category`,
  (MEASURE(`orders_metrics`.`total_quantity`)) AS `total_quantity`,
  (MEASURE(`orders_metrics`.`total_revenue`)) AS `x__alias__0`
FROM
  `orders_metrics` `orders_metrics`
GROUP BY
  1
ORDER BY
  `x__alias__0` DESC,
  `category` ASC
LIMIT 2;
