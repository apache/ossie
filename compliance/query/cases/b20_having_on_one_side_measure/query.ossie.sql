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
-- B20. HAVING on a "one"-side measure that the join fans out.
-- Question: Which jobs exist in more than one department, and in how many?
-- AGG(d.dept_count) may appear in HAVING, as any aggregate may (Section 7.5).
-- The join repeats each dept row once for each employee, and the measure
-- counts each department once, so HAVING keeps the jobs that span more than
-- one department: CLERK and MANAGER. A plain COUNT(*) > 1 in HAVING would use
-- the joined-row count and also keep ANALYST (2) and SALESMAN (4), whose
-- employees are all in one department.
-- Spec: 7.5, 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.job, AGG(d.dept_count) AS dept_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
GROUP BY e.job
HAVING AGG(d.dept_count) > 1
ORDER BY e.job;
