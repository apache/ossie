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
-- ThoughtSpot BI SQL examples: period over period
--
-- Three captures that compare one period against another. They fall into
-- two shapes, and the wording of the search is what selects between them.
--
-- A 'growth of' search compiles to a single pass: LAG() by one row over
-- the aggregated measure, wrapped in a CASE that checks the two rows
-- really are adjacent periods. A 'vs' search compiles to one CTE per
-- period plus a spine CTE of the shared dimension, left-joined together -
-- three scans of the view rather than one conditional aggregation.
--
-- Neither shape uses a date-interval offset. Both growth queries lag by
-- one ROW and then check afterwards which row they landed on. That is
-- safe here only because each GROUP BY puts exactly one row in each
-- period; the CASE covers the remaining hazard, a period missing entirely.
--
-- Measures are read through MEASURE(), the Databricks metric view
-- accessor. Amount is defined in the view as SUM(source.LINE_TOTAL).
--
-- ThoughtSpot feature: the search keyword reference.
-- https://docs.thoughtspot.com/cloud/latest/keywords
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: daily growth - LAG with an adjacency guard.
--
-- The day bucket is DATEDIFF(DATE(order_date), '1970-01-01'), an integer
-- count of days since the epoch. That one expression is the GROUP BY key,
-- the window's ORDER BY key, and the value the guard tests. Alongside it,
-- ca_2 is max(DATE(order_date)) - the readable date for the bucket, a max
-- only because the GROUP BY key is the day number rather than the date.
--
-- The growth column is, with the day expression abbreviated,
--
--   (MEASURE(amount) - LAG(MEASURE(amount), 1, NULL) OVER (ORDER BY day))
--   / NULLIF(LAG(MEASURE(amount), 1, NULL) OVER (ORDER BY day), 0.0)
--
-- and the CASE around it is the part worth reading: the ratio is returned
-- only when the current day number minus the lagged day number is exactly
-- 1.0, and NULL otherwise.
--
-- That guard is load-bearing. LAG(..., 1) is ROW-positional, not
-- date-interval: it reaches the previous row of the result, which is the
-- previous day that HAS orders. A day with no orders produces no row at
-- all. Without the guard, the first day after a gap would be compared
-- against a day further back and reported as one day's growth. With it,
-- that row is NULL - missing rather than wrong. Note the consequence:
-- a gap is not zero-filled, and the day after it reports no growth.
--
-- The row-positional offset is otherwise sound here because GROUP BY and
-- the window's ORDER BY are the same expression, so exactly one row
-- exists per day. That precondition is what a reimplementation has to
-- preserve; the guard detects gaps, but it would not detect duplicate
-- rows within a period, and nothing else in the query would either.
--
-- Division by zero is handled by NULLIF on the denominator, so a day
-- after a zero-amount day gives NULL rather than an error. The result is
-- a fraction - nothing multiplies it by 100.
--
-- Finally, the statement has no ORDER BY of its own. The window is
-- ordered; the result set is not.
--
-- Search: growth of [Amount] by [Order Date] [Order Date].daily
-- ----------------------------------------------------------------------------
SELECT
  DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01') AS `ca_1`,
  max(DATE(`ta_1`.`order_date`)) AS `ca_2`,
  CASE
    WHEN (DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01') - LAG(DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01'), 1, NULL) OVER (ORDER BY DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01'))) = 1.0 THEN ((MEASURE(`ta_1`.`amount`) - LAG(MEASURE(`ta_1`.`amount`), 1, NULL) OVER (ORDER BY DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01'))) / NULLIF(LAG(MEASURE(`ta_1`.`amount`), 1, NULL) OVER (ORDER BY DATEDIFF(DATE(`ta_1`.`order_date`), '1970-01-01')),0.0))
    ELSE NULL
  END AS `ca_3`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 2: year over year - the same shape with a PARTITION BY.
