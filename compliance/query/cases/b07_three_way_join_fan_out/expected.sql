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
-- Expected result for case B7 (query.ossie.sql), in standard SQL.
SELECT *
FROM (VALUES (1, 3,  2850.00, 2),
             (2, 3,  3800.00, 2),
             (3, 2,  3100.00, 1),
             (4, 5, 14275.00, 3),
             (5, 1,  5000.00, 1))
  AS expected (grade, emp_count, sum_sal, dept_count);
