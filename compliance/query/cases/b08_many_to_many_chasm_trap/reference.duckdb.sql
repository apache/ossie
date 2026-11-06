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
-- Reference query for case B8 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- For the joins, it applies the reference evaluation strategy of Section 9,
-- as ../../README.md describes.
WITH joined AS (
  SELECT e.deptno,
         e.empno AS e_k, e.sal AS e_sal,
         c.empno AS c_k
  FROM scott.emp AS e
  JOIN scott.emp AS c ON e.deptno = c.deptno),
spine AS (SELECT DISTINCT deptno FROM joined)
SELECT g.deptno,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.c_k
              FROM joined AS j
              WHERE j.c_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.c_k) AS x) AS colleagues
FROM spine AS g
ORDER BY g.deptno;
