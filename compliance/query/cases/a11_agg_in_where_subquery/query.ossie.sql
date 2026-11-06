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
-- A11. AGG() in a scalar subquery in WHERE.
-- Question: In each department, how many employees earn more than the company
--   average?
-- AGG() cannot appear directly in WHERE, but a scalar subquery in WHERE can
-- use it. The company average is 2073.21.
-- Spec: 7.3
-- ----------------------------------------------------------------------------
SELECT deptno, AGG(emp_count) AS emp_count
FROM scott_model.emp
WHERE sal > (SELECT AGG(avg_sal) FROM scott_model.emp)
GROUP BY deptno
ORDER BY deptno;
