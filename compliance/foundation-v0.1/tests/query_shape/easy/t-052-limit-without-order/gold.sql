-- T-052 — Limit with explicit Order By for deterministic comparison.
-- D-014 requires compiled SQL to be byte-stable; we add ORDER BY here
-- so the row-by-row comparator the harness runs is also stable
-- (LIMIT-without-ORDER-BY result order is inherently engine-defined).
SELECT c.region AS region, SUM(o.amount) AS revenue
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
ORDER BY region NULLS LAST
LIMIT 2
