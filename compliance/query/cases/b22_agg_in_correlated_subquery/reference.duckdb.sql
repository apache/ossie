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
-- Reference query for case B22 (query.ossie.sql), over the base tables in
-- ../../scott/scott.sql. Its result is equal to the result of expected.sql.
-- The correlated subquery recomputes AVG(sal) per department, matching the
-- measure's own source rows (the current department), so no reference
-- de-duplication strategy is needed here.
SELECT deptno,
       COUNT(*) AS emp_count
FROM scott.emp AS e
WHERE sal >
      (SELECT AVG(CAST(x.sal AS DOUBLE))
       FROM scott.emp AS x
       WHERE x.deptno = e.deptno)
GROUP BY deptno
ORDER BY deptno;
