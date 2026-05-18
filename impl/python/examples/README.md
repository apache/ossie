# Examples

Runnable end-to-end OSI Foundation v0.1 scenarios. Every example is a
pair of:

- a model file under `models/`, and
- a query file under `queries/`.

The CLI commands assume you have `pip install -e .`'d the
implementation; without that, replace `osi` with `python -m osi`.

## Quick recipe

```bash
osi compile examples/models/demo_orders.yaml \
    examples/queries/revenue_by_region.json \
    --dialect duckdb
```

The command prints a complete `WITH … SELECT …` chain you can paste
into any DuckDB / Snowflake / Postgres console.

## Available scenarios

### `demo_orders` model

| Scenario | Query file | What it shows |
|:--|:--|:--|
| Revenue by region | [`queries/revenue_by_region.json`](queries/revenue_by_region.json) | Single metric, cross-dataset enrichment (`orders → customers`), `ORDER BY` on the measure. |
| Multi-metric by segment | [`queries/multi_metric_by_segment.json`](queries/multi_metric_by_segment.json) | Three measures including a derived metric (`avg_order_value = total_revenue / order_count`). Shows metric composition and the `ADD_COLUMNS` codegen step. |
| Filtered — completed orders | [`queries/filtered_completed_orders.json`](queries/filtered_completed_orders.json) | `WHERE` predicate pushed to a `FILTER` step before the join. Demonstrates pre-aggregation predicate routing (D-005). |

### `tpcds_thin` model

| Scenario | Query file | What it shows |
|:--|:--|:--|
| Sales by item category | [`queries/tpcds_sales_by_category.json`](queries/tpcds_sales_by_category.json) | Single-fact aggregation with `LIMIT`. Three concurrent metrics — `SUM`, `COUNT`, `AVG` — at the `item.i_category` grain. |
| Sales vs returns by country | [`queries/tpcds_sales_vs_returns.json`](queries/tpcds_sales_vs_returns.json) | **Two-fact query.** `total_sales` (roots at `store_sales`) and `total_returns` (roots at `store_returns`) are planned independently then stitched with a `FULL OUTER JOIN` on the shared `customer` dimension (D-022). |

## Adapting

To plug an example into your own database:

1. Copy the model under `examples/models/` and edit `source:` to point
   at your tables.
2. Compile against the dialect you target (`--dialect snowflake`,
   `--dialect duckdb`, `--dialect ansi`).
3. Paste the printed SQL into your engine, or wrap the call in Python
   using the façade:

```python
from osi import (
    compile_plan,
    Dialect,
    parse_semantic_model,
    plan,
    PlannerContext,
    Reference,
    SemanticQuery,
)

result = parse_semantic_model("examples/models/demo_orders.yaml")
ctx = PlannerContext(
    model=result.model,
    namespace=result.namespace,
    graph=result.graph,
)
query = SemanticQuery(
    dimensions=(Reference(dataset="customers", name="region"),),
    measures=(Reference(dataset=None, name="total_revenue"),),
)
sql = compile_plan(plan(query, ctx), dialect=Dialect.DUCKDB)
print(sql)
```
