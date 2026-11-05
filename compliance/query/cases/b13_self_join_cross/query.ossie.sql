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
-- B13. CROSS JOIN.
-- The cross join has no condition and gives 14 x 14 = 196 rows. Each side
-- still counts each employee once. Naive SUM(e.sal) is 406350.
--
-- B9-B13. One self-join query with each join type.
--
-- e is the employee and m is the manager, joined on e.mgr = m.empno. Both
-- sides have rows that do not match:
--   * KING has no manager, so KING has no match on the e side.
--   * 8 employees manage nobody, so they have no match on the m side.
-- The join type decides which source rows reach each measure. The join never
-- inflates a measure, and NULL-padded rows add nothing:
--
--   join type   joined rows   e_count  e_sal     m_count  m_sal
--   INNER       13            13       24025.00  6        19275.00
--   LEFT        14            14       29025.00  6        19275.00
--   RIGHT       21            13       24025.00  14       29025.00
--   FULL        22            14       29025.00  14       29025.00
--   CROSS       196           14       29025.00  14       29025.00
--
-- Naive SUM(m.sal) for the INNER join is 43650: KING's salary is counted 3
-- times and BLAKE's 5 times.
-- Spec: 7.6, 8.1, G2 (8.3), A.6
-- ----------------------------------------------------------------------------
SELECT AGG(e.emp_count) AS e_count,
       AGG(e.sum_sal) AS e_sal,
       AGG(m.emp_count) AS m_count,
       AGG(m.sum_sal) AS m_sal
FROM scott_model.emp AS e
CROSS JOIN scott_model.emp AS m;
