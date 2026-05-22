SELECT id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC NULLS FIRST) AS rn_per_customer
FROM orders
WHERE status = 'completed'
