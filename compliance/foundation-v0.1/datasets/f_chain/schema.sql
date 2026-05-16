-- F-CHAIN — multi-hop N:1 enrichment chain.
-- Source: introduced in S-E to back T-043 (D-004 multi-hop chain).

CREATE TABLE segments (
    id   INTEGER PRIMARY KEY,
    name VARCHAR
);

INSERT INTO segments VALUES
    (1, 'retail'),
    (2, 'wholesale'),
    (3, 'partner');

CREATE TABLE customers (
    id         INTEGER PRIMARY KEY,
    segment_id INTEGER,
    region     VARCHAR
);

INSERT INTO customers VALUES
    (1, 1, 'EAST'),
    (2, 1, 'WEST'),
    (3, 2, 'EAST'),
    (4, 3, 'WEST');

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER,
    amount      DECIMAL(10, 2),
    status      VARCHAR
);

INSERT INTO orders VALUES
    (1001, 1, 100.00, 'completed'),
    (1002, 2, 200.00, 'completed'),
    (1003, 3, 300.00, 'pending'),
    (1004, 4,  50.00, 'completed');

CREATE TABLE order_lines (
    id       INTEGER PRIMARY KEY,
    order_id INTEGER,
    sku      VARCHAR,
    qty      INTEGER,
    price    DECIMAL(10, 2)
);

INSERT INTO order_lines VALUES
    (5001, 1001, 'A', 2, 25.00),
    (5002, 1001, 'B', 1, 50.00),
    (5003, 1002, 'A', 4, 25.00),
    (5004, 1003, 'C', 3, 100.00),
    (5005, 1004, 'B', 1, 50.00);
