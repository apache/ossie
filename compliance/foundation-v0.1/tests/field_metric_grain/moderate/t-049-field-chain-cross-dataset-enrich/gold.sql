-- Reference SQL: stage the customer-side derived fields in CTEs so
-- the enrich SELECT projects them by name. The implementation is
-- free to stage differently; only the rows matter.
--
-- Expected rows (group by tagged_region_segment):
--   ('rs=EAST:retail',     350.00)  — orders 101 (100) + 102 (50) for cust 1, order 103 (200) for cust 2
--   ('rs=WEST:wholesale',   75.00)  — order 104 for cust 3
--   (NULL,                  30.00)  — orphan order 105 (cust_id=99 has no matching customer)
-- Customer 4 (NORTH:retail) has no orders and so does not appear when
-- the join walks from the orders side.
WITH cust_rs AS (
    SELECT id, region || ':' || segment AS region_segment
    FROM customers
),
cust_tagged AS (
    SELECT id, 'rs=' || region_segment AS tagged_region_segment
    FROM cust_rs
)
SELECT
    c.tagged_region_segment AS tagged_region_segment,
    SUM(o.amount) AS total_amount
FROM orders o
LEFT JOIN cust_tagged c ON o.customer_id = c.id
GROUP BY c.tagged_region_segment
