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
-- B8. Many-to-many join (chasm trap).
-- Each employee is joined to every colleague in the same department, which
-- gives 3 x 3, 5 x 5, and 6 x 6 joined rows. Both sides repeat. Each measure
-- still counts each of its source rows once. Naive SUM(e.sal) gives 26250,
-- 54375, 56400, and naive COUNT(*) gives 9, 25, 36.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
SELECT e.deptno,
       AGG(e.sum_sal) AS sum_sal,
       AGG(c.emp_count) AS colleagues
FROM scott_model.emp AS e
JOIN scott_model.emp AS c ON e.deptno = c.deptno
GROUP BY e.deptno
ORDER BY e.deptno;
