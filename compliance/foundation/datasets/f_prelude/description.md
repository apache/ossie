# F-PRELUDE

The flagship Foundation fixture — a single-fact star (`customers ←
orders`, `customers ← returns`) with one orphan order (customer_id =
99) and one customer with no orders (id = 4). Every Foundation
semantic that exercises join defaults, NULL group keys, multi-fact
stitch, and basic aggregation runs against this fixture.

Authoritative source: `../../DATA_TESTS.md §3.1`.

The deliberate edge cases:

- **Orphan order (105)**: tests Semantic 1 (`LEFT` enrichment surfaces
  orphan orders under `region = NULL`).
- **Customer 4 with returns but no orders**: tests Semantic 3 (the
  `NORTH` region appears in the multi-fact stitch even though it has
  no orders).
- **Both `EAST` customers (1 and 2) have orders, customer 3 in `WEST`
  has orders too**: gives the bridge / chasm tests a non-degenerate
  cardinality.

Aggregate witnesses:

- `SUM(orders.amount)` total: 455.00
- `SUM(orders.amount)` by region (LEFT enrichment): EAST=350.00,
  WEST=75.00, NULL=30.00.
- `SUM(returns.amount)` by region: EAST=10.00, WEST=5.00, NORTH=15.00.
- `COUNT(orders.id)` total: 5; with `Where status='completed'`: 4.
- Distinct customers: 4.
