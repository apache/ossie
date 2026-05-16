# Example semantic models

YAML files used by tests, tutorials, and the CLI demo scripts. Each file
is a valid Foundation model — no deferred features.

When adding a model:

- Use the file format in [`../../specs/OSI_core_file_format.md`](../../specs/OSI_core_file_format.md).
- Declare `primary_key` on every dataset used on the one-side of a
  relationship.
- Declare `referential_integrity` where the relationship is known to be
  inner-join-safe (so the planner can emit `INNER JOIN` instead of
  `LEFT JOIN`).
- Add at least one test under `tests/e2e/` that loads this model.

## Canonical examples (planned)

- `demo_orders.yaml` — single dataset, basic aggregations.
- `sales_returns.yaml` — two facts + shared `customers` dimension; used
  for chasm-trap E2E tests.
- `tpcds_subset.yaml` — TPC-DS schema reduced to the tables the thin
  slice covers.
