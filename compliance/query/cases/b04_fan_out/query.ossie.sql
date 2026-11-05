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
-- B4. Fan-out: a measure of the "one" side, grouped by the "many" side.
-- The join repeats each dept row once for each employee in it. AGG(dept_count)
-- counts each department once in each group, so the result is the number of
-- departments that have the job. Naive COUNT(*) gives 2, 4, 3, 1, 4, which
-- AGG(e.emp_count) also gives, because the join repeats no emp row.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.job,
       AGG(d.dept_count) AS dept_count,
       AGG(e.emp_count) AS emp_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
GROUP BY e.job
ORDER BY e.job;
