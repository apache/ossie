-- T-061: f_prelude has orphan order 105 with customer_id=99 (no
-- matching customer). Foundation default (D-001) is LEFT JOIN
-- fact->dim, so the orphan row's amount is summed under region=NULL.
--
-- Expected:
--   region=NULL  -> 30   (orphan order 105)
--   region=EAST  -> 350  (101+102+103)
--   region=WEST  -> 75   (104)
SELECT c.region        AS region,
       SUM(o.amount)   AS revenue
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
ORDER BY region NULLS LAST
