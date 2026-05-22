# Example semantic models

YAML files used by tests, tutorials, and the CLI demo scripts. Each file
is a valid Foundation model — no deferred features.

When adding a model:

- Use the file format defined in [`../../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) §4 ("Semantic Model").
- Declare `primary_key` on every dataset used on the one-side of a
  relationship.
- Add at least one test under `tests/e2e/` that loads this model.

## Available models

| Model | What it covers |
|:--|:--|
| [`demo_orders.yaml`](demo_orders.yaml) | 3-dataset star schema (`orders`, `customers`, `line_items`). Covers simple and composite equijoin relationships, model-scoped metrics, a derived metric (`avg_order_value`), a named filter, and a parameter. Primary teaching model. |
| [`tpcds_thin.yaml`](tpcds_thin.yaml) | Thin slice of the TPC-DS schema: `store_sales`, `store_returns`, `item`, `customer`, `store`. Two independent fact datasets sharing conformed dimensions — the canonical example for multi-fact FULL OUTER stitch queries. |
