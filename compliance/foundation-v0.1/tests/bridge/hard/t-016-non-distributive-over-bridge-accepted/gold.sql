-- Bridge-dedup AVG(movies.gross) grouped by actors.height (D-027).
-- Same dedup CTE as t-015 (SUM); just AVG over the deduped set.
-- Expected rows from f_bridge:
--   (170, 150.00)  -- AVG(100, 200) over (M10, 170), (M11, 170)
--   (180,  50.00)  -- AVG(50)       over (M12, 180)
WITH dedup AS (
    SELECT DISTINCT a.height AS height, m.movie_id AS movie_id, m.gross AS gross
    FROM appearances ap
    JOIN actors a ON ap.actor_id = a.actor_id
    JOIN movies m ON ap.movie_id = m.movie_id
)
SELECT height, AVG(gross) AS avg_gross
FROM dedup
GROUP BY height
