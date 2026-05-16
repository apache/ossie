-- Reference SQL: stage the windowed field in its own CTE so the
-- downstream CASE expression reads from a committed alias. The
-- implementation under test must produce the same SUM regardless of
-- how (or whether) it stages the intermediate CTE.
--
-- Expected: per customer, the highest-amount order's amount is
-- counted; smaller orders contribute 0. Customer 1 has orders
-- (101, 100) and (102, 50) → 100 contributes. Customer 2 has order
-- (103, 200) → 200 contributes. Customer 3 has order (104, 75) →
-- 75 contributes. Orphan customer 99 has order (105, 30) → 30
-- contributes. Total = 405.
WITH ranked AS (
    SELECT
        id,
        amount,
        ROW_NUMBER() OVER (
            PARTITION BY customer_id
            ORDER BY amount DESC, id ASC
        ) AS rank_in_customer
    FROM orders
),
scored AS (
    SELECT
        id,
        CASE WHEN rank_in_customer = 1 THEN amount ELSE 0 END AS top_only
    FROM ranked
)
SELECT SUM(top_only) AS top_per_customer_total FROM scored
