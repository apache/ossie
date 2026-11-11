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
-- B19. A join to a non-distinct filter relation does not inflate a measure.
-- Question: In the departments that have a salesman, what is the payroll of
--   each job?
-- The subquery lists the department of each salesman. Only department 30 has
-- a salesman, and the subquery returns it four times, once per salesman. The
-- join repeats each employee of department 30 four times, but AGG(e.sum_sal)
-- counts each employee once. A plain SUM(e.sal) over the joined rows gives
-- 3800, 11400, 22400. Here the duplication comes from the filter relation,
-- not from a finer-grained table (B6), and G2 still counts each source row
-- once.
-- Spec: 7.6, 8.4, G2 (8.3), A.2
-- ----------------------------------------------------------------------------
SELECT e.job, AGG(e.sum_sal) AS sum_sal
FROM scott_model.emp AS e
JOIN (SELECT deptno
      FROM scott_model.emp
      WHERE job = 'SALESMAN') AS t ON e.deptno = t.deptno
GROUP BY e.job
ORDER BY e.job;
