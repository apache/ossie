SELECT c.region AS region, COUNT(*) AS order_count
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
WHERE o.status = 'completed'
GROUP BY c.region
