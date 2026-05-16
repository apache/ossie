SELECT id, customer_id, amount,
       SUM(amount) OVER (PARTITION BY customer_id ORDER BY id ASC NULLS LAST
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM orders
WHERE customer_id IS NOT NULL
ORDER BY customer_id ASC NULLS LAST, id ASC NULLS LAST
