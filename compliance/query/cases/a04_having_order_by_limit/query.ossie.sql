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
-- A4. HAVING, ORDER BY, and LIMIT on measures.
-- AGG() can appear in HAVING and ORDER BY, as any aggregate can. HAVING can
-- use a measure that the SELECT list does not contain. HAVING removes ANALYST
-- (6000, 2 employees) and PRESIDENT (5000, 1 employee), so CLERK (4150) is in
-- the top three.
-- Spec: 7.5
-- ----------------------------------------------------------------------------
SELECT job, AGG(sum_sal) AS sum_sal
FROM scott_model.emp
GROUP BY job
HAVING AGG(emp_count) > 2
ORDER BY AGG(sum_sal) DESC
LIMIT 3;
