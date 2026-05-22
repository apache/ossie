-- T-046 reflexive relationship — Foundation surface only counts the
-- employee-side; full role-qualified traversal is deferred (D-018).
-- direct_report_count by employees.region (the simple "rows per region" count).
-- Expected (from f_reflexive seed):
--   EAST -> 4 (Alice, Bob, Dave, Eve)
--   WEST -> 2 (Carol, Frank)
SELECT region,
       COUNT(id) AS direct_report_count
FROM employees
GROUP BY region
ORDER BY region NULLS LAST
