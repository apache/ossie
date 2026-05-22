-- T-047 empty/non-empty group D-033 behaviour:
--   SUM(amount)              ⇒ NULL on an empty group (here: no empties).
--   COUNT(orders.id)          ⇒ 0 on an empty group.
--   COUNT(DISTINCT orders.id) ⇒ 0 on an empty group.
SELECT c.region                   AS region,
       SUM(o.amount)              AS revenue,
       COUNT(o.id)                AS order_count,
       COUNT(DISTINCT o.id)       AS row_count
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
ORDER BY region NULLS LAST
