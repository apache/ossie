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
-- A1. Grand total.
-- AGG() is an aggregate. Without GROUP BY, the query returns one row.
-- Spec: 6, 7.2
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(sum_sal) AS sum_sal,
--          AGG(emp_count) AS emp_count,
--          AGG(avg_sal) AS avg_sal
--   FROM scott_model.emp;
SELECT SUM(sal) AS sum_sal,
       COUNT(*) AS emp_count,
       AVG(sal) AS avg_sal
FROM scott.emp;
-- +----------+-----------+---------+
-- | SUM_SAL  | EMP_COUNT | AVG_SAL |
-- +----------+-----------+---------+
-- | 29025.00 |        14 | 2073.21 |
-- +----------+-----------+---------+
-- (1 row)
