SELECT c.region AS region, c.id AS id,
       (SELECT SUM(o.amount) FROM orders o WHERE o.customer_id = c.id) AS lifetime_value
FROM customers c
ORDER BY c.id
