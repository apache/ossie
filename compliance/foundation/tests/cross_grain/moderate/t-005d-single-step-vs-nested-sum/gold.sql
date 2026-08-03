SELECT c.region AS region, SUM(o.amount) AS sum_single
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
