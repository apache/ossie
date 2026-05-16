-- T-043 multi-hop chain: order_lines -> orders -> customers -> segments
-- revenue = SUM(qty * price), grouped by segments.name.
-- Expected (from f_chain seed):
--   retail    -> 50 + 50 + 100 = 200
--   wholesale -> 300
--   partner   -> 50
SELECT s.name AS name,
       SUM(ol.qty * ol.price) AS revenue
FROM order_lines ol
LEFT JOIN orders    o ON ol.order_id    = o.id
LEFT JOIN customers c ON o.customer_id  = c.id
LEFT JOIN segments  s ON c.segment_id   = s.id
GROUP BY s.name
ORDER BY name NULLS LAST
