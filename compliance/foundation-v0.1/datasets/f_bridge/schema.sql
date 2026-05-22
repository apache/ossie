-- F-BRIDGE — actor↔movie M:N through the appearances bridge.
-- Source: ../../DATA_TESTS.md §3.2
--
-- This is the fixture for the flagship T-015 bridge-deduplication test
-- (D-026). Two actors at height 170 both appeared in M10 (Action),
-- which is the situation D-026 demands the engine de-duplicate.

CREATE TABLE actors (
    actor_id INTEGER PRIMARY KEY,
    name     VARCHAR,
    height   INTEGER
);

INSERT INTO actors VALUES
    (1, 'Alice', 170),
    (2, 'Bob',   170),
    (3, 'Carol', 180);

CREATE TABLE movies (
    movie_id INTEGER PRIMARY KEY,
    title    VARCHAR,
    gross    DECIMAL(10, 2)
);

INSERT INTO movies VALUES
    (10, 'Action', 100.00),
    (11, 'Drama',  200.00),
    (12, 'Comedy',  50.00);

CREATE TABLE appearances (
    actor_id INTEGER,
    movie_id INTEGER,
    PRIMARY KEY (actor_id, movie_id)
);

-- M10 (Action) has two actors at height 170 (Alice + Bob). The
-- naive flat-join SQL of actors, appearances, and movies grouped
-- by actors.height would double-count its gross (200) for the
-- height 170 group. D-026 requires the engine to materialize
-- distinct (movie_id, height) and produce 100 (the M10 gross only
-- once) plus 200 (M11 Drama) equals 300 instead.
INSERT INTO appearances VALUES
    (1, 10),
    (1, 11),
    (2, 10),
    (3, 12);
