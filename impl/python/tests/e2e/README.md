# End-to-end tests

DuckDB-executed tests that assert on **rows**, not SQL shape. See
[`../../docs/TESTING_STRATEGY.md §5`](../../docs/TESTING_STRATEGY.md#5-layer-4-end-to-end).

Each test:

1. Loads a model from `../../examples/models/`.
2. Builds a plan and renders SQL.
3. Executes the SQL against an in-memory DuckDB.
4. Asserts on the row set.

## Rule

If a test asserts on the SQL string (like "has three CTEs"), it belongs
in `tests/golden/`, not here.
