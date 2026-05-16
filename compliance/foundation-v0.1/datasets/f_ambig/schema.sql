-- F-AMBIG — two relationships between `orders` and `users`.
-- Source: ../../../../proposals/foundation-v0.1/DATA_TESTS.md §3.4
--
-- Used by E_AMBIGUOUS_PATH (D-018) tests: orders carries both
-- placed_by_id and fulfilled_by_id, each FKing to users.id, so any
-- aggregate `Dimensions: [users.region]` query has two equally-valid
-- paths and the engine MUST refuse to silently pick one.

CREATE TABLE users (
    id     INTEGER PRIMARY KEY,
    region VARCHAR
);

INSERT INTO users VALUES
    (1, 'EAST'),
    (2, 'WEST');

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    placed_by_id    INTEGER,
    fulfilled_by_id INTEGER,
    amount          DECIMAL(10, 2)
);

INSERT INTO orders VALUES
    (301, 1, 2, 100.00),
    (302, 2, 2,  50.00);
