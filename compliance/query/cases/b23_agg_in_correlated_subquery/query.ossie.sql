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
-- B23. A measure in a correlated scalar subquery.
-- Question: In each department, how many employees earn more than the average
--   salary of their department?
--
-- The scalar subquery evaluates avg_sal over only the rows of the current
-- department. AGG() appears inside a correlated subquery and retains its
-- ordinary measure semantics.
--
-- This extends A11 from an uncorrelated scalar subquery to a correlated one.
--
-- Spec: 7.3, 8.1
-- ----------------------------------------------------------------------------
SELECT deptno,
       AGG(emp_count) AS emp_count
FROM scott_model.emp AS e
WHERE sal >
      (SELECT AGG(avg_sal)
       FROM scott_model.emp AS x
       WHERE x.deptno = e.deptno)
GROUP BY deptno
ORDER BY deptno;
