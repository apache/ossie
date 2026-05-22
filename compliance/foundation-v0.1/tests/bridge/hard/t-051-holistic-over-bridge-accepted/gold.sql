-- Bridge-dedup MEDIAN(movies.gross) grouped by actors.height (D-027).
-- Same dedup CTE as t-015; just MEDIAN over the deduped set. Holistic
-- aggregates over an N:N bridge are accepted bare per the post-revision
-- D-027 contract — the bridge plan is a single-pass aggregate, not a
-- multi-stage decomposition, so MEDIAN is well-defined here.
-- Expected rows from f_bridge:
--   (170, 150.00)  -- MEDIAN(100, 200) over (M10, 170), (M11, 170)
--   (180,  50.00)  -- MEDIAN(50)       over (M12, 180)
WITH dedup AS (
    SELECT DISTINCT a.height AS height, m.movie_id AS movie_id, m.gross AS gross
    FROM appearances ap
    JOIN actors a ON ap.actor_id = a.actor_id
    JOIN movies m ON ap.movie_id = m.movie_id
)
SELECT height, MEDIAN(gross) AS median_gross
FROM dedup
GROUP BY height
