-- T-044 composite-key join: sales -> inventory on (store_id, sku).
-- units_sold by inventory.reorder_point.
-- Expected (from f_composite seed):
--   reorder_point=10 -> sku B in store 1: 2
--   reorder_point=15 -> sku A in store 2: 4
--   reorder_point=20 -> sku A in store 1: 5+3=8
--   reorder_point=25 -> sku C in store 2: 1
SELECT i.reorder_point AS reorder_point,
       SUM(s.qty)      AS units_sold
FROM sales s
LEFT JOIN inventory i
       ON s.store_id = i.store_id
      AND s.sku      = i.sku
GROUP BY i.reorder_point
ORDER BY reorder_point NULLS LAST
