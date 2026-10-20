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
-- Tableau BI SQL examples: LoDs for two-stage aggregation
--
-- Measures compose with Tableau's own FIXED level-of-detail (LoD) calculations.
-- This computes a measure at a per-state grain, then averages that result: for
-- each category, the average across its states of total revenue.
--
-- The generated SQL is two-stage: an inner query computes the per-state measure
-- (MEASURE(total_revenue) grouped by state_id), and the outer query applies the
-- second aggregation (AVG) after joining on the state grain. The join uses
-- IS NOT DISTINCT FROM so NULL state_id values match.
--
-- Tableau feature: FIXED Level of Detail Expressions.
-- https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_fixed.htm
-- ============================================================================

SELECT
  `t0`.`category` AS `category`,
  AVG(`t1`.`x_measure__1`) AS `average_of_total_revenues_by_state`
FROM
  (
    SELECT
      `orders_metrics`.`category` AS `category`,
      `orders_metrics`.`state_id` AS `state_id`
    FROM
      `orders_metrics` `orders_metrics`
    GROUP BY
      1,
      2
  ) `t0`
    JOIN (
      SELECT
        `orders_metrics`.`state_id` AS `state_id`,
        (MEASURE(`orders_metrics`.`total_revenue`)) AS `x_measure__1`
      FROM
        `orders_metrics` `orders_metrics`
      GROUP BY
        1
    ) `t1`
      ON (`t0`.`state_id` IS NOT DISTINCT FROM `t1`.`state_id`)
GROUP BY
  1;
