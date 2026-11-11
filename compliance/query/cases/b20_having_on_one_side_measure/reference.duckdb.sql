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
-- Reference query for case B20 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- For the joins, it applies the reference evaluation strategy of Section 9,
-- as ../../README.md describes. HAVING is applied as a filter on the
-- evaluated measure.
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k,
         d.deptno AS d_k
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno),
spine AS (SELECT DISTINCT job FROM joined),
per_job AS (
  SELECT g.job AS job,
         (SELECT COUNT(*)
          FROM (SELECT j.d_k
                FROM joined AS j
                WHERE j.d_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
                GROUP BY j.d_k) AS x) AS dept_count
  FROM spine AS g)
SELECT job, dept_count
FROM per_job
WHERE dept_count > 1
ORDER BY job;
