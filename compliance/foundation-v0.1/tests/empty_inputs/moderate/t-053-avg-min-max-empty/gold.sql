SELECT c.region AS region,
       AVG(o.amount) AS avg_amount,
       MIN(o.amount) AS min_amount,
       MAX(o.amount) AS max_amount
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
