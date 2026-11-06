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
-- A3. WHERE removes rows before a measure is evaluated.
-- Question: Without the clerks, what are the payroll and the headcount of
--   each department?
-- Each measure sees only the rows that remain after WHERE.
-- Spec: 7.3, G1 (8.2)
-- ----------------------------------------------------------------------------
SELECT deptno,
       AGG(sum_sal) AS sum_sal,
       AGG(emp_count) AS emp_count
FROM scott_model.emp
WHERE job <> 'CLERK'
GROUP BY deptno
ORDER BY deptno;
