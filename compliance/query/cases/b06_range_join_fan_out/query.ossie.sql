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
-- B6. Fan-out through a range join.
-- Question: For each department, how many employees are there, and how many
--   salary grades do their salaries fall into?
-- The join condition can be any predicate over fields, not only an equality.
-- Each employee matches one salary grade, and one grade can match many
-- employees. AGG(s.grade_count) counts each grade once in each department, so
-- it gives the number of distinct grades in the department. A plain COUNT(*)
-- over the joined rows gives 3, 5, 6.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.deptno,
       AGG(e.emp_count) AS emp_count,
       AGG(s.grade_count) AS grade_count
FROM scott_model.emp AS e
JOIN scott_model.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal
GROUP BY e.deptno
ORDER BY e.deptno;
