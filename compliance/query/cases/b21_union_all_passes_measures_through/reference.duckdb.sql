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
-- Reference query for case B21 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- Each UNION ALL branch selects a disjoint set of deptno values, so no source
-- row is counted twice; SUM/COUNT over the recombined rows equals the measure
-- evaluation.
WITH staff AS (
  SELECT deptno,
         empno,
         sal
  FROM scott.emp
  WHERE deptno IN (10, 20)

  UNION ALL

  SELECT deptno,
         empno,
         sal
  FROM scott.emp
  WHERE deptno = 30
)
SELECT deptno,
       SUM(sal) AS sum_sal,
       COUNT(*) AS emp_count
FROM staff
GROUP BY deptno
ORDER BY deptno;
