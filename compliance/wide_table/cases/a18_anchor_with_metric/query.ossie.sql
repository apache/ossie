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
-- a18. A metric makes an otherwise unanchored query valid
-- The Section 5 lowering of query.json. Case notes: ../README.md.
-- Spec: 5.3, 5.6, B.3
-- ----------------------------------------------------------------------------
SELECT i.brand, st.state, AGG(s.total_sales) AS total_sales
FROM stores_model.store_sales s
LEFT JOIN stores_model.item i ON s.item_id = i.item_id
LEFT JOIN stores_model.store st ON s.store_id = st.store_id
GROUP BY i.brand, st.state;
