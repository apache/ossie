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
-- Reference query for case A5 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
SELECT deptno,
       SUM(sal) / COUNT(*) AS sal_per_emp,
       AVG(sal) AS avg_sal,
       SUM(sal) + COALESCE(SUM(comm), 0) AS total_pay
FROM scott.emp
GROUP BY deptno
ORDER BY deptno;
