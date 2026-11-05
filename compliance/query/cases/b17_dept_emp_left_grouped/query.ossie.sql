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
-- B17. LEFT JOIN, grouped: a group with an empty input set.
-- The B15 query, grouped by department. The OPERATIONS group stays in the
-- result. Its joined row is NULL-padded on the emp side, so the emp measures
-- take their empty-input values: 0 for COUNT, NULL for SUM and AVG. The
-- INNER join of B1 omits OPERATIONS.
-- Spec: 7.6, 8.1, A.6
-- ----------------------------------------------------------------------------
SELECT d.dname,
       AGG(e.emp_count) AS emp_count,
       AGG(e.sum_sal) AS sum_sal,
       AGG(e.avg_sal) AS avg_sal
FROM scott_model.dept AS d
LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno
GROUP BY d.dname
ORDER BY d.dname;
