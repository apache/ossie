SELECT c.region AS region, AVG(o.amount) AS avg_order_amount
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
