-- F-COMPOSITE — composite-key relationship.
-- Source: introduced in S-E to back T-044 (D-009 composite-key join).

CREATE TABLE inventory (
    store_id      INTEGER,
    sku           VARCHAR,
    stock_level   INTEGER,
    reorder_point INTEGER,
    PRIMARY KEY (store_id, sku)
);

INSERT INTO inventory VALUES
    (1, 'A', 100, 20),
    (1, 'B',  50, 10),
    (2, 'A',  30, 15),
    (2, 'C',  80, 25);

CREATE TABLE sales (
    id       INTEGER PRIMARY KEY,
    store_id INTEGER,
    sku      VARCHAR,
    qty      INTEGER,
    sale_ts  TIMESTAMP
);

INSERT INTO sales VALUES
    (10, 1, 'A',  5, TIMESTAMP '2026-01-01 10:00:00'),
    (11, 1, 'A',  3, TIMESTAMP '2026-01-02 11:00:00'),
    (12, 1, 'B',  2, TIMESTAMP '2026-01-02 12:00:00'),
    (13, 2, 'A',  4, TIMESTAMP '2026-01-03 09:00:00'),
    (14, 2, 'C',  1, TIMESTAMP '2026-01-03 14:00:00');
