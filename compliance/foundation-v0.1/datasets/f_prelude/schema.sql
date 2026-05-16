-- F-PRELUDE — single-fact star with multi-fact extension.
-- Source: ../../../../proposals/foundation-v0.1/DATA_TESTS.md §3.1
--
-- Used by: T-001, T-002, T-003, T-004, T-005x, T-006, T-011, T-016,
--          T-021, T-029, T-033, and most query-shape / predicate-routing
--          / namespace cases.

CREATE TABLE customers (
    id      INTEGER PRIMARY KEY,
    region  VARCHAR,
    segment VARCHAR
);

INSERT INTO customers VALUES
    (1, 'EAST',  'retail'),
    (2, 'EAST',  'retail'),
    (3, 'WEST',  'wholesale'),
    (4, 'NORTH', 'retail');

CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER,
    amount       DECIMAL(10, 2),
    status       VARCHAR
);

-- Note: order 105 is an orphan (customer_id = 99 is not in customers).
INSERT INTO orders VALUES
    (101, 1,  100.00, 'completed'),
    (102, 1,   50.00, 'completed'),
    (103, 2,  200.00, 'pending'),
    (104, 3,   75.00, 'completed'),
    (105, 99,  30.00, 'completed');

CREATE TABLE returns (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER,
    amount       DECIMAL(10, 2)
);

-- Note: customer 4 has a return but no orders — Semantic 3 case.
INSERT INTO returns VALUES
    (201, 1, 10.00),
    (202, 3,  5.00),
    (203, 4, 15.00);

CREATE TABLE premium_customers (
    id INTEGER PRIMARY KEY
);

INSERT INTO premium_customers VALUES (1), (3);
