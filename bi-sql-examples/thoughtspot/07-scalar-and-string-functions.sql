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
-- ThoughtSpot BI SQL examples: scalar and string functions
--
-- The other files capture saved Answers, whose input is a search string. These
-- four have a different input: an AgentQL (Semantic SQL) statement written
-- directly against the Model and compiled by the same generator. The line above
-- each query is that statement.
--
-- They are here for what the compiler does to a scalar function on its way to
-- the warehouse. Twelve cases were compiled and all twelve succeeded, so these
-- are not support gaps; the four below were chosen because each is rewritten
-- differently. Read together they are a rough taxonomy: pass it through, rename
-- the type, substitute a different function, or expand it inline.
--
-- Two of the rewrites are worth more than the taxonomy. In both cases Databricks
-- already has the function the input asked for, and the compiler substitutes
-- anyway - so these are not gaps being papered over, they are choices.
--
-- One rewrite is common to all four and is not repeated below: the input's
-- SELECT DISTINCT never survives. Every statement projects its expression and
-- carries a GROUP BY on the output alias.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: passed through unchanged.
--
-- POSITION('Paper' IN col) reaches the warehouse as position('Paper' in col) -
-- the same function, the same infix IN spelling, only the case of the keyword
-- altered. Nothing is translated because Databricks already spells it this way.
--
-- This is the baseline the other three depart from.
--
-- AgentQL: SELECT DISTINCT POSITION('Paper' IN m."Product Category") AS pos_paper FROM "Semantic SQL - Sales " m ORDER BY pos_paper LIMIT 10
-- ----------------------------------------------------------------------------
SELECT position('Paper' in `ta_1`.`product_category`) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Query 2: the type name rewritten.
--
-- CAST(col AS VARCHAR) becomes CAST(col AS String). The function survives and
-- the TYPE NAME is translated.
--
-- The reason is narrower than it looks. Databricks does accept VARCHAR - but
-- only with a length, and VARCHAR(10) resolves to exactly the same type String
-- does. It is the UNSIZED form the input used that has no Databricks spelling:
-- CAST(12345 AS VARCHAR) fails with DATATYPE_MISSING_SIZE (SQLSTATE 42K01).
--
-- Worth noting for anyone writing a portable semantics layer: the type
-- vocabulary needs translating alongside the functions, and it can differ in
-- whether a length is optional rather than in the name itself.
--
-- AgentQL: SELECT DISTINCT CAST(m."Customer Zipcode" AS VARCHAR) AS cast_val FROM "Semantic SQL - Sales " m ORDER BY cast_val LIMIT 10
-- ----------------------------------------------------------------------------
SELECT CAST(`ta_1`.`customer_zipcode` AS String) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Query 3: a different function substituted.
--
-- ZEROIFNULL(col) is emitted as IFNULL(col, 0) - a different function, with the
-- zero the original name only implied now written out as an argument.
--
-- The substitution is not filling a gap. Databricks has its own zeroifnull
-- built-in with the same single-argument signature and the same semantics
-- (DESCRIBE FUNCTION EXTENDED zeroifnull reports it, Since 4.0.0). The compiler
-- rewrites past a function the engine already has.
--
-- The rewrite is exact for this input, and the reason is worth being precise
-- about: it holds because the replacement's second argument is a literal zero
-- of a type the column can be compared against. Here customer_zipcode is
-- BIGINT, so IFNULL(col, 0) stays BIGINT. Over a string column the same
-- substitution would return a string, which is a different result type from
-- what the name implies.
--
-- AgentQL: SELECT DISTINCT ZEROIFNULL(m."Customer Zipcode") AS zeroifnull_val FROM "Semantic SQL - Sales " m ORDER BY zeroifnull_val LIMIT 10
-- ----------------------------------------------------------------------------
SELECT IFNULL(`ta_1`.`customer_zipcode`, 0) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Query 4: expanded inline.
--
-- SPLIT_PART(col, ' ', 1) is emitted as a substring of a substring_index, with
-- the start offset chosen by a CASE, naming the column twice where the input
-- named it once.
--
-- As with Query 3, this is not a gap. Databricks has split_part built in with
-- exactly the input's three-argument signature (Since 3.3.0), and the expansion
-- below returns identical results to a native call - verified over the whole
-- view with a null-safe comparison, no mismatches. The compiler expands anyway.
--
-- The CASE is the part worth reading. It is `case 1 when 1 then 1 else ...` - a
-- constant compared against itself - so the ELSE arm is unreachable for this
-- input, and that dead arm still carries a column reference and two char_length
-- calls. The template was written for any part index and instantiated at 1
-- without being folded.
--
-- That is the third time this folder shows a guard or branch built over a
-- constant: 01-filters-and-predicates.sql escapes a literal string through three
-- nested replace() calls, 05-period-over-period.sql divides by NULLIF(3,0), and
-- here a CASE tests 1 against 1. The generator emits the general form and leaves
-- the folding to the engine.
--
-- AgentQL: SELECT DISTINCT SPLIT_PART(m."Product Category", ' ', 1) AS category_first_word FROM "Semantic SQL - Sales " m ORDER BY category_first_word LIMIT 10
-- ----------------------------------------------------------------------------
SELECT substring(substring_index(`ta_1`.`product_category`, ' ', 1), case 1 when 1 then 1 else (char_length(substring_index(`ta_1`.`product_category`, ' ', (1 - 1))) + char_length(' ') + 1) end) AS `ca_1`
FROM `agent_skills`.`dunder_mifflin`.`dunder_mifflin_sales_mv` AS `ta_1`
GROUP BY `ca_1`
ORDER BY `ca_1` ASC
LIMIT 10;
