-- F-REFLEXIVE — self-referential employee hierarchy.
-- Source: introduced in S-E to back T-046 (D-018 reflexive relationship).

CREATE TABLE employees (
    id         INTEGER PRIMARY KEY,
    name       VARCHAR,
    manager_id INTEGER,
    region     VARCHAR
);

INSERT INTO employees VALUES
    (1, 'Alice',   NULL, 'EAST'),
    (2, 'Bob',     1,    'EAST'),
    (3, 'Carol',   1,    'WEST'),
    (4, 'Dave',    2,    'EAST'),
    (5, 'Eve',     2,    'EAST'),
    (6, 'Frank',   3,    'WEST');
