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
-- Tableau BI SQL examples: multi-table analysis (joins + relationships)
--
-- A two-table model (tickets and sales), each with its own measures. Tableau
-- offers two ways to combine them:
--   * Low-code joins: the user forces an explicit join path and type.
--   * Relationships: Tableau issues a query per level of detail and picks the
--     join shape itself, allowing more flexible analysis.
--
-- With native SQL measures, the SQL layer handles measure de-duplication after
-- the join (no double counting across a many-to-many join), while the BI tool
-- keeps control of the join path and type.
--
-- Tableau feature: Relate Your Data (relationships).
-- https://help.tableau.com/current/pro/desktop/en-us/relate_tables.htm
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: low-code join, measures from both tables.
-- The user forces a many-to-many join on region. Region sales are plotted with
-- Ticket Count (a tickets measure) and Total Sales (a sales measure). Native
-- SQL measures avoid any measure duplication across the M:M join.
-- ----------------------------------------------------------------------------
SELECT
  `sales`.`region` AS `region_sales`,
  (MEASURE(`tickets`.`ticket_count`)) AS `ticket_count`,
  (MEASURE(`sales`.`total_sales`)) AS `total_sales`
FROM
  `tickets` `tickets`
    JOIN `sales` `sales`
      ON (`tickets`.`region` = `sales`.`region`)
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 2: de-duplication preserved when the join key is not a group-by field.
-- Same low-code join, but grouped by sale_id (a non-join-key dimension from the
-- other table). Measure de-duplication still holds: Ticket Count is not
-- inflated by the M:M join.
-- ----------------------------------------------------------------------------
SELECT
  `sales`.`sale_id` AS `sale_id`,
  (MEASURE(`tickets`.`ticket_count`)) AS `ticket_count`,
  (MEASURE(`sales`.`total_sales`)) AS `total_sales`
FROM
  `tickets` `tickets`
    JOIN `sales` `sales`
      ON (`tickets`.`region` = `sales`.`region`)
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 3: relationships, one query per level of detail (ticket count case).
-- Using the relationship feature, Tableau issues a separate query per measure
-- grain. This is the ticket-count query, which spans tables: it first computes
-- the distinct set of regions from sales, joins that back to tickets to avoid
-- row duplication, then applies the measure aggregation.
-- ----------------------------------------------------------------------------
SELECT
  `t0`.`region` AS `region`,
  (MEASURE(`tickets`.`ticket_count`)) AS `ticket_count`
FROM
  `tickets` `tickets`
    LEFT OUTER JOIN (
      SELECT
        `sales`.`region` AS `region`
      FROM
        `sales` `sales`
      GROUP BY
        1
    ) `t0`
      ON (`tickets`.`region` = `t0`.`region`)
GROUP BY
  1;

-- ----------------------------------------------------------------------------
-- Query 4: relationships with dimensions from both sides of the join.
-- Ticket Count by region (grouped on the tickets side) and sale_id (from sales).
-- Tableau computes the unique (region, sale_id) pairs and joins them back to
-- the main table to avoid measure duplication from the many-to-many join.
--
-- This query is an example of a measure column (ticket_count) passing through a
-- derived table unevaluated: it is referenced inside the derived table `t0`
-- without being aggregated.
-- ----------------------------------------------------------------------------
SELECT
  `t2`.`region` AS `region`,
  `t2`.`sale_id` AS `sale_id`,
  (MEASURE(`t0`.`ticket_count`)) AS `ticket_count`
FROM
  (
    SELECT
      `tickets`.`region` AS `region`,
      `tickets`.`ticket_count` AS `ticket_count`,
      `tickets`.`region` AS `region__tickets_`
    FROM
      `tickets` `tickets`
  ) `t0`
    JOIN (
      SELECT
        `t1`.`region__tickets_` AS `region__tickets_`,
        MIN(`sales`.`region`) AS `region`,
        `sales`.`sale_id` AS `sale_id`
      FROM
        (
          SELECT
            `tickets`.`region` AS `region`,
            `tickets`.`region` AS `region__tickets_`
          FROM
            `tickets` `tickets`
        ) `t1`
          LEFT OUTER JOIN `sales` `sales`
            ON (`t1`.`region__tickets_` = `sales`.`region`)
      GROUP BY
        1,
        3
    ) `t2`
      ON (`t0`.`region__tickets_` IS NOT DISTINCT FROM `t2`.`region__tickets_`)
GROUP BY
  1,
  2;

-- ----------------------------------------------------------------------------
-- Query 5: the same cross-table analysis with a plain aggregate (MAX), for
-- contrast.
-- Tableau already applies a related optimization for ordinary aggregates such as
-- MAX. This query does the same cross-table analysis as Query 4 but emits a
-- simpler shape: it aggregates a dimension (MAX(ticket_id)) rather than passing a
-- measure column through a derived table unevaluated. Measure-type awareness
-- would let Tableau rely on the engine's measure de-duplication, so the M:M
-- join's duplicate rows do not inflate the measure, and simplify the Query 4
-- shape similarly.
-- ----------------------------------------------------------------------------
SELECT
  `sales`.`region` AS `region`,
  `sales`.`sale_id` AS `sale_id`,
  MAX(`t0`.`ticket_id`) AS `max_ticket`
FROM
  (
    SELECT
      `tickets`.`region` AS `region`,
      `tickets`.`ticket_id` AS `ticket_id`,
      `tickets`.`region` AS `region__tickets_`
    FROM
      `tickets` `tickets`
  ) `t0`
    LEFT OUTER JOIN `sales` `sales`
      ON (`t0`.`region__tickets_` = `sales`.`region`)
GROUP BY
  1,
  2;

-- ----------------------------------------------------------------------------
-- Note: constraint-driven rewrites.
-- Tableau also rewrites based on database-constraint metadata and user-asserted
-- metadata. Asserting many-to-one cardinality reduces the number of inner
-- subqueries Tableau inserts to avoid measure duplication; asserting "all
-- records match" on referential integrity lets Tableau lower left joins to
-- inner joins. The source doc illustrates these with the UI rather than a
-- distinct captured query, so no separate SQL is reproduced here.
-- ----------------------------------------------------------------------------
