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
-- ThoughtSpot BI SQL examples: date filters and grains
--
-- How date filters and date granularities compile to SQL against the Metric View.
-- All three read order_date, which the Metric View carries through its join to
-- the order table.
--
-- ThoughtSpot feature: the search keyword reference.
-- https://docs.thoughtspot.com/cloud/latest/keywords
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: a relative date keyword.
--
-- Nothing relative survives into the SQL. There is no CURRENT_DATE, no
-- add_months, no date arithmetic of any kind - the keyword was resolved
-- before the statement was sent, and what the warehouse receives is two
-- constants, to_date('2026-08-01', 'yyyy-MM-dd') and to_date('2026-09-01',
-- 'yyyy-MM-dd'), compared with >= and <. The format string is explicit, so
-- the literals do not depend on a session date format.
--
-- That has a consequence for anyone treating captured SQL as an artifact.
-- This statement does not mean 'last month'; it means August 2026, and it
-- will still mean August 2026 next year. The relative semantics live in
-- ThoughtSpot, not in the warehouse, so replaying the SQL is not a
-- substitute for replaying the search.
--
-- Note the bounds are half-open - >= the first of the month, < the first of
-- the next - rather than BETWEEN. Query 2 shows why that is the careful
-- choice. This is also the only query here with no GROUP BY: a single
-- ungrouped aggregate, with the measure read through MEASURE().
--
-- Search: [Amount] [Order Date].'last month'
-- ----------------------------------------------------------------------------
SELECT MEASURE(`ta_1`.`amount`) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE (
  `ta_1`.`order_date` >= to_date('2026-08-01', 'yyyy-MM-dd')
  AND `ta_1`.`order_date` < to_date('2026-09-01', 'yyyy-MM-dd')
);

-- ----------------------------------------------------------------------------
-- Query 2: an explicit date range, plus a dimension.
--
-- The user typed 09/01/2000 and 09/30/2000. Neither date reaches the SQL as
-- typed: the range is rendered half-open, >= to_date('2000-09-01') AND
-- < to_date('2000-10-01'), so the end date is advanced by a day and made
-- exclusive. That is the rendering that does not silently drop rows carrying a
-- time of day, and it is why the typed end date appears nowhere in the
-- statement.
--
-- The predicate sits in WHERE, so it selects the order lines before
-- MEASURE(amount) - SUM(source.LINE_TOTAL) in the Metric View - aggregates
-- them, not after.
--
-- Both of these queries leave order_date bare on the left of the comparison and
-- put the work on the literal side. Two contrasts are worth drawing. It is the
-- opposite of what ThoughtSpot does for the string predicates in
-- 01-filters-and-predicates.sql, where all four wrap the column in LOWER(). And
-- it is the opposite of what Tableau does for a date filter in
-- bi-sql-examples/tableau/01-top-n-filters.sql, which compiles to
-- YEAR(order_date) IN (2023, 2024) - a different filter, a membership test
-- rather than a range, but one that puts the function on the column.
--
-- Search: [Amount] by [Product Category] [Order Date] between [Order Date].09/01/2000 and [Order Date].09/30/2000
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`product_category` AS `ca_1`,
  MEASURE(`ta_1`.`amount`) AS `ca_2`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE (
  `ta_1`.`order_date` >= to_date('2000-09-01', 'yyyy-MM-dd')
  AND `ta_1`.`order_date` < to_date('2000-10-01', 'yyyy-MM-dd')
)
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 3: seven granularities of one column.
--
-- The search asks for order_date at all seven granularities at once, and the
-- SELECT list buckets the same column three different ways. Hourly, weekly
-- and monthly use DATE_TRUNC, each casting the column to TIMESTAMP first.
-- Daily uses DATE(). Quarterly and yearly use neither: they build a string
-- and cast it back to DATE. Quarterly's arithmetic,
-- FLOOR((MONTH - 1) / 3) * 3 + 1, yields 1, 4, 7 or 10 - the first month of
-- the quarter - while yearly concatenates a literal 1 and calls no MONTH()
-- at all.
--
-- Two details in that construction are worth naming. The divisor is written
-- NULLIF(3,0), a divide-by-zero guard applied to the literal 3, which cannot
-- be zero. And the month is not zero-padded, though the day beside it is the
-- literal '-01', so the intermediate string for the third quarter of 2000
-- reads '2000-7-01' - in the same file where Queries 1 and 2 hand to_date an
-- explicit 'yyyy-MM-dd'. Both read like a template emitted without folding.
--
-- The larger point is in the GROUP BY. It lists all seven date aliases, and
-- the first of them is the raw, untruncated order_date. The other six
-- are functionally determined by it, so they add no grouping at all: the
-- grain of this result is detailed order_date, and the coarser columns ride
-- along as labels on it.
--
-- That matters because the measure is employee_count, which the Metric View
-- defines as COUNT(DISTINCT orders.employees.EMPLOYEE_ID). A distinct count
-- is not additive, so these rows cannot be summed up into a monthly distinct
-- employee count. The monthly column here labels a detailed-grain number.
--
--
-- One more thing to watch: the hourly bucket casts order_date to TIMESTAMP
-- before truncating. If order_date carries no time-of-day component, the
-- hourly and daily buckets identify the same instant for every row and
-- differ only in type. This is an observation about this capture, not
-- documented behaviour.
--
-- Search: [Employee Count] [Order Date].detailed [Order Date].hourly [Order Date].daily [Order Date].weekly [Order Date].monthly [Order Date].quarterly [Order Date].yearly
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`order_date` AS `ca_1`,
  DATE_TRUNC('HOUR', CAST(`ta_1`.`order_date` as TIMESTAMP)) AS `ca_2`,
  DATE(`ta_1`.`order_date`) AS `ca_3`,
  DATE_TRUNC('WEEK', CAST(`ta_1`.`order_date` as TIMESTAMP)) AS `ca_4`,
  DATE_TRUNC('MONTH', CAST(`ta_1`.`order_date` as TIMESTAMP)) AS `ca_5`,
  CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE) AS `ca_6`,
  CAST(YEAR(`ta_1`.`order_date`) || '-' || 1 || '-01' AS DATE) AS `ca_7`,
  MEASURE(`ta_1`.`employee_count`) AS `ca_8`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY
  `ca_1`,
  `ca_2`,
  `ca_3`,
  `ca_4`,
  `ca_5`,
  `ca_6`,
  `ca_7`;
