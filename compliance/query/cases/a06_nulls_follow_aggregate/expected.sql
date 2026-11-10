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
-- Expected result for case A6 (query.ossie.sql), in standard SQL.
SELECT *
FROM (VALUES (10, 3, 0,    NULL,  NULL),
             (20, 5, 0,    NULL,  NULL),
             (30, 6, 4, 2200.00, 5.5E2))
  AS expected (deptno, emp_count, comm_count, sum_comm, avg_comm);
