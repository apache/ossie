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
-- Reference query for case B7 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- For the joins, it applies the reference evaluation strategy of Section 9,
-- as ../../README.md describes.
WITH joined AS (
  SELECT s.grade,
         e.empno AS e_k, e.sal AS e_sal,
         d.deptno AS d_k
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno
  JOIN scott.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal),
spine AS (SELECT DISTINCT grade FROM joined)
SELECT g.grade,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.d_k
              FROM joined AS j
              WHERE j.d_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.d_k) AS x) AS dept_count
FROM spine AS g
ORDER BY g.grade;
