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
-- B16. A filter in the ON clause of a LEFT JOIN keeps every left row.
-- Question: What is the payroll of the clerks per department, over all
--   departments?
-- B14 to B16 run one dept-emp query with different joins. The query divides
-- the payroll by the number of departments: a ratio of two measures from
-- different relations. The join repeats each dept row once for each employee
-- in the department, but dept_count still counts each department once. The
-- CAST makes the division approximate (README, "Result semantics").
--
-- The condition e.job = 'CLERK' in ON keeps every department and matches only
-- clerks. If the condition were in WHERE, the query would remove the
-- NULL-padded OPERATIONS row, and dept_count would be 3.
-- Spec: 7.2, 7.6, 8.1, A.6
-- ----------------------------------------------------------------------------
SELECT AGG(d.dept_count) AS dept_count,
       AGG(e.emp_count) AS emp_count,
       AGG(e.sum_sal) AS sum_sal,
       CAST(AGG(e.sum_sal) AS DOUBLE PRECISION) / AGG(d.dept_count) AS sal_per_dept
FROM scott_model.dept AS d
LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno AND e.job = 'CLERK';
