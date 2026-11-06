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
-- B21. UNION ALL passes measures through without changing their semantics.
-- Question: What are the payroll and the headcount of each department when the
--   employees are split into two branches and recombined?
--
-- The UNION ALL combines two subsets of emp. The measures sum_sal and
-- emp_count pass through the UNION ALL without being evaluated. The outer
-- AGG() calls evaluate the measures over the rows that reach each department
-- group. The result is identical to A2.
--
-- A UNION ALL is a relational operation and does not itself aggregate a
-- measure. An implementation must preserve measure lineage across the UNION
-- boundary.
--
-- Spec: 7.1, 7.7, 8.1
-- ----------------------------------------------------------------------------
WITH staff AS (
  SELECT deptno,
         sum_sal,
         emp_count
  FROM scott_model.emp
  WHERE deptno IN (10, 20)

  UNION ALL

  SELECT deptno,
         sum_sal,
         emp_count
  FROM scott_model.emp
  WHERE deptno = 30
)
SELECT deptno,
       AGG(sum_sal) AS sum_sal,
       AGG(emp_count) AS emp_count
FROM staff
GROUP BY deptno
ORDER BY deptno;
