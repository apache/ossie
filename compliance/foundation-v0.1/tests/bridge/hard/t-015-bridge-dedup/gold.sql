WITH dedup AS (
    SELECT DISTINCT a.height AS height, m.movie_id AS movie_id, m.gross AS gross
    FROM appearances ap
    JOIN actors a ON ap.actor_id = a.actor_id
    JOIN movies m ON ap.movie_id = m.movie_id
)
SELECT height, SUM(gross) AS total_gross
FROM dedup
GROUP BY height
