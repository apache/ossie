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
-- a03. Three branches give each key once
-- The Section 5 lowering of query.json. Case notes: ../README.md.
-- Spec: 5.5, B.2
-- ----------------------------------------------------------------------------
WITH b_orders AS (
  SELECT c.region, AGG(o.total_revenue) AS total_revenue
  FROM retail_model.orders o
  LEFT JOIN retail_model.customers c ON o.customer_id = c.customer_id
  GROUP BY c.region
), b_returns AS (
  SELECT c.region, AGG(r.total_returns) AS total_returns
  FROM retail_model.returns r
  LEFT JOIN retail_model.customers c ON r.customer_id = c.customer_id
  GROUP BY c.region
), b_visits AS (
  SELECT c.region, 1 AS present, AGG(v.visit_count) AS visit_count
  FROM retail_model.visits v
  LEFT JOIN retail_model.customers c ON v.customer_id = c.customer_id
  GROUP BY c.region
), keys AS (
  SELECT region FROM b_orders
  UNION SELECT region FROM b_returns
  UNION SELECT region FROM b_visits
)
SELECT k.region,
       o.total_revenue,
       r.total_returns,
       CASE WHEN v.present IS NULL THEN 0
            ELSE v.visit_count END AS visit_count
FROM keys k
LEFT JOIN b_orders o ON o.region IS NOT DISTINCT FROM k.region
LEFT JOIN b_returns r ON r.region IS NOT DISTINCT FROM k.region
LEFT JOIN b_visits v ON v.region IS NOT DISTINCT FROM k.region;
