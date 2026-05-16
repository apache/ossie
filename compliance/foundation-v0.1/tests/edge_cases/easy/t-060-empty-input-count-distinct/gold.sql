-- T-060: empty result set (no orders > $1M) — must return zero rows.
SELECT c.region AS region, COUNT(DISTINCT o.status) AS n_distinct_status
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
WHERE o.amount > 1000000
GROUP BY c.region
ORDER BY region NULLS LAST
