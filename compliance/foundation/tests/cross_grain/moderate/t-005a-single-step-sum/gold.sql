SELECT c.region AS region, SUM(o.amount) AS total_order_amount
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
