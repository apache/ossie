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
-- A8. GROUP BY ALL does not group by measures.
-- Question: How many employees were hired in each year, and what is their
--   total salary?
-- A measure is not a grouping column. GROUP BY ALL groups by the other
-- expressions of the SELECT list: here, an expression on a field.
-- Spec: 7.4
-- ----------------------------------------------------------------------------
SELECT EXTRACT(YEAR FROM hiredate) AS hire_year,
       AGG(emp_count) AS emp_count,
       AGG(sum_sal) AS sum_sal
FROM scott_model.emp
GROUP BY ALL
ORDER BY hire_year;
