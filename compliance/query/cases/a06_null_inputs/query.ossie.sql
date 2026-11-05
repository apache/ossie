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
-- A6. NULL inputs.
-- A measure follows the NULL rules of its aggregate. emp_count is COUNT(*)
-- and counts every row. comm_count is COUNT(comm) and ignores NULL. Departments
-- 10 and 20 have no commission values, so SUM and AVG give NULL. In department
-- 30, TURNER's 0.00 is a value, so AVG(comm) is 2200 / 4.
-- Spec: 8.1
-- ----------------------------------------------------------------------------
SELECT deptno,
       AGG(emp_count) AS emp_count,
       AGG(comm_count) AS comm_count,
       AGG(sum_comm) AS sum_comm,
       AGG(avg_comm) AS avg_comm
FROM scott_model.emp
GROUP BY deptno
ORDER BY deptno;
