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
-- Reference query for case B17 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- For the joins, it applies the reference evaluation strategy of Section 9,
-- as ../../README.md describes.
WITH joined AS (
  SELECT d.dname,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.dept AS d
  LEFT JOIN scott.emp AS e ON e.deptno = d.deptno),
spine AS (SELECT DISTINCT dname FROM joined)
SELECT g.dname,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT AVG(CAST(x.sal AS DOUBLE))
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS avg_sal
FROM spine AS g
ORDER BY g.dname;
