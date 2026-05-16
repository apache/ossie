-- T-049: two measures from incompatible roots (revenue from orders,
-- customer_count from customers) ⇒ Foundation default is the
-- FULL OUTER stitch on the shared dimension `region` (D-001 / S-7).
--
-- Expected (from f_prelude seed):
--   region=NULL  -> revenue= 30 (orphan order 105 → customer 99 missing)
--                   customer_count=NULL
--   region=EAST  -> revenue=350 (101+102+103)        customer_count=2
--   region=NORTH -> revenue=NULL (no orders)         customer_count=1
--   region=WEST  -> revenue= 75 (104)                customer_count=1
SELECT region,
       SUM(revenue)        AS revenue,
       SUM(customer_count) AS customer_count
FROM (
    SELECT c.region AS region,
           o.amount AS revenue,
           CAST(NULL AS BIGINT) AS customer_count
    FROM orders o
    LEFT JOIN customers c ON o.customer_id = c.id
    UNION ALL
    SELECT region,
           CAST(NULL AS DECIMAL(10, 2)) AS revenue,
           cc                            AS customer_count
    FROM (
        SELECT region, COUNT(id) AS cc
        FROM customers
        GROUP BY region
    ) by_region
) stitched
GROUP BY region
ORDER BY region NULLS LAST
