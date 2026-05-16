-- F-BRIDGE-NONE — variant of F-BRIDGE without the `appearances` bridge.
-- Source: ../../../../proposals/foundation-v0.1/DATA_TESTS.md §3.3
--
-- Used by negative cases that pin E3012_MN_NO_SAFE_REWRITE: the model
-- declares no relationship between `actors` and `movies`, so any
-- query referencing both at once MUST fail closed (not silently
-- fabricate a join).

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
