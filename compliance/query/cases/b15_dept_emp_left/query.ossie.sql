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
-- B15. LEFT JOIN.
--
-- B14-B17. One dept-emp query with inner and outer joins.
--
-- The query computes the payroll per department as a ratio of two measures
-- from different relations: AGG(e.sum_sal) / AGG(d.dept_count). The join
-- repeats each dept row once for each employee in it (fan-out), but
-- dept_count still counts each department once. Naive COUNT(d.deptno) is 14
-- for INNER and 15 for LEFT.
--
-- The join type is the tool's choice, and it changes the answer (spec 10.2,
-- A.6). The LEFT join keeps OPERATIONS, which has no employees, so the
-- payroll is divided by 4 departments and not by 3. A filter in the ON clause
-- of the LEFT join keeps all 4 departments but matches only the clerks.
-- A RIGHT join with the sides swapped, or a FULL join, gives the LEFT
-- result, because every employee has a department.
-- Spec: 7.2, 7.6, 8.1, A.6
-- ----------------------------------------------------------------------------
SELECT AGG(d.dept_count) AS dept_count,
       AGG(e.emp_count) AS emp_count,
       AGG(e.sum_sal) AS sum_sal,
       CAST(AGG(e.sum_sal) AS DOUBLE PRECISION) / AGG(d.dept_count) AS sal_per_dept
FROM scott_model.dept AS d
LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno;
