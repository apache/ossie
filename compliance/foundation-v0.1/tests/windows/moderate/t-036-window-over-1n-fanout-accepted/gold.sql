SELECT id, customer_id,
       RANK() OVER (PARTITION BY customer_id ORDER BY amount DESC NULLS FIRST) AS order_rank
FROM orders
