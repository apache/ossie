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
-- ThoughtSpot BI SQL examples: top N and sub-selects
--
-- How a 'top N' typed into the search bar compiles, and what changes when the
-- ranked dimension is not the dimension being displayed. The direct Tableau
-- counterpart is tableau/01-top-n-filters.sql, which faces the same choice.
--
-- Both products branch on the same test. When the ranked dimension is also
-- the grouping dimension, the limit folds into the main query. When it is
-- not, a sub-select is unavoidable - and there the two diverge: Tableau
-- joins an inline view, ThoughtSpot names a CTE and applies IN (sub-query).
--
-- Measures are read through MEASURE(), the Databricks metric view accessor,
-- at both levels: the ranking measure inside the sub-select and the reported
-- measure outside it.
--
-- ThoughtSpot feature: the search keyword reference.
-- https://docs.thoughtspot.com/cloud/latest/keywords
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: top 7 folded into the main query.
--
-- The ranked dimension, Employee Name, is also the grouping dimension, so no
-- sub-select is emitted at all: one SELECT, ORDER BY the measure descending,
-- LIMIT 7. tableau/01 reaches the same shape in its Query 4 on the same
-- criterion, and projects the ranking measure as an extra output
-- column to get there. No extra column appears here because the ranking
-- measure and the displayed measure are the same one, Amount.
--
-- Worth noting where the search's explicit 'sort by [Employee Name]' landed.
-- It is the SECOND ORDER BY key, after the measure, and descending. It has
-- to be second: the LIMIT applies to this single ORDER BY, so the measure
-- must lead or a different 7 rows survive. An explicit sort and a top-N over
-- one query cannot both hold the primary key, and the top-N takes it.
-- Whether the client re-orders the 7 rows afterwards is not visible in the
-- SQL; the SQL orders by the measure first.
--
-- A side effect is that this query is fully deterministic. `ca_1` is the
-- GROUP BY key, so the pair (measure, name) is unique across rows and the
-- seven survivors are decided by the SQL rather than by the engine. That is
-- not true of the ranking sub-select in Query 2.
--
-- Search: by [Employee Name] top 7 ranked by [Amount] sort by [Employee Name]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`employee_name` AS `ca_1`,
  MEASURE(`ta_1`.`amount`) AS `ca_2`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY
  `ca_2` DESC,
  `ca_1` DESC
LIMIT 7;

-- ----------------------------------------------------------------------------
-- Query 2: top N on a different dimension - CTE plus IN (sub-query).
--
-- Display Amount by Product Category, but only for products in the top 10 by
-- # Employees over a date window. The ranked dimension (Product Name) is not
-- the grouping dimension (Product Category), so nothing can fold. ThoughtSpot
-- names the ranking as a CTE, `qt_0`, and applies it as a semi-join:
-- product_name IN (SELECT ca_1 FROM qt_0).
--
-- tableau/01's Query 3 solves the same problem with an inner JOIN to an
-- inline view. The two are equivalent only while the sub-query is distinct on
-- the join key - GROUP BY makes it so in both captures - but IN never has to
-- rely on that, because a semi-join cannot duplicate an outer row
-- however many times the sub-query repeats a value.
--
-- The [Order Date].'10 years ago' filter resolves to a half-open calendar
-- year, >= 2016-01-01 AND < 2017-01-01, and it sits INSIDE the CTE only. So
-- the ranking is scoped to that year while the Amount reported outside is
-- over all time. The sub-select decides which products qualify, not which
-- rows the reported measure sums.
--
-- Now the thing to check: the CTE's ORDER BY `ca_2` DESC LIMIT 10 carries no
-- secondary key, so ties at the tenth place are broken by whatever the engine
-- happens to produce. Tableau emits a tiebreak in the same position
-- (`state_id` ASC). And the ranking measure here is # Employees, defined in
-- the metric view as COUNT(DISTINCT orders.employees.EMPLOYEE_ID), so it takes small integer
-- values over many products - ties at a cut-off are structural, not a corner
-- case.
--
-- Across these three captures the only secondary sort key is in Query 1,
-- whose search asked for a sort; neither ranking sub-select here has one and
-- neither search asked. That is consistent with the secondary key coming from
-- the request rather than from an automatic tiebreak, and a sibling Answer
-- settles it: same model, same columns, differing only in carrying no
-- sort by clause, it compiles to a bare ORDER BY on the measure with no
-- second key at all. Either way the ranking sub-select is unordered at ties.
--
-- Search: [Amount] [Product Category] [Product Name] in ( [Product Name] top 10 [Employee Count] [Order Date].'10 years ago' )
-- ----------------------------------------------------------------------------
WITH `qt_0` AS (
  SELECT
    `ta_1`.`product_name` AS `ca_1`,
    MEASURE(`ta_1`.`employee_count`) AS `ca_2`
  FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
  WHERE (
    `ta_1`.`order_date` >= to_date('2016-01-01', 'yyyy-MM-dd')
    AND `ta_1`.`order_date` < to_date('2017-01-01', 'yyyy-MM-dd')
  )
  GROUP BY `ca_1`
  ORDER BY `ca_2` DESC
  LIMIT 10
)
SELECT
  `ta_1`.`product_category` AS `ca_3`,
  MEASURE(`ta_1`.`amount`) AS `ca_4`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE `ta_1`.`product_name` IN (
  SELECT `ca_1`
  FROM `qt_0`
)
GROUP BY `ca_3`;

