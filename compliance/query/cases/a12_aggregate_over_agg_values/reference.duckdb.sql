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
-- Reference query for case A12 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
SELECT 'avg payroll per dept' AS metric,
       AVG(CAST(dept_sal AS DOUBLE PRECISION)) AS amount
FROM (SELECT deptno, SUM(sal) AS dept_sal
      FROM scott.emp
      GROUP BY deptno) AS d
UNION ALL
SELECT 'avg salary per emp', AVG(CAST(sal AS DOUBLE PRECISION))
FROM scott.emp
ORDER BY metric;
