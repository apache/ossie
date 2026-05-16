-- Count orders per region, restricted to premium customers. The query
-- selects metric ``order_count = COUNT(orders.id)`` aliased as
-- ``customer_count`` so the row count is in *orders*, not customers.
-- The WHERE filter ``customers.is_premium`` resolves as a row-level
-- boolean scalar over customers' own columns (D-005(a)), so it is
-- accepted in ``Where``.
SELECT c.region AS region, COUNT(o.id) AS customer_count
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE c.segment = 'PREMIUM'
GROUP BY c.region
ORDER BY c.region NULLS LAST