--
-- Structurally this is Query 1 plus a partition. The window is PARTITION
-- BY <quarter number> ORDER BY <year>, so one row back inside a partition
-- is the same quarter of the previous year, and the CASE guard checks
-- that the year difference is exactly 1.0. Same NULLIF on the
-- denominator, same NULL when the chain breaks: a quarter absent from the
-- data yields NULL for the next occurrence of that quarter rather than
-- comparing across a two-year gap.
--
-- The offset is row-positional again, and the GROUP BY again makes that
-- safe - the query groups by quarter and year, which are exactly the
-- partition key and the ordering key, so each partition holds one row per
-- year.
--
-- How the quarter is derived is the other thing to read. The SQL builds a
-- quarter-start DATE by string concatenation,
--
--   CAST(YEAR(order_date) || '-'
--        || ((FLOOR(((MONTH(order_date) - 1) / NULLIF(3,0))) * 3) + 1)
--        || '-01' AS DATE)
--
-- and then takes that date apart again: CEIL(MONTH(...) / 3.0) for the
-- quarter number, YEAR(...) for the year. Both keys could have come from
-- order_date directly. The round trip does buy ca_3, max(quarter start),
-- a real DATE for the ORDER BY the search asked for.
--
-- Every division in the quarter derivation is wrapped in NULLIF against a constant -
-- NULLIF(3,0) inside the CAST, NULLIF(3.0,0.0) in the CEIL around it -
-- and neither divisor can ever be zero. The guard looks to be emitted
-- for every division, without checking whether the divisor is a literal;
-- that is an observation about this capture, not documented behaviour.
-- 01-filters-and-predicates.sql shows the same habit, applying its LIKE
-- escaping chain to a constant literal.
--
-- The quarter-start expression appears eleven times in this one
-- statement, because the shape names no intermediate - no CTE, no lateral
-- and no named window.
--
-- Two differences from Query 1 come straight from the search. The measure
-- itself is projected (ca_5) because [Amount] is repeated after 'year
-- over year', and the rows are sorted by max(quarter start) ascending,
-- which is chronological and therefore interleaves the window partitions.
-- The output order and the window order are independent here.
--
-- Search: growth of [Amount] by [Order Date] [Order Date].quarterly year over year [Amount] sort by [Order Date] [Order Date].quarterly
-- ----------------------------------------------------------------------------
SELECT
  CEIL((MONTH(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) / NULLIF(3.0,0.0))) AS `ca_1`,
  YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) AS `ca_2`,
  max(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) AS `ca_3`,
  CASE
    WHEN (YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) - LAG(YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)), 1, NULL) OVER (PARTITION BY CEIL((MONTH(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) / NULLIF(3.0,0.0))) ORDER BY YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)))) = 1.0 THEN ((MEASURE(`ta_1`.`amount`) - LAG(MEASURE(`ta_1`.`amount`), 1, NULL) OVER (PARTITION BY CEIL((MONTH(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) / NULLIF(3.0,0.0))) ORDER BY YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)))) / NULLIF(LAG(MEASURE(`ta_1`.`amount`), 1, NULL) OVER (PARTITION BY CEIL((MONTH(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE)) / NULLIF(3.0,0.0))) ORDER BY YEAR(CAST(YEAR(`ta_1`.`order_date`) || '-' || ((FLOOR(((MONTH(`ta_1`.`order_date`) - 1) / NULLIF(3,0))) * 3) + 1) || '-01' AS DATE))),0.0))
    ELSE NULL
  END AS `ca_4`,
  MEASURE(`ta_1`.`amount`) AS `ca_5`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY
  `ca_1`,
  `ca_2`
ORDER BY `ca_3` ASC;

