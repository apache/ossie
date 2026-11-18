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
-- B18. WHERE restricts a "one"-side measure that the join fans out.
-- Question: Among the employees who earn more than 1000, how many
--   departments have each job, and how many such employees are there?
-- The join repeats each dept row once for each employee in the department,
-- and WHERE removes rows before the measures are evaluated. AGG(d.dept_count)
-- counts each surviving department once in each group. Removing the two
-- lowest-paid clerks (SMITH and JAMES) drops CLERK from three departments to
-- two. A plain COUNT(*) over the joined rows gives 2, 2, 3, 1, 4.
-- AGG(e.emp_count) gives the same values, because the join repeats no emp row.
-- Spec: 7.3, 7.6, G1 (8.2), G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.job,
       AGG(d.dept_count) AS dept_count,
       AGG(e.emp_count) AS emp_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
WHERE e.sal > 1000
GROUP BY e.job
ORDER BY e.job;
