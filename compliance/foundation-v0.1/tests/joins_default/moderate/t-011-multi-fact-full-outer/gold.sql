WITH orders_branch AS (
    SELECT c.region AS region, SUM(o.amount) AS revenue
    FROM orders o LEFT JOIN customers c ON o.customer_id = c.id
    GROUP BY c.region
),
returns_branch AS (
    SELECT c.region AS region, SUM(r.amount) AS return_total
    FROM returns r LEFT JOIN customers c ON r.customer_id = c.id
    GROUP BY c.region
)
SELECT COALESCE(o.region, r.region) AS region, o.revenue, r.return_total
FROM orders_branch o
FULL OUTER JOIN returns_branch r ON o.region IS NOT DISTINCT FROM r.region
