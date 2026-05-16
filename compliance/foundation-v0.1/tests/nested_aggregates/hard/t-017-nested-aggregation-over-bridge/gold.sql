WITH per_actor AS (
    SELECT a.actor_id, a.height, AVG(m.gross) AS aa
    FROM actors a
    JOIN appearances ap ON ap.actor_id = a.actor_id
    JOIN movies m ON ap.movie_id = m.movie_id
    GROUP BY a.actor_id, a.height
)
SELECT height, AVG(aa) AS avg_of_per_actor_avg
FROM per_actor
GROUP BY height
