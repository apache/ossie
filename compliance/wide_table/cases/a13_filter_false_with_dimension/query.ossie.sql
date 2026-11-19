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
-- a13. A filter that removes every row leaves no groups
-- The Section 5 lowering of query.json. Case notes: ../README.md.
-- Spec: 3.4, 5.5, B.5
-- ----------------------------------------------------------------------------
SELECT c.region, AGG(o.total_revenue) AS total_revenue
FROM retail_model.orders o
LEFT JOIN retail_model.customers c ON o.customer_id = c.customer_id
WHERE FALSE
GROUP BY c.region;
