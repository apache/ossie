-- Negative test: planner MUST reject the AVG(AVG(...)) shape with
-- E_NESTED_AGGREGATION_DEFERRED (D-020(c) / D-027(d)). No SQL is
-- emitted; gold.sql is kept as a stub so the harness can locate
-- this directory.
SELECT 1 WHERE FALSE;
