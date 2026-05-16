-- Reference SQL: hand-written, portable across dialects. Computes the
-- chain inline in CTEs (one per derived field) so no lateral aliasing
-- is required. The implementation under test is free to stage
-- differently — only the row set matters.
WITH lt AS (
    SELECT id, qty * price AS line_total
    FROM order_lines
),
dlt AS (
    SELECT id, line_total * 0.9 AS discounted_line_total
    FROM lt
),
fp AS (
    SELECT id, discounted_line_total + 1 AS final_price
    FROM dlt
)
SELECT SUM(final_price) AS total_final FROM fp
