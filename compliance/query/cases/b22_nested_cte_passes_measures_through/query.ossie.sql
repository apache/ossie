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
-- ----------------------------------------------------------------------------
-- B22. Measures retain their identity through nested CTEs.
-- Question: Counting only clerks and salesmen, what are the payroll and the
--   headcount of each department after several projection boundaries?
--
-- The measure passes through multiple CTEs before AGG() evaluates it. Renames
-- and projections do not change a measure. The result is identical to A10.
--
-- This case tests that an implementation preserves measure lineage through
-- repeated relational transformations.
--
-- Spec: 7.1, 7.7, 8.1
-- ----------------------------------------------------------------------------
WITH staff AS (
  SELECT deptno,
         sum_sal AS payroll,
         emp_count AS headcount
  FROM scott_model.emp
  WHERE job IN ('CLERK', 'SALESMAN')
),
staff2 AS (
  SELECT deptno,
         payroll,
         headcount
  FROM staff
),
staff3 AS (
  SELECT *
  FROM staff2
)
SELECT deptno,
       AGG(payroll) AS payroll,
       AGG(headcount) AS headcount
FROM staff3
GROUP BY deptno
ORDER BY deptno;
