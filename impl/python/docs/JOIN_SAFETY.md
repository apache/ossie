# Join Safety in OSI

OSI enforces join safety at the algebra level to prevent common BI pitfalls: fan traps, chasm traps, and incorrect semi-additive aggregations.

---

## Table of Contents

1. [Overview](#overview)
2. [Fan Traps (Explosion Safety)](#fan-traps-explosion-safety)
3. [Chasm Traps](#chasm-traps)
4. [Snapshot Safety (Semi-Additive Measures)](#snapshot-safety-semi-additive-measures)
5. [Error Codes](#error-codes)
6. [Comparison to Other Tools](#comparison-to-other-tools)
7. [Examples](#examples)

---

## Overview

Join safety is enforced through **column-level flags** tracked through the query planning process:

| Flag | Purpose | Set When |
|------|---------|----------|
| `is_join_exploded` | Tracks join fan-out | Column comes from many-side of N:1 join |
| `snapshot_dimensions` | Tracks semi-additive measures | Column is balance/inventory at point in time |
| `is_single_valued` | Tracks filtered dimensions | Column filtered to single value (WHERE x = const) |

These flags flow through all algebra operations (aggregate, enrich, filtering, etc.) and are validated before aggregation.

---

## Fan Traps (Explosion Safety)

### What is a Fan Trap?

A **fan trap** occurs when one table (center) has multiple child tables (many-side), creating a fan-out pattern:

```
Orders (many) → Products (one) ← Suppliers (many)
```

**Problem**: Joining Orders → Products ← Suppliers duplicates order rows (once per supplier):

```sql
-- WRONG: This multiplies order amounts by supplier count!
SELECT
    p.product_name,
    SUM(o.amount) as total_orders,      -- WRONG! Multiplied
    SUM(s.cost) as total_supplier_cost  -- WRONG! Also wrong
FROM orders o
JOIN products p ON o.product_id = p.product_id
JOIN suppliers s ON p.product_id = s.product_id
GROUP BY p.product_name;
```

If Product A has 2 suppliers, each order for Product A gets counted twice!

### How OSI Prevents Fan Traps

**Step 1: Mark exploded columns**

When enriching from many-to-one join:

```python
# Orders -> Products (safe)
state = enrich(orders_state, products_state, [("product_id", "product_id")])
# products.* columns are NOT marked exploded (coming from one-side)

# Products <- Suppliers (fan-out!)
state = enrich(state, suppliers_state, [("product_id", "product_id")])
# suppliers.* columns ARE marked is_join_exploded=True
```

**Step 2: Enforce explosion-safe aggregations**

```python
# This will FAIL with E4001:
aggregate(state, grain=["product_name"], aggregations=[
    ("total_cost", "SUM(suppliers.cost)")  # ❌ E4001!
])

# This will SUCCEED (explosion-safe):
aggregate(state, grain=["product_name"], aggregations=[
    ("min_cost", "MIN(suppliers.cost)"),           # ✅ MIN is safe
    ("supplier_count", "COUNT(DISTINCT supplier_id)")  # ✅ COUNT DISTINCT is safe
])
```

### Explosion-Safe Aggregations

These aggregations are **safe** on exploded columns (produce correct results despite duplication):

```python
EXPLOSION_SAFE_AGGREGATIONS = {
    "MIN", "MAX",
    "COUNT", "COUNT_DISTINCT",
    "ANY_VALUE",
    "ARRAY_AGG", "LISTAGG"
}
```

**Unsafe** (blocked by E4001):
- `SUM` - Would sum duplicated values
- `AVG` - Would average duplicated values
- `STDDEV`, `VARIANCE` - Would compute on duplicated values

### Workarounds

**Option 1: Pre-aggregate before join**

```yaml
# Create metric that aggregates suppliers at product level first
avg_supplier_cost_per_product:
  expression: AVG(suppliers.cost)
  grain: { mode: FIXED, dimensions: [product_id] }

# Then query at product level:
dimensions: [products.product_name]
measures: [total_order_amount, avg_supplier_cost_per_product]
```

**Option 2: Use explosion-safe aggregation**

```yaml
dimensions: [products.product_name]
measures:
  - total_order_amount
  - { name: min_supplier_cost, expression: MIN(suppliers.cost) }
  - { name: max_supplier_cost, expression: MAX(suppliers.cost) }
```

**Option 3: UNSAFE override** (use with caution!)

```yaml
# Bypass safety check (may produce INCORRECT results)
measures:
  - { name: total_cost, expression: UNSAFE(SUM(suppliers.cost)) }
```

---

## Chasm Traps

### What is a Chasm Trap?

A **chasm trap** occurs when two fact tables share a dimension:

```
Sales (many) → Date (one) ← Budgets (many)
```

**Problem**: Naive join creates cartesian product at shared dimension level:

```sql
-- WRONG: Cartesian product!
SELECT
    d.date,
    SUM(s.amount) as total_sales,     -- WRONG! Multiplied by budget rows
    SUM(b.budget) as total_budget     -- WRONG! Multiplied by sales rows
FROM sales s
JOIN date_dim d ON s.date_id = d.date_id
JOIN budgets b ON d.date_id = b.date_id
GROUP BY d.date;
```

If date 1/15 has 2 sales and 2 budgets, the join creates 2×2=4 rows!

### How OSI Handles Chasm Traps

**Detection**: `RelationshipGraph.detect_chasm_trap()` identifies the pattern

**Resolution**: Aggregate each fact **independently** at shared dimension grain, then **merge**:

```sql
-- CORRECT: Independent aggregation + merge
WITH sales_by_date AS (
    SELECT date_id, SUM(amount) as total_sales
    FROM sales
    GROUP BY date_id
),
budgets_by_date AS (
    SELECT date_id, SUM(budget) as total_budget
    FROM budgets
    GROUP BY date_id
)
SELECT
    d.date,
    COALESCE(s.total_sales, 0) as total_sales,
    COALESCE(b.total_budget, 0) as total_budget
FROM date_dim d
LEFT JOIN sales_by_date s ON d.date_id = s.date_id
LEFT JOIN budgets_by_date b ON d.date_id = b.date_id;
```

**In OSI**: The LOD planner automatically creates separate branches for each fact and merges at query grain.

---

## Snapshot Safety (Semi-Additive Measures)

### What is a Snapshot Dimension?

A **snapshot dimension** is a point-in-time axis (usually date) for measures that shouldn't be summed across it.

**Example**: Account balances

```
account_snapshots(account_id, date, balance)
  2024-01-15: $1000
  2024-01-16: $1200
  2024-01-17: $1100
```

**Problem**: `SUM(balance)` across dates = $3,300 counts the same money 3 times!

### How OSI Prevents Incorrect Snapshot Aggregation

**Step 1: Mark snapshot columns**

```python
Column(
    name="balance",
    expression="balance",
    dependencies=frozenset(),
    is_agg=False,
    snapshot_dimensions=frozenset({"snapshot_date"})  # ← Key!
)
```

**Step 2: Enforce snapshot-safe aggregations**

```python
# This will FAIL with E4002:
aggregate(state, grain=["account_id"], aggregations=[
    ("total_balance", "SUM(balance)")  # ❌ E4002! (date not filtered)
])

# This will SUCCEED:
aggregate(
    filtering(state, "snapshot_date = '2024-01-17'"),  # Filter to single date
    grain=["account_id"],
    aggregations=[("total_balance", "SUM(balance)")]  # ✅ Now safe!
)
```

### Snapshot-Safe Aggregations

These aggregations are **safe** on snapshot columns (don't sum across time):

```python
SNAPSHOT_SAFE_AGGREGATIONS = {
    "MIN", "MAX",
    "COUNT", "COUNT_DISTINCT",
    "ANY_VALUE",
    "FIRST_VALUE", "LAST_VALUE"
}
```

**Unsafe** (blocked by E4002):
- `SUM` - Would sum same balance multiple times
- `AVG` - Would average across snapshots incorrectly

### Workarounds

**Option 1: Filter to single date**

```yaml
measures:
  - name: balance_at_date
    expression: SUM(account_snapshots.balance)
    filter: snapshot_date = '2024-01-17'
```

**Option 2: Use snapshot-safe aggregation**

```yaml
measures:
  - { name: max_balance, expression: MAX(account_snapshots.balance) }
  - { name: min_balance, expression: MIN(account_snapshots.balance) }
  - { name: latest_balance, expression: LAST_VALUE(account_snapshots.balance) }
```

**Option 3: Use FIXED grain at specific date**

```yaml
balance_at_latest_date:
  expression: SUM(account_snapshots.balance)
  grain: { mode: FIXED, dimensions: [snapshot_date] }
  filter: snapshot_date = (SELECT MAX(snapshot_date) FROM account_snapshots)
```

---

## Error Codes

### E4001: Explosion-Unsafe Aggregation

**When**: Trying to use SUM/AVG on a column from the many-side of a join

**Message**:
```
Cannot use ['SUM'] on join-exploded column 'cost' — explosion-unsafe aggregation
```

**Suggestions**:
- Use explosion-safe aggregations: MIN, MAX, COUNT, COUNT_DISTINCT
- Pre-aggregate the column before joining to remove explosion
- Use UNSAFE(SUM(...)) to bypass safety checks (may produce incorrect results)
- Note: SUM and AVG are unsafe because join explosion duplicates rows

**Fix**: See [Fan Traps](#fan-traps-explosion-safety) workarounds

---

### E4002: Snapshot-Unsafe Aggregation

**When**: Trying to use SUM on a semi-additive measure across snapshot dimension

**Message**:
```
Cannot use ['SUM'] on snapshot column 'balance' with dimensions ['snapshot_date'] — snapshot-unsafe aggregation
```

**Suggestions**:
- Use snapshot-safe aggregations: MIN, MAX, FIRST_VALUE, LAST_VALUE
- Filter ['snapshot_date'] to single value before aggregating
- Use filter_to_remove_lod() to restrict snapshot dimension
- Note: SUM is unsafe because it would sum across snapshot points
- Example: SUM(balance) across dates would count the same balance multiple times

**Fix**: See [Snapshot Safety](#snapshot-safety-semi-additive-measures) workarounds

---

## Comparison to Other Tools

| Tool | Fan Trap Detection | Chasm Trap Handling | Snapshot Safety |
|------|-------------------|---------------------|-----------------|
| **OSI** | ✅ Automatic (E4001) | ✅ Automatic (separate branches) | ✅ Automatic (E4002) |
| **Looker** | ⚠️ Manual (`sql_always_where`, `always_filter`) | ⚠️ Manual (symmetric aggregates, fanout warning) | ⚠️ Manual field type |
| **Tableau** | ❌ None (user must LOD) | ❌ None (user must understand blending) | ⚠️ Manual (user must know to filter) |
| **Power BI** | ⚠️ DAX relationships (user must model correctly) | ⚠️ DAX context (user must understand) | ⚠️ Manual measure logic |
| **dbt** | ❌ None (pure SQL) | ❌ None (pure SQL) | ❌ None (pure SQL) |
| **MetricFlow** | ⚠️ Entity paths (manual modeling) | ⚠️ Multi-hop joins (user defined) | ⚠️ Manual metric type |

**Key**: ✅ Automatic enforcement, ⚠️ Requires manual configuration, ❌ No support

**OSI advantage**: Catches errors at **planning time** before execution, with clear error messages and suggestions.

---

## Examples

See E2E tests for executable examples:

- **Fan trap**: `tests/e2e/test_join_safety_e2e.py::TestFanTrapDetection`
  - Schema: `tests/e2e/schemas/fan_trap_orders.yaml`
  - Data: `tests/e2e/data/fan_trap_orders.sql`

- **Chasm trap**: `tests/e2e/test_join_safety_e2e.py::TestChasmTrapDetection`
  - Schema: `tests/e2e/schemas/chasm_trap_sales.yaml`
  - Data: `tests/e2e/data/chasm_trap_sales.sql`

- **Snapshot safety**: `tests/e2e/test_join_safety_e2e.py::TestSnapshotSafety`
  - Schema: `tests/e2e/schemas/snapshot_accounts.yaml`
  - Data: `tests/e2e/data/snapshot_accounts.sql`

---

## References

- **Algebra implementation**: `src/osi/planning/algebra.py`
  - `_validate_aggregation_safety()` (lines 580-679)
  - Explosion safety enforcement
  - Snapshot safety enforcement

- **State tracking**: `src/osi/planning/state.py`
  - `Column.is_join_exploded` flag
  - `Column.snapshot_dimensions` tracking

- **Graph detection**: `src/osi/parsing/graph.py`
  - `RelationshipGraph.detect_fan_trap()`
  - `RelationshipGraph.detect_chasm_trap()`

- **Spec reference**: `specs/OSI_Calc_Model_Semantics.md`
  - Section on explosion safety
  - Section on semi-additive measures

---

## Summary

OSI's join safety mechanisms catch common BI errors at **planning time**:

1. **Fan traps** → E4001 prevents explosion-unsafe aggregations
2. **Chasm traps** → Automatic independent aggregation + merge
3. **Snapshot issues** → E4002 prevents incorrect semi-additive sums

These protections are **automatic** (no manual configuration) and provide **clear error messages** with actionable suggestions.

The algebra tracks safety flags through all transformations, ensuring correctness is maintained throughout the query planning process.
