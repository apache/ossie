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
-- B7. Fan-out in a three-way join.
-- Question: For each salary grade, how many employees are in it, what is
--   their total salary, and how many departments have one of them?
-- emp joins to dept and to salgrade, and the group is the salary grade.
-- AGG(d.dept_count) gives the number of departments that have an employee in
-- the grade. The join repeats each dept row once for each of its employees in
-- the grade. A plain COUNT(*) over the joined rows gives 3, 3, 2, 5, 1.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT s.grade,
       AGG(e.emp_count) AS emp_count,
       AGG(e.sum_sal) AS sum_sal,
       AGG(d.dept_count) AS dept_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
JOIN scott_model.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal
GROUP BY s.grade
ORDER BY s.grade;
