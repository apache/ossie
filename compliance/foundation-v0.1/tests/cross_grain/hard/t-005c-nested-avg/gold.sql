WITH per_cust AS (
    SELECT c.id AS cid, c.region AS region, AVG(o.amount) AS pc_avg
    FROM customers c
    LEFT JOIN orders o ON o.customer_id = c.id
    GROUP BY c.id, c.region
    UNION ALL
    SELECT NULL AS cid, NULL AS region, AVG(o.amount) AS pc_avg
    FROM orders o
    WHERE o.customer_id NOT IN (SELECT id FROM customers)
)
SELECT region, AVG(pc_avg) AS avg_of_per_customer_avg
FROM per_cust
WHERE pc_avg IS NOT NULL
GROUP BY region
