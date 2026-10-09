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
-- ThoughtSpot BI SQL examples: formulas and parameters
--
-- Three Answers that add calculations on top of the Metric View, and what each
-- one compiles to. Every formula here is defined on the Answer rather than in
-- the model, and every one lands in the SELECT list of a single query - there is
-- no CTE and no subselect anywhere in this file.
--
-- Two things are worth watching. First, division: one ratio is guarded two
-- different ways in a single SELECT list, and the two do not return the same
-- value when the divisor is zero. Second, IF/THEN/ELSE: the shape compiles to a
-- CASE when its condition depends on the data, and disappears entirely when the
-- condition can be decided before the SQL is generated - whatever makes it
-- decidable.
--
-- ThoughtSpot feature: the formula function reference.
-- https://docs.thoughtspot.com/cloud/latest/formula-reference
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: two spellings of one division, side by side.
--
-- Both formulas are defined on the Answer:
--
--   fxRatio    = [Amount] / [Quantity]
--   safeDivide = safe_divide([Amount], [Quantity])
--
-- Neither [Amount] nor [Quantity] is expanded into the SUM() the model
-- declares for it. They compile to MEASURE(ta_1.amount) and
-- MEASURE(ta_1.quantity) - the Metric View's own measure references - so
-- the model keeps ownership of how each measure aggregates. What the
-- Answer contributes is only the arithmetic between them.
--
-- fxRatio was written as a bare '/', with no guard of any kind. It
-- compiles to MEASURE(amount) / NULLIF(MEASURE(quantity), 0.0): the
-- NULLIF was inserted for it, so where quantity sums to zero the column
-- is NULL and the divisor is never literally zero.
--
-- safeDivide compiles to a CASE that tests the divisor for zero and
-- yields 0, carrying the same NULLIF division in its ELSE arm.
--
-- So the two columns give different answers to the same question. Where
-- quantity sums to zero, fxRatio is NULL and safeDivide is 0 - one says
-- 'no answer', the other says 'the answer is nothing'. Both are
-- defensible and they are not interchangeable, and a consumer reading
-- only the SQL cannot tell which the author meant to publish.
--
-- Two observations about this capture rather than documented behaviour.
-- The NULLIF inside safeDivide's ELSE arm cannot change the result: the
-- WHEN has already removed the only value NULLIF tests for, and for any
-- other value NULLIF returns its first argument unchanged. And
-- safe_divide guards zero but not NULL - if quantity is NULL then
-- MEASURE(quantity) = 0 is NULL, the WHEN does not match, the ELSE arm
-- runs and the column is NULL. The name promises more than the SQL does.
--
-- Naming note: 04-lod-two-stage-aggregation.sql also carries a formula called
-- fxRatio, defined differently there. The name is the Answer author's, not the
-- model's, and nothing binds the two.
--
-- Search: [Product Name] [formula_fxRatio] [formula_safeDivide] sort by [Product Name]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`product_name` AS `ca_1`,
  (MEASURE(`ta_1`.`amount`) / NULLIF(MEASURE(`ta_1`.`quantity`),0.0)) AS `ca_2`,
  CASE
    WHEN MEASURE(`ta_1`.`quantity`) = 0 THEN 0
    ELSE (MEASURE(`ta_1`.`amount`) / NULLIF(MEASURE(`ta_1`.`quantity`),0.0))
  END AS `ca_3`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC;

