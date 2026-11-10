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
-- A7. A measure over no rows.
-- Question: What are the headcount, the payroll, and the average salary of
--   department 40?
-- No employee is in department 40. Each measure takes the empty-input value
-- of its aggregate: 0 for COUNT, NULL for SUM and AVG.
-- Spec: 8.1
-- ----------------------------------------------------------------------------
SELECT AGG(emp_count) AS emp_count,
       AGG(sum_sal) AS sum_sal,
       AGG(avg_sal) AS avg_sal
FROM scott_model.emp
WHERE deptno = 40;
