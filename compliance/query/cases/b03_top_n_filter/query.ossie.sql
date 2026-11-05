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
-- B3. Top-N filter through a join.
-- The subquery selects the two departments with the largest payroll (20 and
-- 30). The join to it acts as a filter on emp, and the outer query reports
-- payroll by job for those departments. The departments that the subquery
-- ranks are the same values that the join matches (closure, spec 8.4).
-- Spec: 7.6, 8.4, A.2
-- ----------------------------------------------------------------------------
SELECT e.job, AGG(e.sum_sal) AS sum_sal
FROM scott_model.emp AS e
JOIN (SELECT deptno
      FROM scott_model.emp
      GROUP BY deptno
      ORDER BY AGG(sum_sal) DESC
      LIMIT 2) AS t ON e.deptno = t.deptno
GROUP BY e.job
ORDER BY e.job;
