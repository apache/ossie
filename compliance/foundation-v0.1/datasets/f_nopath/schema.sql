-- F-NOPATH — two disconnected datasets (no relationships).
-- Source: ../../DATA_TESTS.md §3.5
--
-- Used by E_NO_PATH (D-018) and E3013_NO_STITCHING_DIMENSION tests:
-- orders and inventory_movements share no key and have no declared
-- relationship, so any cross-dataset query MUST fail closed.

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER,
    amount      DECIMAL(10, 2)
);

INSERT INTO orders VALUES
    (101, 1, 100.00),
    (102, 1,  50.00);

CREATE TABLE inventory_movements (
    movement_id  INTEGER PRIMARY KEY,
    warehouse_id VARCHAR,
    quantity     INTEGER
);

INSERT INTO inventory_movements VALUES
    (1, 'W-EAST', 10),
    (2, 'W-WEST',  5);
