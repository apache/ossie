SELECT id, customer_id, amount, order_rank_in_customer
FROM (
    SELECT id, customer_id, amount,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC NULLS FIRST, id ASC NULLS LAST)
               AS order_rank_in_customer
    FROM orders
) t
WHERE order_rank_in_customer = 1
