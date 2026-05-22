SELECT c.region AS region, SUM(o.amount) AS revenue
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
ORDER BY c.region DESC NULLS FIRST
