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
-- A9. ROLLUP evaluates a measure again for each grouping set.
-- Question: What is the average salary of each department, and of the whole
--   company?
-- On the total row, AGG(avg_sal) is the average over all 14 employees
-- (2073.21). It is not the average of the three department averages
-- (2219.44).
-- Spec: 7.4, G1 (8.2)
-- ----------------------------------------------------------------------------
SELECT deptno,
       AGG(emp_count) AS emp_count,
       AGG(avg_sal) AS avg_sal
FROM scott_model.emp
GROUP BY ROLLUP (deptno)
ORDER BY deptno NULLS LAST;
