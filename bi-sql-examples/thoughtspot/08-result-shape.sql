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
-- ThoughtSpot BI SQL examples: result shape
--
-- Three saved Answers over the same Metric View that differ in how the result is
-- presented: a KPI tile with no dimension, a straight table, and a pivot table.
-- The question they are here to answer is how much of that presentation reaches
-- the SQL.
--
-- Almost none of it. The two table Answers compile to the same shape, differing
-- only in the order of the SELECT list. The KPI differs by more - its search
-- names neither the dimension nor one of the two measures, so it projects a
-- single scalar aggregate and drops the GROUP BY with the dimension. No LIMIT appears anywhere, so no display row cap leaks into the SQL either.
--
-- One caveat on the scope of that claim. None of these three Answers has pivot
-- One caveat on the scope of that claim. The pivot Answer has no totals
-- configured - its saved chart state carries a totals-summary block whose
-- row and column grand-total and sub-total flags are all false - so nothing
-- here shows what a totals row would compile to, or whether it would reach
-- the SQL at all. These captures speak to projection and grouping, not totals.
-- SQL at all. These captures speak to projection and grouping, not to totals.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: a KPI tile, no dimension.
--
-- The search names one measure and nothing else. The SQL is one MEASURE()
-- call with no GROUP BY, no WHERE, no ORDER BY and no LIMIT - the whole
-- statement is a single scalar aggregate over the Metric View.
--
-- Note where the missing GROUP BY comes from. It is the search that has no
-- dimension, not the tile: the SQL follows the projection, and choosing a
-- KPI visualization is not what removed the grouping.
--
-- Dropping it is not merely cosmetic. A scalar aggregate returns exactly one
-- row whatever the data does, including when a filter matches nothing, while
-- the grouped form in Queries 2 and 3 returns no rows at all in that case.
-- A KPI tile always has a cell to fill.
--
-- The Metric View declares employee_count as
-- COUNT(DISTINCT orders.employees.EMPLOYEE_ID). Ungrouped, MEASURE() expands
-- to one distinct count over everything the view joins. That matters for
-- Query 3: a distinct count is not additive, so this value cannot be
-- recovered by adding up the per-state rows the other two queries return.
--
-- Search: [Employee Count]
-- ----------------------------------------------------------------------------
SELECT MEASURE(`ta_1`.`employee_count`) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`;

-- ----------------------------------------------------------------------------
-- Query 2: a straight table, one dimension and two measures.
--
-- Adding a dimension to the search adds a GROUP BY; this search also added
-- FROM clause and the absence of WHERE, ORDER BY and LIMIT are as in Query 1.
-- The MEASURE() calls are not: this search names two measures where Query 1's
-- named one, so the KPI differs by a measure as well as by the dimension.
-- its projection and in the GROUP BY that the projected dimension requires,
-- and in nothing else.
--
-- Two details of the shape are worth naming.
--
-- The GROUP BY names the output alias, not the underlying column and not an
-- ordinal. Databricks resolves that; standard SQL does not, so it is a thing
-- to fix when replaying these statements against another engine.
--
-- And there is no ORDER BY, so the row order of the result is unspecified.
-- Whatever order the table displays was not requested from the warehouse.
-- Nor is there a LIMIT. Across the wider set of captures these examples were
-- drawn from, every LIMIT sits inside a top-N the search explicitly asked
-- for; none of them is a display cap. That is an observation about these
-- captures rather than a documented guarantee, but it is the pattern here.
--
-- Search: [Amount] [Employee Count] [Customer State]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`customer_state` AS `ca_1`,
  MEASURE(`ta_1`.`amount`) AS `ca_2`,
  MEASURE(`ta_1`.`employee_count`) AS `ca_3`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 3: a pivot table.
--
-- Nothing in this statement distinguishes a pivot from a straight table. It is
-- the same shape as Query 2 - same FROM, same GROUP BY on the same single
-- dimension, the same two MEASURE() calls, no WHERE, no ORDER BY, no LIMIT - and
-- the two searches that produced them are byte-identical.
--
-- The one difference is the order of the SELECT list, and it is not cosmetic.
-- This Answer projects employee_count before amount; Query 2 projects them the
-- other way round. Because the generated aliases are positional, ca_2 names a
-- different measure in each statement. Anything binding to these results by
-- alias would silently swap the two measures between two Answers that asked the
-- same question.
--
-- So the visual form reaches the SQL through exactly one channel here - column
-- order - and that channel is the one that can quietly give a consumer the wrong
-- number.
--
-- What this capture does NOT show: this Answer has no pivot summaries configured,
-- so there is no totals row in play. Whether a totals row would compile to a
-- ROLLUP, a GROUPING SETS, a second statement, or nothing at all is not settled
-- by anything here.
--
-- Search: [Amount] [Employee Count] [Customer State]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`customer_state` AS `ca_1`,
  MEASURE(`ta_1`.`employee_count`) AS `ca_2`,
  MEASURE(`ta_1`.`amount`) AS `ca_3`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`;