-- ----------------------------------------------------------------------------
-- Query 3: two periods side by side - three CTEs and two joins.
--
-- This is the other shape. Rather than one pass with conditional
-- aggregation, 'vs' produces one CTE per period - qt_0 filtered to 2019,
-- qt_1 to 2020 - and a third, qt_2, holding the distinct product names
-- across both years. The final SELECT starts from qt_2 and LEFT OUTER
-- JOINs the other two onto it. Three scans of the metric view - the spine and one per period.
--
-- The spine is the point of the shape. A product that sold in 2019 but
-- not in 2020 still has a row in qt_2, so it survives with a NULL in the
-- 2020 column; an inner join of qt_0 to qt_1 would drop it. This is a
-- FULL OUTER JOIN written as a spine plus LEFT joins - the form that
-- keeps working unchanged when a third and fourth period are added.
--
-- The join predicate is EQUAL_NULL(a, b) rather than a = b, so a NULL
-- product name matches a NULL instead of dropping out. The Tableau file
-- 02-lod-two-stage-aggregation.sql spells the same intent as
-- IS NOT DISTINCT FROM.
--
-- Read the year filter literally:
--
--   LOWER(CAST(YEAR(order_date) AS String)) = '2019'
--
-- The year is extracted as a number, cast to a string, lowercased, and
-- compared to a string literal. LOWER() over decimal digits does nothing.
-- This is the lowercase-the-column pattern of
-- 01-filters-and-predicates.sql, with the same consequence: it is a
-- function of order_date being compared, so statistics on order_date
-- cannot narrow it.
--
-- Each period CTE carries the filter twice - the shared scope
-- (year = 2020 OR year = 2019) AND then its own year - where the second
-- conjunct implies the first. The shared scope is redundant in qt_0 and
-- qt_1; in qt_2 it is the only filter, and there it does the work of
-- keeping products that sold in neither year out of the spine.
--
-- Three scans, and the generator chose them - it was not forced to.
--
-- One obvious one-pass form is genuinely unavailable:
-- SUM(CASE WHEN year = 2019 THEN MEASURE(amount) END) is rejected outright,
-- because nesting an aggregate inside another is an error
-- ([NESTED_AGGREGATE_FUNCTION], SQLSTATE 42607). Reaching past the measure
-- to its own expression is not an option either - LINE_TOTAL is not
-- addressable through the view.
--
-- But a one-pass form does exist. Databricks accepts an aggregate FILTER
-- clause over a metric view measure:
--
--   MEASURE(amount) FILTER (WHERE YEAR(order_date) = 2019)
--
-- Run side by side with the statement below, that form returns the same 77
-- rows - identical in both directions under EXCEPT - from a plan carrying a
-- third of the scan nodes. So the shape here reflects how this generator
-- composes period scopes, not a cost that reading a measure by name
-- imposes. Worth knowing before citing multi-pass period comparison as an
-- inherent price of defining measures outside the tool: on this engine it
-- is not.
--
-- Lastly, this query computes no growth at all. It returns the two period
-- values and leaves the arithmetic to whatever reads the result - and the
-- SQL result does not say which column is which year. ca_7 comes from qt_0
-- (2019) and ca_8 from qt_1 (2020), the reverse of the order the search
-- names them in.
--
-- Search: [Amount] ( [Order Date].2020 vs [Order Date].2019 ) [Product Name]
-- ----------------------------------------------------------------------------
WITH
  `qt_2` AS (
    SELECT `ta_1`.`product_name` AS `ca_5`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    WHERE ((
      LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2020'
      OR LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2019'
    ))
    GROUP BY `ca_5`
  ),
  `qt_0` AS (
    SELECT
      MEASURE(`ta_1`.`amount`) AS `ca_1`,
      `ta_1`.`product_name` AS `ca_2`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    WHERE (
      ((
        LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2020'
        OR LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2019'
      ))
      AND LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2019'
    )
    GROUP BY `ca_2`
  ),
  `qt_1` AS (
    SELECT
      MEASURE(`ta_1`.`amount`) AS `ca_3`,
      `ta_1`.`product_name` AS `ca_4`
    FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
    WHERE (
      ((
        LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2020'
        OR LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2019'
      ))
      AND LOWER(CAST(YEAR(`ta_1`.`order_date`) AS String)) = '2020'
    )
    GROUP BY `ca_4`
  )
SELECT
  `ta_2`.`ca_5` AS `ca_6`,
  `ta_3`.`ca_1` AS `ca_7`,
  `ta_4`.`ca_3` AS `ca_8`
FROM `qt_2` AS `ta_2`
  LEFT OUTER JOIN `qt_0` AS `ta_3`
    ON (EQUAL_NULL(`ta_2`.`ca_5`,`ta_3`.`ca_2`))
  LEFT OUTER JOIN `qt_1` AS `ta_4`
    ON (EQUAL_NULL(`ta_2`.`ca_5`,`ta_4`.`ca_4`));
