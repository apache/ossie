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
-- B15. A LEFT JOIN keeps an empty department, and the ratio changes.
-- Question: What is the payroll per department, over all departments?
-- B14 to B16 run one dept-emp query with different joins. The query divides
-- the payroll by the number of departments: a ratio of two measures from
-- different relations. The join repeats each dept row once for each employee
-- in the department, but dept_count still counts each department once. The
-- CAST makes the division approximate (README, "Result semantics").
--
-- OPERATIONS has no employees. The LEFT join keeps it, so the payroll is
-- divided by 4 departments and not by 3 (B14). The join type is the choice of
-- the tool, and it changes the answer (Sections 10.2 and A.6). A plain
-- COUNT(d.deptno) over the joined rows gives 15, not 4. A RIGHT join with the
-- sides swapped, or a FULL join, gives the same result, because every
-- employee has a department.
-- Spec: 7.2, 7.6, 8.1, 10.2, A.6
-- ----------------------------------------------------------------------------
SELECT AGG(d.dept_count) AS dept_count,
       AGG(e.emp_count) AS emp_count,
       AGG(e.sum_sal) AS sum_sal,
       CAST(AGG(e.sum_sal) AS DOUBLE PRECISION) / AGG(d.dept_count) AS sal_per_dept
FROM scott_model.dept AS d
LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno;
