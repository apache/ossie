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
-- B12. FULL OUTER JOIN: each side counts each of its rows once.
-- Question: Over all employees on both sides of the manager relationship, how
--   many employees are on each side, and what do they earn?
-- B9 to B13 run one self-join query with each join type. e is the employee
-- and m is the manager, joined on e.mgr = m.empno. KING has no manager, and 8
-- employees manage nobody. The join type decides which rows reach each
-- measure. No join type makes a measure count a row twice, and NULL-padded
-- rows add nothing.
--
-- FULL gives 22 joined rows. Both sides keep all 14 employees, and each side
-- counts each employee once.
-- Spec: 7.6, 8.1, G2 (8.3), A.6
-- ----------------------------------------------------------------------------
SELECT AGG(e.emp_count) AS e_count,
       AGG(e.sum_sal) AS e_sal,
       AGG(m.emp_count) AS m_count,
       AGG(m.sum_sal) AS m_sal
FROM scott_model.emp AS e
FULL JOIN scott_model.emp AS m ON e.mgr = m.empno;
