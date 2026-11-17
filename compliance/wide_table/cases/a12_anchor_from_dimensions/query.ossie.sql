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
-- a12. The anchor is the dimension dataset that reaches the others
-- The Section 5 lowering of query.json. Case notes: ../README.md.
-- Spec: 5.6, B.5
-- ----------------------------------------------------------------------------
SELECT o.order_id, c.region
FROM retail_model.orders o
LEFT JOIN retail_model.customers c ON o.customer_id = c.customer_id
WHERE o.amount > 100
GROUP BY o.order_id, c.region;
