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
-- a16. Two fact metrics share item and date dimensions
-- The Section 5 lowering of query.json. Case notes: ../README.md.
-- Spec: 4, 5.4, 5.5, B.3
-- ----------------------------------------------------------------------------
WITH b_sales AS (
  SELECT i.brand, d.year, AGG(s.total_sales) AS total_sales
  FROM stores_model.store_sales s
  LEFT JOIN stores_model.item i ON s.item_id = i.item_id
  LEFT JOIN stores_model.date_dim d ON s.sale_date_id = d.date_id
  GROUP BY i.brand, d.year
), b_returns AS (
  SELECT i.brand, d.year, AGG(r.total_returns) AS total_returns
  FROM stores_model.store_returns r
  LEFT JOIN stores_model.item i ON r.item_id = i.item_id
  LEFT JOIN stores_model.date_dim d ON r.return_date_id = d.date_id
  GROUP BY i.brand, d.year
)
SELECT COALESCE(b_sales.brand, b_returns.brand) AS brand,
       COALESCE(b_sales.year, b_returns.year) AS year,
       b_sales.total_sales,
       b_returns.total_returns
FROM b_sales
FULL JOIN b_returns
  ON b_sales.brand IS NOT DISTINCT FROM b_returns.brand
 AND b_sales.year IS NOT DISTINCT FROM b_returns.year;
