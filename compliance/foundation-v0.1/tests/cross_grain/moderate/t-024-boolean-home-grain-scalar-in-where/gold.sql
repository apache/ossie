-- Count orders per customer-region, restricted to customers who have at
-- least one completed order. The query selects metric ``order_count =
-- COUNT(orders.id)`` aliased as ``customer_count`` so the row count is
-- in *orders*, not customers; the WHERE filter exists at the customer
-- grain via the home-grain rewrite (D-003 / D-015).
SELECT c.region AS region, COUNT(o.id) AS customer_count
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE EXISTS (SELECT 1 FROM orders oo WHERE oo.customer_id = c.id AND oo.status = 'completed')
GROUP BY c.region
ORDER BY c.region NULLS LAST
