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
-- A12. An ordinary aggregate over AGG() values (two-stage aggregation).
-- Question: What is the average payroll of a department, and what is the
--   average salary of an employee?
-- The first branch averages the payroll of the three departments (29025 / 3).
-- The second branch is the average salary of an employee. After AGG()
-- evaluates the measures, the values are ordinary scalars, so the outer AVG
-- and UNION ALL are valid. The outer AVG casts its argument, so its result is
-- approximate (README, "Result semantics").
-- Spec: A.3, 7.8
-- ----------------------------------------------------------------------------
SELECT 'avg payroll per dept' AS metric,
       AVG(CAST(dept_sal AS DOUBLE PRECISION)) AS amount
FROM (SELECT deptno, AGG(sum_sal) AS dept_sal
      FROM scott_model.emp
      GROUP BY deptno) AS d
UNION ALL
SELECT 'avg salary per emp', AGG(avg_sal)
FROM scott_model.emp
ORDER BY metric;