-- ----------------------------------------------------------------------------
-- Query 3: the same sub-select, negated.
--
-- Identical to Query 2 apart from NOT(...) around the predicate - same CTE,
-- same outer SELECT, same aliases. This is the negation strategy seen in
-- 01-filters-and-predicates.sql: compile the positive form, then wrap it. As
-- there, three-valued logic is what the wrapper exposes.
--
-- NOT(x IN (sub-query)) is NOT IN, and NOT IN over a sub-query that can
-- return NULL is the classic trap. If `qt_0` ever yields a NULL `ca_1`, then
-- IN is TRUE on a match and UNKNOWN otherwise, never FALSE; NOT() makes that
-- FALSE or UNKNOWN, never TRUE; and the query returns no rows at all,
-- silently and without an error.
--
-- Is this capture exposed to it? Yes. The CTE projects product_name with no
-- IS NOT NULL guard, and the metric view defines that dimension as
-- products.PRODUCT_NAME with nothing constraining it to be non-NULL. An
-- unnamed product reaching the top 10 would empty the result. Whether one
-- does is a fact about the data; the SQL does not rule it out.
--
-- A second NULL effect applies unconditionally, on the outer side: a row
-- whose product_name is NULL gives UNKNOWN and is dropped, so unnamed
-- products are absent from a 'not in' answer a user would expect to include
-- them. That one does not depend on the CTE's contents.
--
-- Guards exist for both - NOT EXISTS, an IS NOT NULL inside the CTE, or a
-- NULL-safe comparison - and this capture uses none. tableau/01 flags a
-- related assumption of its own, a plain = join predicate that matches a
-- null-safe join only when the key has no NULLs. Both vendors leave the NULL
-- question to the data.
--
-- Search: [Amount] [Product Category] [Product Name] not in ( [Product Name] top 10 [Employee Count] [Order Date].'10 years ago' )
-- ----------------------------------------------------------------------------
WITH `qt_0` AS (
  SELECT
    `ta_1`.`product_name` AS `ca_1`,
    MEASURE(`ta_1`.`employee_count`) AS `ca_2`
  FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
  WHERE (
    `ta_1`.`order_date` >= to_date('2016-01-01', 'yyyy-MM-dd')
    AND `ta_1`.`order_date` < to_date('2017-01-01', 'yyyy-MM-dd')
  )
  GROUP BY `ca_1`
  ORDER BY `ca_2` DESC
  LIMIT 10
)
SELECT
  `ta_1`.`product_category` AS `ca_3`,
  MEASURE(`ta_1`.`amount`) AS `ca_4`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE NOT(`ta_1`.`product_name` IN (
  SELECT `ca_1`
  FROM `qt_0`
))
GROUP BY `ca_3`;
