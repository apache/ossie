SELECT c.region AS region, SUM(o.amount) AS revenue
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
WHERE o.status = 'completed' AND o.amount > 60
GROUP BY c.region
