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
-- B2. A filter on the joined relation restricts the measure.
-- Question: For the employees in New York, what are the payroll and the
--   headcount of each job?
-- A filter on dept removes emp rows from the input set, as a filter on emp
-- does. Only the employees in NEW YORK remain.
-- Spec: 7.6, G1 (8.2)
-- ----------------------------------------------------------------------------
SELECT e.job,
       AGG(e.sum_sal) AS sum_sal,
       AGG(e.emp_count) AS emp_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
WHERE d.loc = 'NEW YORK'
GROUP BY e.job
ORDER BY e.job;
