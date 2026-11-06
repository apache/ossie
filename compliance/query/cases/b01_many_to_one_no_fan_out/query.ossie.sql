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
-- B1. A many-to-one join repeats no row of the measure.
-- Question: What are the payroll and the headcount of each department, by
--   department name?
-- Each employee matches one department, so the join repeats no emp row. The
-- values are the per-department totals of A2.
-- Spec: 7.6
-- ----------------------------------------------------------------------------
SELECT d.dname,
       AGG(e.sum_sal) AS sum_sal,
       AGG(e.emp_count) AS emp_count
FROM scott_model.emp AS e
JOIN scott_model.dept AS d ON e.deptno = d.deptno
GROUP BY d.dname
ORDER BY d.dname;
