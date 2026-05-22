-- Uses F-PRELUDE; the NULL-FK case (order 106) would be added by the
-- planner's LEFT-join shape if a NULL-FK row existed. This gold runs
-- the same shape as T-001 against F-PRELUDE.
SELECT c.region AS region, SUM(o.amount) AS revenue
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
