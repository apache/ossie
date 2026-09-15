SELECT c.region AS region, COUNT(DISTINCT o.status) AS distinct_order_statuses
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
