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
-- Reference query for case B2 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno
  WHERE d.loc = 'NEW YORK'),
spine AS (SELECT DISTINCT job FROM joined)
SELECT g.job,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS emp_count
FROM spine AS g
ORDER BY g.job;
