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
-- A5. Scalar expressions over AGG() results.
-- Each AGG() gives a scalar value for each group. Scalar operators and
-- functions then apply to these values. AGG(sum_sal) / AGG(emp_count) is equal
-- to AGG(avg_sal). COALESCE replaces the NULL commission of departments 10
-- and 20.
-- Spec: 7.2, 8.1
-- ----------------------------------------------------------------------------
SELECT deptno,
       CAST(AGG(sum_sal) AS DOUBLE PRECISION) / AGG(emp_count) AS sal_per_emp,
       AGG(avg_sal) AS avg_sal,
       AGG(sum_sal) + COALESCE(AGG(sum_comm), 0) AS total_pay
FROM scott_model.emp
GROUP BY deptno
ORDER BY deptno;
