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
-- B5. Fan-out in a self-join: the manager's salary.
-- e is the employee and m is the manager. The join repeats each manager row
-- once for each report. AGG(m.sum_sal) counts each manager's salary once in
-- each group. The 4 salesmen all report to BLAKE, so the result is 2850; the
-- naive SUM(m.sal) is 11400. The 4 clerks report to 4 different managers, so
-- the two values are the same (11300). Naive SUM(m.sal) by job is 5950,
-- 11300, 15000, 11400.
-- Spec: 7.6, 8.1, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.job,
       AGG(e.emp_count) AS emp_count,
       AGG(m.emp_count) AS managers,
       AGG(m.sum_sal) AS managers_sal
FROM scott_model.emp AS e
JOIN scott_model.emp AS m ON e.mgr = m.empno
GROUP BY e.job
ORDER BY e.job;
