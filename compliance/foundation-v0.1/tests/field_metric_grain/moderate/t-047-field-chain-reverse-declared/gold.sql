-- Same expected rows as T-046 — declaration order is not semantically
-- significant. The implementation must compute the same total
-- regardless of the field declaration order.
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
