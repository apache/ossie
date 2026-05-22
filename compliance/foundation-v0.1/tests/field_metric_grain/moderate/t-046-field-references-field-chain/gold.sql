-- Reference SQL: hand-written and portable across dialects. The
-- harness executes both this query and the adapter's emitted SQL
-- against the same fixture; only the resulting row multiset is
-- compared (per D-014, SQL byte-text is a per-engine concern). The
-- shape below — one CTE per derived field — is illustrative; the
-- adapter is free to stage differently.
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