-- ----------------------------------------------------------------------------
-- Query 2: IF/THEN/ELSE, and what becomes of the formula it reuses.
--
-- The same two formulas as Query 1, plus a third built on one of them:
--
--   IF THEN ELSE = if ( [formula_fxRatio] > 10 ) then true else false
--
-- It compiles to CASE WHEN ... THEN TRUE ELSE FALSE END, a literal
-- transcription with the TRUE and FALSE arms carried through. That looks
-- like sugar around a comparison which is already boolean, and it is not
-- quite - see the NULL note below.
--
-- The reference to formula_fxRatio does not survive as a reference. The division
-- is substituted in place. The expression MEASURE(amount) /
-- NULLIF(MEASURE(quantity), 0.0) then appears three times in this one
-- SELECT list: inside the CASE condition, again as a column of its own,
-- and again in the ELSE arm of safeDivide. Only the first of those is
-- the substitution - Query 1, which has no IF/THEN/ELSE formula at all,
-- already carries the other two.
--
-- So a formula built on another formula is substitution, not a named
-- binding: there is no CTE, no lateral and no alias reuse to factor the
-- shared expression out, and whether the repetition costs anything is
-- left to the engine's common-subexpression elimination.
--
-- The NULL behaviour deserves stating, because the two-branch CASE
-- changes it. Where quantity sums to zero, fxRatio is NULL, NULL > 10 is
-- NULL, the WHEN does not match, and the ELSE arm returns FALSE. So
-- 'unknown' and 'not greater than 10' arrive as the same value. Written
-- as the bare comparison the column would have been NULL for those rows;
-- wrapping it in IF/THEN/ELSE removes a NULL that three-valued logic
-- would otherwise have kept. That is an easy thing to do by accident,
-- because the formula never mentions NULL at all.
--
-- Note also that the aliases are positional, ca_1 through ca_4. The
-- names fxRatio and safeDivide do not reach the SQL, so which column
-- carries which calculation is recorded only on the Answer.
--
-- Search: [Product Name] [formula_fxRatio] [formula_safeDivide] [formula_IF THEN ELSE] sort by [Product Name]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`product_name` AS `ca_1`,
  CASE
    WHEN (MEASURE(`ta_1`.`amount`) / NULLIF(MEASURE(`ta_1`.`quantity`),0.0)) > 10 THEN TRUE
    ELSE FALSE
  END AS `ca_2`,
  (MEASURE(`ta_1`.`amount`) / NULLIF(MEASURE(`ta_1`.`quantity`),0.0)) AS `ca_3`,
  CASE
    WHEN MEASURE(`ta_1`.`quantity`) = 0 THEN 0
    ELSE (MEASURE(`ta_1`.`amount`) / NULLIF(MEASURE(`ta_1`.`quantity`),0.0))
  END AS `ca_4`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC;

-- ----------------------------------------------------------------------------
-- Query 3: a parameter-driven measure switch.
--
-- The Answer carries a parameter and a formula over it:
--
--   Measure Picker  CHAR, default 'amount', choices 'amount' | 'quantity'
--   fxMeasure       if ( [Measure Picker] = 'amount' )
--                     then [Amount] else [Quantity]
--
-- The generated SQL holds no CASE, no comparison against any parameter value,
-- and no mention of amount. Only MEASURE(quantity) survives - the ELSE arm.
--
-- Disclosure, because it changes how this capture should be read: the saved
-- formula originally compared against 'attribute', a value the parameter cannot
-- hold, which would have explained the ELSE arm winning all by itself. That was
-- a typo. It was corrected to 'amount' - matching the parameter's own default -
-- before the SQL above was captured, and the ELSE arm still wins.
--
-- Two further conditions were then tried by editing the formula and re-reading
-- the generated SQL. A constant that is plainly true, 'amount' = 'amount',
-- emits MEASURE(amount): a decidable condition IS folded away at generation and
-- only the surviving arm reaches the SQL. But comparing the parameter against
-- 'quantity', and against the empty string, both emit the ELSE arm as well. On
-- this build the parameter reference does not resolve to anything a comparison
-- matches, so the switch never selects.
--
-- Recorded as observed behaviour on one build in September 2026. No cause is
-- claimed, and note that the folding itself is driven by the condition being
-- decidable, not by it mentioning a parameter.
--
-- The part that generalises is what the SQL does not contain. Folding discards
-- the losing arm, so the statement carries no trace of the parameter, of the
-- condition, or of the measure not chosen. A consumer holding this SQL cannot
-- tell a switch that worked from one whose condition never matched - both
-- reduce to a single measure and an unremarkable GROUP BY. Any interface that
-- resolves choices before emitting SQL inherits that: the simpler output is
-- also the output with the evidence removed.
--
-- Search: [Product Category] [formula_fxMeasure]
-- ----------------------------------------------------------------------------
SELECT
  `ta_1`.`product_category` AS `ca_1`,
  MEASURE(`ta_1`.`quantity`) AS `ca_2`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`;
