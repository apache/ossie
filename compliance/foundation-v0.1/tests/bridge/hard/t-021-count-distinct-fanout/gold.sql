-- T-021: COUNT(DISTINCT actors.actor_id) per movie title via bridge dedup.
-- Per the seed (f_bridge):
--   Action (M10): appearances (1, 10) and (2, 10) -> {actor 1, actor 2} -> 2
--   Drama  (M11): appearance  (1, 11)               -> {actor 1}        -> 1
--   Comedy (M12): appearance  (3, 12)               -> {actor 3}        -> 1
SELECT m.title AS title,
       COUNT(DISTINCT ap.actor_id) AS unique_actors
FROM movies m
LEFT JOIN appearances ap ON ap.movie_id = m.movie_id
GROUP BY m.title
ORDER BY title NULLS LAST
