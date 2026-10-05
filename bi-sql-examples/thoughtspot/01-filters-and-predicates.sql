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
-- ThoughtSpot BI SQL examples: string predicates
--
-- How ThoughtSpot's search-bar predicates compile to SQL against the Metric View.
-- All four filter a dimension and none reads a measure.
--
-- Two things are worth watching. Every predicate lowercases the COLUMN rather
-- than the literal, and three of the four wrap their literal in a nested
-- replace() chain to neutralise LIKE metacharacters. The fourth does not.
--
-- Queries 1 to 3 filter customer_name and differ only in keyword, so they
-- isolate one variable. Query 4 changes both the keyword and the column, so read
-- it for the escaping difference rather than as a like-for-like comparison.
--
-- ThoughtSpot feature: the search keyword reference.
-- https://docs.thoughtspot.com/cloud/latest/keywords
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: begins with.
--
-- The literal 'va' is wrapped in three nested replace() calls before it reaches
-- LIKE: '!' becomes '!!', then '%' becomes '!%', then '_' becomes '!_', and the
-- predicate declares ESCAPE '!'. Escaping the escape character first is the
-- correct order. The effect is that LIKE metacharacters in the literal are
-- matched literally rather than acting as wildcards.
--
-- Note the escaping is applied to a constant, so an engine has to fold it away
-- or pay for three string operations per row. Databricks folds it: the optimised
-- plan for this statement reduces the whole chain to StartsWith(lower(col),'va').
--
-- Note also LOWER() is applied to the COLUMN, not to the literal - the literal is
-- already lowercase. The comparison is therefore case-insensitive only because
-- the literal arrives lowercased; these captures do not show what happens to a
-- mixed-case one. Wrapping the column also costs min/max file skipping on the
-- underlying DM_CUSTOMER.COMPANY_NAME, since it is the expression, not the
-- stored column, that is compared. The predicate is still pushed to the scan.
--
-- Search: [Customer Name] begins with 'va'
-- ----------------------------------------------------------------------------
SELECT `ta_1`.`customer_name` AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE LOWER(`ta_1`.`customer_name`) LIKE concat(
  replace(
    replace(
      replace(
        'va',
        '!',
        '!!'
      ),
      '%',
      '!%'
    ),
    '_',
    '!_'
  ),
  '%'
) ESCAPE '!'
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 2: not begins with.
--
-- Identical to Query 1 with the whole predicate wrapped in NOT(). The escaping
-- chain and the LOWER() on the column are unchanged.
--
-- The wrapping has a consequence worth stating: if customer_name is NULL, the
-- inner LIKE is NULL, and NOT(NULL) is NULL, so the row fails the WHERE clause
-- and is dropped. A user reading 'not begins with va' would generally expect a
-- NULL name to be kept, since it does not begin with 'va'. This is ordinary
-- three-valued logic rather than a defect, but it is the kind of thing a SQL
-- interface to reusable semantics has to decide deliberately.
--
-- Search: [Customer Name] not begins with 'va'
-- ----------------------------------------------------------------------------
SELECT `ta_1`.`customer_name` AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE NOT(LOWER(`ta_1`.`customer_name`) LIKE concat(
  replace(
    replace(
      replace(
        'va',
        '!',
        '!!'
      ),
      '%',
      '!%'
    ),
    '_',
    '!_'
  ),
  '%'
) ESCAPE '!')
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 3: contains.
--
-- The same escaping chain and the same LOWER() on the column as Query 1, with a
-- leading '%' added as well as the trailing one. Set the differing literals
-- aside - the two Answers were written with different search terms - and the
-- only structural difference between 'contains' and 'begins with' is where the
-- wildcard sits.
--
-- Search: [Customer Name] contains 'da'
-- ----------------------------------------------------------------------------
SELECT `ta_1`.`customer_name` AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE LOWER(`ta_1`.`customer_name`) LIKE concat(
  '%',
  replace(
    replace(
      replace(
        'da',
        '!',
        '!!'
      ),
      '%',
      '!%'
    ),
    '_',
    '!_'
  ),
  '%'
) ESCAPE '!'
GROUP BY `ca_1`;

-- ----------------------------------------------------------------------------
-- Query 4: similar to.
--
-- This one behaves differently from the other three, in two ways.
--
-- First, the literal is NOT escaped. Queries 1 to 3 pass their literal through
-- the replace() chain; this one passes it straight into LIKE. That is not driven
-- by the input: none of the four literals contains a metacharacter, yet three of
-- them are escaped anyway. The asymmetry is in the generator.
--
-- It is also the undocumented half. The keyword reference states that for
-- 'similar to' you must enclose the item in quotes and supply '%' for zero, one
-- or multiple characters, or '_' for one - so the wildcard behaviour is written
-- down. Nothing on that page describes the other three escaping theirs.
--
-- Second, this capture supplies no wildcard at all, which the page presents as
-- required. ThoughtSpot accepted it and emitted LOWER(employee_name) LIKE 'jim',
-- which Databricks optimises to an equality test. So the search matched nothing:
-- employee_name is CONCAT(LAST_NAME, ', ', FIRST_NAME), and every value reads
-- 'Last, First'. Reaching Jim Halpert would need 'similar to %jim%'; a prefix
-- pattern would not match either, because the surname sorts first.
--
-- The escaping asymmetry is an observation about these four captures.
--
-- Search: [Employee Name] similar to 'jim'
-- ----------------------------------------------------------------------------
SELECT `ta_1`.`employee_name` AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
WHERE LOWER(`ta_1`.`employee_name`) LIKE 'jim'
GROUP BY `ca_1`;
