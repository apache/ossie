# Proposal: Static Pivot Operator for OSI

**Status:** Draft Proposal
**Author:** will.pugh@snowflake.com
**Date:** 2026-02-22
**Related specs:**
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md)
- [SQL Expression Subset](./SQL_EXPRESSION_SUBSET.md)

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Design Principles](#2-design-principles)
3. [Syntax](#3-syntax)
   - [Semantic Query Level](#31-semantic-query-level)
   - [Model Level (Metric Definition)](#32-model-level-metric-definition)
4. [LOD / Grain Semantics](#4-lod--grain-semantics)
   - [Grain Rule](#41-grain-rule)
   - [CalculationState Changes](#42-calculationstate-changes)
   - [Interaction with LOD Modes](#43-interaction-with-lod-modes)
   - [Unmatched Values (Residual Rows)](#44-unmatched-values-residual-rows)
   - [Filter Interaction with Pivot Dimension](#45-filter-interaction-with-pivot-dimension)
5. [Algebra Operation](#5-algebra-operation)
   - [Pivot Operation Definition](#51-pivot-operation-definition)
   - [Aggregate Function Extraction](#52-aggregate-function-extraction)
   - [Column Ordering](#53-column-ordering)
   - [Safety Infrastructure Compatibility](#54-safety-infrastructure-compatibility)
   - [Position in the Algebra](#55-position-in-the-algebra)
6. [SQL Generation](#6-sql-generation)
   - [Semantic SQL Syntax](#61-semantic-sql-syntax)
7. [Proposed Spec Changes](#7-proposed-spec-changes)
   - [OSI_Core_Abstractions.md](#71-osi_core_abstractionsmd)
   - [OSI_Calc_Model_Semantics.md](#72-osi_calc_model_semanticsmd)
   - [SQL_EXPRESSION_SUBSET.md](#73-sql_expression_subsetmd)
8. [Implementation Steps](#8-implementation-steps)
9. [Out of Scope](#9-out-of-scope)

---

## 1. Motivation

Several analytical queries require transforming dimension values into separate output columns.  In TPC-DS, at least 9 of the 99 benchmark queries use this pattern:

| Query | Pivot Dimension | Values | Pattern |
|:---|:---|:---|:---|
| Q43, Q59 | `d_day_name` | Sunday–Saturday (7) | Revenue by day of week as columns |
| Q50, Q62, Q99 | Return/shipping delay range | 30d, 60d, 90d, 120d, >120d | Delay bucket counts as columns |
| Q88 | `t_hour` ranges | 8 hour-of-day shifts | Transaction counts per shift |
| Q66 | `sm_type` | Ship mode names | Sales per ship mode as columns |
| Q9 | Quantity ranges | 5 quantity buckets | Conditional aggregation per bucket |

Today, OSI handles these via ad-hoc `CASE WHEN` measures — the user must manually write N separate `SUM(CASE WHEN dim = 'value' THEN measure END)` expressions.  This is verbose, error-prone, and obscures the analytical intent.

A first-class pivot operator would:

1. **Reduce verbosity**: 1 pivot spec replaces N CASE WHEN measures
2. **Preserve analytical intent**: "pivot revenue by day of week" is clearer than 7 CASE WHEN expressions
3. **Enable clean grain tracking**: The algebra can formally track that the pivot dimension was consumed
4. **Generate optimal SQL**: The transpiler can emit either CASE WHEN (universal) or native PIVOT syntax (Snowflake, DuckDB, BigQuery)

---

## 2. Design Principles

1. **Static values only**: The pivot values MUST be enumerated at query/model definition time.  No data-dependent column generation.
2. **Syntactic sugar over CASE WHEN**: Pivot is a convenience layer.  It MUST produce identical results to the equivalent manual CASE WHEN measures.
3. **Clean grain algebra**: Pivot has a precise, well-defined effect on grain — it removes exactly one dimension and adds N aggregated columns.
4. **No schema discovery**: The semantic layer never inspects the data to determine pivot columns.  This preserves the principle that query plans are deterministic from the model + query alone.
5. **Composable**: Pivoted columns are regular aggregated columns in the resulting state.  They can participate in further composition, window functions, and filtering.

---

## 3. Syntax

### 3.1 Semantic Query Level

A `pivot` property is added to measure requests within the semantic query, consistent with how `grain` and `filter` are per-metric properties in OSI:

```yaml
query:
  dataset_name: store_sales
  dimensions: [s_store_name, s_store_id]
  measures:
    - output_name: daily_sales
      metric_name: ss_total_sales
      pivot:
        dimension: d_day_name
        values:
          - { value: "Sunday",    output_name: sun_sales }
          - { value: "Monday",    output_name: mon_sales }
          - { value: "Tuesday",   output_name: tue_sales }
          - { value: "Wednesday", output_name: wed_sales }
          - { value: "Thursday",  output_name: thu_sales }
          - { value: "Friday",    output_name: fri_sales }
          - { value: "Saturday",  output_name: sat_sales }
  where: "d_year = 2000 AND s_gmt_offset = -5"
```

**Pivot schema (on a measure request or metric definition):**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `dimension` | string | Yes | The dimension field whose values become columns.  This dimension is consumed — it is removed from the output grain for this measure's branch. |
| `values` | array | Yes | List of value specifications (see below). Must have at least one entry. |
| `residual_column` | string | No | If set, an additional output column with this name is generated to capture rows whose pivot dimension value does not match any entry in `values`.  If not set, unmatched rows are silently excluded from all pivot columns.  See [§4.4 Unmatched Values](#44-unmatched-values-residual-rows). |

**Value specification:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `value` | scalar | Yes | The dimension value to pivot on.  Must be a literal (string, number, boolean).  `null` is NOT supported as a pivot value — NULL dimension values are unmatched (see [§4.4](#44-unmatched-values-residual-rows)); use `residual_column` to capture them. |
| `output_name` | string | Yes | The output column name for this pivot value. Must be unique across the **entire query output** — not just within this pivot, but across all pivots and non-pivoted measures in the query. |

**Value shorthand syntax:**

For simple cases where the output column name can be derived from the value, a string shorthand is supported:

```yaml
pivot:
  dimension: d_day_name
  output_prefix: sales_    # optional — prepended to auto-generated names
  values: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
  # Equivalent to: [{ value: "Sunday", output_name: sales_sunday }, ...]
```

When a `values` entry is a plain string instead of a `{value, output_name}` object:
- `value` = the string itself
- `output_name` = `output_prefix` (default `""`) + lowercase value with spaces replaced by underscores

The explicit `{value, output_name}` form is always available for full control.  The two forms may be mixed within the same `values` list.

| Field | Type | Required | Default | Description |
|:---|:---|:---|:---|:---|
| `output_prefix` | string | No | `""` | Prefix prepended to auto-generated output names when using the string shorthand. |

**Semantics:**

The pivoted measure above is semantically equivalent to 7 independent ad-hoc measures, each with a CASE WHEN conditional aggregation:

```yaml
dimensions: [s_store_name, s_store_id]
measures:
  - { output_name: sun_sales, expression: "SUM(CASE WHEN d_day_name = 'Sunday' THEN ss_sales_price ELSE NULL END)" }
  - { output_name: mon_sales, expression: "SUM(CASE WHEN d_day_name = 'Monday' THEN ss_sales_price ELSE NULL END)" }
  # ... etc for all 7 days
```

The measure's own metric expression (here `ss_total_sales`, which resolves to `SUM(ss_sales_price)`) provides the aggregation function and measure column.  The pivot auto-generates the CASE WHEN wrapper for each value.  See [§5.3 Aggregate Function Extraction](#53-aggregate-function-extraction) for the precise decomposition rules.

**Key rules:**

1. The `pivot.dimension` MUST NOT appear in the `dimensions` list (it would be redundant — the pivot consumes it).
2. The `pivot.dimension` MUST be a dimension reachable from the primary dataset.
3. The measure that the pivot is attached to MUST have a valid aggregation expression (metric reference or inline expression with a single outermost aggregation — see [§5.2](#52-aggregate-function-extraction)).
4. If the query also has non-pivoted `measures`, those are computed at the output grain (without the pivot dimension) alongside the pivoted columns.
5. **Multiple pivots** are allowed within a single query, subject to the following constraints:
   - Each pivot is associated with an **independent measure** and becomes its own planner branch.  The branches compose at the final query grain via the standard LOD composition mechanism (Merge / Enrich).
   - Two measures MAY pivot on the **same dimension** — they are independent branches, each with their own copy of the dimension.  For example, `SUM(sales)` pivoted by `d_day_name` and `COUNT(*)` pivoted by `d_day_name` produce distinct column sets (`sun_revenue` vs `sun_count`) and compose correctly.
   - A single measure CANNOT have more than one pivot (multi-dimensional pivot — e.g., day × shift = 21 columns — is out of scope; see §9).
   - All output column names MUST be unique across the **entire query output** — across all pivots and non-pivoted measures.
   - Non-pivoted measures may coexist freely alongside pivoted measures in the same query.

**Example — two independent pivots on different dimensions:**

```yaml
query:
  dataset_name: store_sales
  dimensions: [s_store_name]
  measures:
    - output_name: daily_sales
      metric_name: ss_total_sales
      pivot:
        dimension: d_day_name
        values:
          - { value: "Sunday",  output_name: sun_sales }
          - { value: "Monday",  output_name: mon_sales }
          # ... etc
    - output_name: shift_sales
      metric_name: ss_total_sales
      pivot:
        dimension: t_shift
        values:
          - { value: "morning",   output_name: morning_sales }
          - { value: "afternoon", output_name: afternoon_sales }
          - { value: "evening",   output_name: evening_sales }
```

The planner handles this identically to two metrics at different LODs:
- Branch A: grain `{s_store_name, d_day_name}` → Pivot on `d_day_name` → grain `{s_store_name}`, produces 7 columns
- Branch B: grain `{s_store_name, t_shift}` → Pivot on `t_shift` → grain `{s_store_name}`, produces 3 columns
- Compose: Merge both branches at grain `{s_store_name}` → 1 row per store with 10 measure columns

**Example — two pivots on the same dimension (different measures):**

```yaml
query:
  dataset_name: store_sales
  dimensions: [s_store_name]
  measures:
    - metric_name: ss_total_sales       # SUM(ss_ext_sales_price)
      pivot:
        dimension: d_day_name
        values:
          - { value: "Sunday",  output_name: sun_revenue }
          - { value: "Monday",  output_name: mon_revenue }
          # ... etc
    - metric_name: ss_transaction_count  # COUNT(*)
      pivot:
        dimension: d_day_name
        values:
          - { value: "Sunday",  output_name: sun_count }
          - { value: "Monday",  output_name: mon_count }
          # ... etc
```

Both branches consume `d_day_name` independently:
- Branch A: `SUM(sales)` pivoted by `d_day_name` → `{sun_revenue, mon_revenue, ...}`
- Branch B: `COUNT(*)` pivoted by `d_day_name` → `{sun_count, mon_count, ...}`
- Compose: Merge at grain `{s_store_name}` → 1 row per store with 14 columns (7 revenue + 7 count)

This works because each measure is its own branch — they each get their own copy of the pivot dimension.  The only constraint is that output column names are unique across the entire query.

This is the same branch / compose pattern the planner already uses for FIXED, INCLUDE, EXCLUDE, and filter-isolated metrics.

### 3.2 Model Level (Metric Definition)

Pivot can also be used in metric definitions to create reusable pivoted metric sets:

```yaml
metrics:
  - name: daily_store_sales
    description: Store sales broken down by day of week as separate columns
    expression: SUM(ss_sales_price)
    pivot:
      dimension: d_day_name
      values:
        - { value: "Sunday",    output_name: sun_sales }
        - { value: "Monday",    output_name: mon_sales }
        - { value: "Tuesday",   output_name: tue_sales }
        - { value: "Wednesday", output_name: wed_sales }
        - { value: "Thursday",  output_name: thu_sales }
        - { value: "Friday",    output_name: fri_sales }
        - { value: "Saturday",  output_name: sat_sales }
```

When a pivoted metric is used in a query, the pivot dimension is automatically consumed.  The query does not need its own `pivot:` — the metric carries the pivot spec:

```yaml
query:
  dimensions: [s_store_name, s_store_id]
  measures:
    - { metric_name: daily_store_sales }
  where: "d_year = 2000"
```

This expands to 7 output columns (`sun_sales` through `sat_sales`) at the grain `{s_store_name, s_store_id}`.  The pivot dimension `d_day_name` does not appear in the output.

Multiple model-level pivoted metrics compose naturally in a single query — each becomes its own planner branch.

**Interaction with grain modes:** When a pivoted metric has a `grain` specification, the pivot dimension is consumed from the *effective* grain:

```yaml
- name: customer_daily_sales
  expression: SUM(ss_sales_price)
  grain:
    mode: FIXED
    dimensions: [ss_customer_sk, d_day_name]
  pivot:
    dimension: d_day_name
    values: [...]
  # Effective grain after pivot: FIXED [ss_customer_sk]
  # (d_day_name consumed by pivot)
```

---

## 4. LOD / Grain Semantics

### 4.1 Grain Rule

**Pivot removes exactly one dimension from the grain and replaces it with N aggregated measure columns.**

Formally:

```
grain_after = grain_before − {pivot_dimension}
```

This is the same direction as `EXCLUDE` (removing a dimension), but applied structurally to the column layout rather than as an LOD computation modifier.

| | Before Pivot | After Pivot |
|:---|:---|:---|
| **Grain** | `{s_store_name, s_store_id, d_day_name}` | `{s_store_name, s_store_id}` |
| **Dimension columns** | s_store_name, s_store_id, d_day_name | s_store_name, s_store_id |
| **Measure columns** | total_sales (1 column) | sun_sales, mon_sales, ..., sat_sales (7 columns) |
| **Rows per store** | 7 (one per day) | 1 (all days as columns) |

### 4.2 CalculationState Changes

After a `Pivot` operation, the resulting `CalculationState` has:

| Property | Value |
|:---|:---|
| **grain** | `original_grain − {pivot_dimension}` |
| **columns** | Original grain columns (minus pivot dimension) + N new pivoted measure columns |
| **Pivoted column properties** | |
| `is_agg` | `True` — each pivoted column is an aggregation |
| `num_aggs` | Incremented from the source measure's `num_aggs` |
| `is_join_exploded` | Inherited from the source measure column |
| `snapshot_dimensions` | Inherited from the source measure column |
| `is_single_valued` | `False` |
| `dependencies` | The pivot dimension + the measure's dependencies |

The pivot dimension column is **removed** from the state's column set.  It no longer exists as an independent column — its information is now encoded in the column names of the pivoted measures.

### 4.3 Interaction with LOD Modes

| LOD Mode | Pivot Behavior | Effective Grain |
|:---|:---|:---|
| **QUERY** (default) | Pivot dimension removed from query grain | `query_dims − {pivot_dim}` |
| **FIXED [dims]** | Pivot dimension must be in the FIXED dims; removed after pivot | `fixed_dims − {pivot_dim}` |
| **INCLUDE [dims]** | If pivot dimension is in INCLUDE dims, removed after pivot | `(query_dims ∪ include_dims) − {pivot_dim}` |
| **EXCLUDE [dims]** | Pivot dimension is already removed from grain by EXCLUDE; pivot on an EXCLUDE'd dimension is an error (it's not in the grain to consume) | Error |

**Validation rules:**

1. The pivot dimension MUST be present in the effective grain *before* the pivot is applied.  Otherwise there is nothing to consume.
2. If the pivot dimension is the ONLY dimension in the grain, the resulting grain is empty (`{}`) — the pivot produces a single row with N columns (grand-total pivot).

### 4.4 Unmatched Values (Residual Rows)

When a row's pivot dimension value does not match any of the enumerated `values`, it is **silently included in the aggregation but contributes NULL to every pivoted column**.  This follows directly from the CASE WHEN expansion — each column evaluates `AGG(CASE WHEN dim = value THEN expr ELSE NULL END)`, and `NULL` is ignored by all standard aggregation functions (`SUM`, `COUNT`, `AVG`, `MIN`, `MAX`).

**Concrete example:**

Suppose the data has `d_day_name` values including `'Holiday'` (an unexpected value not in the 7-day pivot list):

| s_store_name | d_day_name | ss_sales_price |
|:---|:---|:---|
| Store A | Sunday | 100 |
| Store A | Monday | 200 |
| Store A | Holiday | 50 |

After pivoting on `d_day_name` with values `[Sunday, Monday, ..., Saturday]`:

| s_store_name | sun_sales | mon_sales | ... | sat_sales |
|:---|:---|:---|:---|:---|
| Store A | 100 | 200 | ... | NULL |

The `Holiday` row's `$50` is **not included in any pivot column**.  It does not cause an error, it is not assigned to a default column — it is simply excluded from all CASE WHEN branches.

**This is intentional and matches standard SQL PIVOT semantics.** The behavior is:

| Scenario | Behavior |
|:---|:---|
| Row matches one pivot value | Contributes to that value's aggregated column |
| Row matches no pivot values | Contributes NULL to every column; effectively excluded from all pivot aggregations |
| Row has NULL pivot dimension | Treated the same as unmatched — `NULL = 'Sunday'` is false in SQL |
| All rows in a group are unmatched | All pivot columns are NULL for that group (the row still appears due to the GROUP BY, with NULLs in every pivot column) |

**Capturing residual values with `residual_column`:**

Setting the optional `residual_column` attribute on the pivot spec adds one additional output column that captures all rows whose dimension value does not match any enumerated value:

```yaml
measures:
  - output_name: daily_sales
    metric_name: ss_total_sales
    pivot:
      dimension: d_day_name
      residual_column: other_sales
      values:
        - { value: "Sunday",    output_name: sun_sales }
        - { value: "Monday",    output_name: mon_sales }
        - { value: "Tuesday",   output_name: tue_sales }
        - { value: "Wednesday", output_name: wed_sales }
        - { value: "Thursday",  output_name: thu_sales }
        - { value: "Friday",    output_name: fri_sales }
        - { value: "Saturday",  output_name: sat_sales }
```

The `residual_column` generates one additional CASE WHEN with the inverse condition:

```sql
SUM(CASE WHEN d_day_name NOT IN ('Sunday','Monday','Tuesday','Wednesday',
    'Thursday','Friday','Saturday') OR d_day_name IS NULL
    THEN ss_sales_price ELSE NULL END) AS other_sales
```

With the example data:

| s_store_name | sun_sales | mon_sales | ... | sat_sales | other_sales |
|:---|:---|:---|:---|:---|:---|
| Store A | 100 | 200 | ... | NULL | 50 |

The `Holiday` row's $50 now appears in `other_sales`.

**Rules for `residual_column`:**

| Attribute | Behavior |
|:---|:---|
| Not set (default) | Unmatched rows excluded from all pivot columns; no residual column generated |
| Set to a name | An additional column is generated capturing all non-matching rows (including NULLs in the pivot dimension) |

The `residual_column` name MUST be unique — it must not collide with any `output_name` in the `values` list or with other columns in the state.

The residual column has the same `CalculationState` properties as the other pivoted columns (`is_agg: True`, etc.).

**The output schema remains fully deterministic** — the column set is known from the pivot spec alone (N value columns + optionally 1 residual column), regardless of what values appear in the data.

### 4.5 Filter Interaction with Pivot Dimension

When a query-level `WHERE` filter references the pivot dimension, the filter is applied **before** the pivot (at the row level).  This means the filter restricts which rows contribute to the pivot aggregations.

**Example — filter narrows the pivot:**

```yaml
dimensions: [s_store_name]
measures:
  - metric_name: ss_total_sales
    pivot:
      dimension: d_day_name
      values:
        - { value: "Sunday",  output_name: sun_sales }
        - { value: "Monday",  output_name: mon_sales }
        - { value: "Saturday", output_name: sat_sales }
where: "d_day_name IN ('Sunday', 'Monday', 'Saturday')"
```

The filter eliminates rows for Tuesday–Friday before the pivot.  The result is identical to pivoting without the filter — `tue_sales` through `fri_sales` columns are simply absent because they aren't in the values list.

**Potentially surprising case — filter on a single pivot value:**

```yaml
where: "d_day_name = 'Sunday'"
```

This would make `mon_sales` through `sat_sales` all NULL because only Sunday rows survive the filter.  The query is technically valid but probably not the user's intent.

**Validation:** The planner SHOULD emit a **warning** (not an error) when a query-level filter constrains the pivot dimension to a strict subset of the pivot values, as this may indicate a user mistake.  The warning is informational — the query still executes correctly.

**Alternative pattern — non-pivoted total for discrepancy detection:**

Users can also include a non-pivoted total alongside the pivoted columns:

```yaml
measures:
  - output_name: daily_sales
    metric_name: ss_total_sales
    pivot:
      dimension: d_day_name
      values: [{ value: "Sunday", output_name: sun_sales }, ...]
  - output_name: total_sales
    metric_name: ss_total_sales
```

Here `total_sales` aggregates *all* rows (including unmatched), while the pivoted columns sum to ≤ total.  Any difference is the residual.  This pattern does not require `residual_column` but requires the consumer to compute the difference.

---

## 5. Algebra Operation

### 5.1 Pivot Operation Definition

#### Pivot(original_state, pivot_dimension, values, measure_expression, agg_function, residual_column=None) → State

**Operation:**
Consumes a dimension from the grain and produces N aggregated columns — one per pivot value.  Each column computes `agg_function(CASE WHEN pivot_dimension = value THEN measure_expression ELSE NULL END)`.  If `residual_column` is set, an additional column is generated for rows not matching any pivot value.

**Parameters:**

| Parameter | Type | Description |
|:---|:---|:---|
| `original_state` | CalculationState | The input state.  Must contain the pivot dimension in its grain. |
| `pivot_dimension` | string | The dimension column to consume.  Must be in `original_state.grain`. |
| `values` | list[{value, output_name}] | The static list of dimension values to pivot on, each with an output column name. |
| `measure_expression` | string | The measure expression to aggregate (e.g., `ss_sales_price`).  Must reference columns in `original_state`. |
| `agg_function` | string | The aggregation function to apply (e.g., `SUM`, `COUNT`, `AVG`). |
| `residual_column` | string or None | Optional.  If set, an additional output column with this name captures rows whose pivot dimension value does not match any entry in `values` (including NULL). |

**Validation:**

1. `pivot_dimension` MUST exist in `original_state.grain`.
2. `pivot_dimension` MUST exist in `original_state.columns`.
3. `measure_expression` MUST reference only columns in `original_state.columns`.
4. `values` MUST have at least one entry.
5. All `output_name` values MUST be unique and MUST NOT collide with existing column names in the state.
6. If `residual_column` is set, it MUST NOT collide with any `output_name` in `values` or with existing column names.
7. The aggregation rules from [Aggregation Rules](./OSI_Calc_Model_Semantics.md#aggregation-rules) apply to the measure column:
   - If `is_join_exploded`, only explosion-safe aggregations are allowed.
   - If `snapshot_dimensions` is set, only snapshot-safe aggregations are allowed.

**Resulting State:**

- **Grain**: `original_state.grain − {pivot_dimension}`
- **Columns**:
  - All grain columns from `original_state` *except* `pivot_dimension`
  - N new columns, one per entry in `values`, each with:
    - `name`: The `output_name` from the value spec
    - `expression`: `agg_function(CASE WHEN pivot_dimension = value THEN measure_expression ELSE NULL END)`
    - `is_agg`: `True`
    - `num_aggs`: `source_measure.num_aggs + 1`
    - `is_join_exploded`: `False` (aggregation resolves explosion)
    - `is_single_valued`: `False`
    - `dependencies`: `{pivot_dimension, ...measure_dependencies}`
  - If `residual_column` is set, 1 additional column:
    - `name`: The `residual_column` value
    - `expression`: `agg_function(CASE WHEN pivot_dimension NOT IN (v1, v2, ...) OR pivot_dimension IS NULL THEN measure_expression ELSE NULL END)`
    - Same properties as the other pivoted columns
- **expression_ids**: Preserved from `original_state`

**Equivalence:**
`Pivot(state, dim, values, expr, AGG, residual_column=None)` is semantically equivalent to:

```
all_values = [v.value for v in values]
aggs = [
    (v.output_name, "AGG(CASE WHEN dim = v.value THEN expr ELSE NULL END)")
    for v in values
]
if residual_column is not None:
    aggs.append(
        (residual_column,
         "AGG(CASE WHEN dim NOT IN (all_values) OR dim IS NULL THEN expr ELSE NULL END)")
    )

Aggregate(state, new_grain = state.grain − {dim}, new_aggs = aggs)
```

This equivalence is the formal guarantee that pivot is pure syntactic sugar — it produces identical results to manual CASE WHEN aggregation.

### 5.2 Aggregate Function Extraction

The pivot operation takes `agg_function` and `measure_expression` as separate parameters, but the user provides a complete metric expression like `SUM(ss_sales_price)`.  The planner must **decompose** the metric expression into its aggregate function and inner expression.

**Decomposition rule:** Given a metric expression of the form `AGG_FUNC(inner_expr)`, the planner extracts:
- `agg_function` = the outermost aggregation function name
- `measure_expression` = the inner expression (argument to the aggregation)

**Examples:**

| Metric Expression | agg_function | measure_expression | Pivot Column Expression |
|:---|:---|:---|:---|
| `SUM(ss_sales_price)` | `SUM` | `ss_sales_price` | `SUM(CASE WHEN dim = val THEN ss_sales_price END)` |
| `SUM(price * quantity)` | `SUM` | `price * quantity` | `SUM(CASE WHEN dim = val THEN price * quantity END)` |
| `COUNT(*)` | `COUNT` | `*` | `COUNT(CASE WHEN dim = val THEN 1 END)` ¹ |
| `COUNT(DISTINCT customer_id)` | `COUNT_DISTINCT` | `customer_id` | `COUNT(DISTINCT CASE WHEN dim = val THEN customer_id END)` |
| `AVG(amount)` | `AVG` | `amount` | `AVG(CASE WHEN dim = val THEN amount END)` |
| `MIN(price)` | `MIN` | `price` | `MIN(CASE WHEN dim = val THEN price END)` |

¹ `COUNT(*)` is special: the CASE WHEN returns `1` (not NULL) for matching rows, since `COUNT(*)` counts rows, not values.  Equivalently, `SUM(CASE WHEN dim = val THEN 1 ELSE 0 END)`.

**Restriction — single outermost aggregation:**

Pivot requires that the metric expression has exactly **one** outermost aggregation function.  Expressions with multiple top-level aggregations or arithmetic between aggregations are **not valid** for direct pivoting:

| Expression | Valid for Pivot? | Reason |
|:---|:---|:---|
| `SUM(amount)` | ✅ | Single aggregation |
| `SUM(price * qty)` | ✅ | Single aggregation with compound inner expression |
| `COUNT(DISTINCT id)` | ✅ | Single aggregation |
| `SUM(amount) / COUNT(*)` | ❌ | Two aggregations — decompose into two pivoted measures and compute ratio via `add_columns` |
| `SUM(amount) - SUM(cost)` | ❌ | Two aggregations — same approach |
| `COALESCE(SUM(a), 0)` | ✅ | Single aggregation wrapped in scalar — the CASE WHEN wraps `a`, and `COALESCE` applies to the result |

For ratio metrics (`SUM(a) / COUNT(*)`), the user should pivot each component separately and then compute the ratio as a derived column:

```yaml
measures:
  - metric_name: total_amount    # SUM(amount)
    pivot: { dimension: d_day_name, values: [...] }
  - metric_name: order_count     # COUNT(*)
    pivot: { dimension: d_day_name, values: [...] }
  # Then use add_columns (or a derived expression) to compute
  # sun_avg = sun_amount / sun_count, etc.
```

**Composition metrics (nested AGG):**

If the metric expression uses composition (e.g., `AVG(customer_revenue)` where `customer_revenue` is `SUM(amount) FIXED [customer_id]`), the inner metric is resolved first by the standard composition pipeline.  The pivot's CASE WHEN wraps the **innermost measure expression** (`amount`), not the composed expression.  The composition machinery handles the rest.  This is identical to how composition works without pivot — the pivot is applied at the same point where a plain `Aggregate` would be.

### 5.3 Column Ordering

Pivoted columns appear in the output in the following deterministic order:

1. Grain columns (from `original_state`, minus the pivot dimension), preserving their original order
2. Pivoted value columns, in the order they appear in the `values` list
3. The `residual_column` (if specified), last among the pivoted columns

This ordering is part of the contract — consumers can rely on it for `SELECT *` results, positional column references, and human readability.

### 5.4 Safety Infrastructure Compatibility

The CASE WHEN pattern generated by pivot is **already handled** by the existing explosion-safety infrastructure in the algebra.  Specifically, `_extract_aggregated_value_deps()` (added in the TPC-DS validation phase) correctly distinguishes between:

- The pivot dimension in the `CASE WHEN` condition (not an aggregated value — safe even if join-exploded)
- The measure expression in the `THEN` branch (the actual aggregated value — subject to explosion/snapshot safety checks)

This means:
- Pivoting on a join-exploded dimension (e.g., `d_day_name` from an N:1 join to `date_dim`) is safe with any aggregation function — the exploded dimension is only in the CASE condition.
- The measure column's own safety properties (`is_join_exploded`, `snapshot_dimensions`) are checked normally.
- **No special-casing** is needed in the safety validation — the existing `_extract_aggregated_value_deps` handles pivot-generated expressions natively.

This is a strength of the "syntactic sugar over CASE WHEN" design principle: the safety infrastructure was built to handle exactly this pattern, and pivot simply generates it systematically.

### 5.5 Position in the Algebra

Pivot is classified as an **LOD Change Operation** (alongside `Aggregate`, `ExtendLOD`, `AddDimensions`, `FilterToRemoveLOD`).  It reduces the grain by exactly one dimension.

In the query execution pipeline, Pivot occurs at the **same position as Aggregate** — after row-level filtering and join resolution, but before window functions and composition joins.

**Pipeline position:**

```
Base Joins → Row Filters → Pivot / Aggregate → Window Functions → Composition → Final Output
```

The planner decides whether to use `Pivot` or `Aggregate` based on whether the semantic query contains a `pivot` clause.  If pivoting is requested, the planner:

1. Ensures the pivot dimension is joined into the state
2. Applies row-level filters (including any filters on the pivot dimension's table)
3. Executes the `Pivot` operation (which includes the aggregation)
4. Proceeds with window functions / composition as usual

---

## 6. SQL Generation

The transpiler generates SQL for pivot in two modes:

### Mode 1: CASE WHEN (Universal — all databases)

```sql
SELECT s_store_name, s_store_id,
       SUM(CASE WHEN d_day_name = 'Sunday' THEN ss_sales_price ELSE NULL END) AS sun_sales,
       SUM(CASE WHEN d_day_name = 'Monday' THEN ss_sales_price ELSE NULL END) AS mon_sales,
       SUM(CASE WHEN d_day_name = 'Tuesday' THEN ss_sales_price ELSE NULL END) AS tue_sales,
       SUM(CASE WHEN d_day_name = 'Wednesday' THEN ss_sales_price ELSE NULL END) AS wed_sales,
       SUM(CASE WHEN d_day_name = 'Thursday' THEN ss_sales_price ELSE NULL END) AS thu_sales,
       SUM(CASE WHEN d_day_name = 'Friday' THEN ss_sales_price ELSE NULL END) AS fri_sales,
       SUM(CASE WHEN d_day_name = 'Saturday' THEN ss_sales_price ELSE NULL END) AS sat_sales
FROM store_sales
JOIN date_dim ON ss_sold_date_sk = d_date_sk
JOIN store ON ss_store_sk = s_store_sk
WHERE d_year = 2000 AND s_gmt_offset = -5
GROUP BY s_store_name, s_store_id
```

### Mode 2: Native PIVOT (dialect-specific optimization)

For databases that support `PIVOT` syntax (Snowflake, DuckDB, SQL Server, BigQuery), the transpiler MAY generate native PIVOT:

```sql
-- Snowflake / DuckDB native PIVOT
SELECT *
FROM (
    SELECT s_store_name, s_store_id, d_day_name, ss_sales_price
    FROM store_sales
    JOIN date_dim ON ss_sold_date_sk = d_date_sk
    JOIN store ON ss_store_sk = s_store_sk
    WHERE d_year = 2000 AND s_gmt_offset = -5
) src
PIVOT (
    SUM(ss_sales_price)
    FOR d_day_name IN ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
) AS p (s_store_name, s_store_id, sun_sales, mon_sales, tue_sales, wed_sales, thu_sales, fri_sales, sat_sales)
```

The choice between Mode 1 and Mode 2 is a transpiler optimization.  Both MUST produce identical results.  Mode 1 is the reference implementation; Mode 2 is an optional performance optimization for supported dialects.

### 6.1 Semantic SQL Syntax

Both frontend variants support pivot via the `{PIVOT ...}` property block syntax:

**Variant A — `SELECT SEMANTIC_AGG`:**

```sql
SELECT SEMANTIC_AGG
  DIMENSIONS s_store_name, s_store_id
  MEASURES
    SUM(ss_sales_price)
      {PIVOT d_day_name IN ('Sunday' AS sun_sales, 'Monday' AS mon_sales,
       'Tuesday' AS tue_sales, 'Wednesday' AS wed_sales, 'Thursday' AS thu_sales,
       'Friday' AS fri_sales, 'Saturday' AS sat_sales)}
      AS daily_sales
WHERE d_year = 2000 AND s_gmt_offset = -5
```

**Variant B — `SELECT SEMANTIC`:**

```sql
SELECT SEMANTIC
  s_store_name,
  s_store_id,
  SUM(ss_sales_price)
    {PIVOT d_day_name IN ('Sunday' AS sun_sales, 'Monday' AS mon_sales,
     'Tuesday' AS tue_sales, 'Wednesday' AS wed_sales, 'Thursday' AS thu_sales,
     'Friday' AS fri_sales, 'Saturday' AS sat_sales)}
    AS daily_sales
GROUP BY s_store_name, s_store_id
WHERE d_year = 2000 AND s_gmt_offset = -5
```

**Property block syntax for PIVOT:**

```
{PIVOT dimension IN (value1 [AS alias1], value2 [AS alias2], ...)}
```

The `PIVOT` property is parsed within the existing curly-brace `{...}` property block mechanism.  It can be combined with other properties:

```sql
SUM(amount) {GRAIN FIXED (customer_id, d_day_name), PIVOT d_day_name IN ('Mon' AS mon, 'Tue' AS tue)} AS weekly
```

**PIVOT property grammar:**

```
PIVOT <dimension> IN ( <value_item> [, <value_item> ...] )
  [RESIDUAL <identifier>]

value_item := <literal> [AS <identifier>]
```

When `AS <identifier>` is omitted from a value item, the output name is auto-generated per the shorthand rules (lowercase value, spaces → underscores, with optional `output_prefix`).

If `RESIDUAL <identifier>` is present, the named residual column is generated.

**Metric reference with model-level pivot:**

When the measure references a metric that already has a pivot definition in the model, no `{PIVOT ...}` block is needed in the SQL:

```sql
SELECT SEMANTIC_AGG
  DIMENSIONS s_store_name
  MEASURES daily_store_sales
WHERE d_year = 2000
```

The pivot spec is inherited from the `daily_store_sales` metric definition.

---

## 7. Proposed Spec Changes

### 7.1 OSI_Core_Abstractions.md

**§ Analytical Context → Properties (line ~280):**

Pivot is a per-measure property, consistent with `grain`, `filter`, and `joins`.  Add to the properties table:

| Context | Query Scope | Metric Scope |
|:---|:---|:---|
| **Pivot** | Per-measure `pivot:` on a measure request | Metric-level `pivot:` definition |

**§ Semantic Query (table at line ~252):**

No new top-level clause is needed.  Instead, add a note to the **Measures** row:

> Measures may include an optional `pivot` specification that transforms a dimension's values into separate output columns, consuming the pivot dimension from the measure's grain.  Multiple measures may each have their own independent pivot.

**§ Quick Reference → Common Patterns (line ~520):**

Add:

| Pattern | Grain Setup |
|:---|:---|
| Static pivot (rows → columns) | `pivot: { dimension: day_name, measure: revenue, values: [...] }` — consumes pivot dimension from grain |

**§ Schema Extensions → Extended Metrics Schema (line ~536):**

Add a `pivot` field:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `pivot` | object | No | Static pivot specification — transforms dimension values into columns |

**§ Schema Extensions → Pivot Schema (new section):**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `dimension` | string | Yes | Dimension field to consume |
| `values` | array | Yes | List of `{value, output_name}` pairs |

The measure expression comes from the metric's own `expression` (for model-level pivots) or from the measure request's `metric_name` / `expression` (for query-level pivots).  This is consistent with how grain and filter work — they modify the metric's behavior, they don't redefine it.

**§ Appendix A (new pattern):**

Add **Pattern 13: Static Pivot (Rows to Columns)**:

```
# Revenue by store, pivoted by day of week
query:
  dimensions: [s_store_name]
  measures:
    - metric_name: revenue
      pivot:
        dimension: d_day_name
        values:
          - { value: "Sunday",    output_name: sun_sales }
          - { value: "Monday",    output_name: mon_sales }
          - { value: "Tuesday",   output_name: tue_sales }
          - { value: "Wednesday", output_name: wed_sales }
          - { value: "Thursday",  output_name: thu_sales }
          - { value: "Friday",    output_name: fri_sales }
          - { value: "Saturday",  output_name: sat_sales }
  where: "d_year = 2000"
```

Result:

| s_store_name | sun_sales | mon_sales | tue_sales | wed_sales | thu_sales | fri_sales | sat_sales |
|:---|:---|:---|:---|:---|:---|:---|:---|
| Store A | 12,000 | 15,000 | 14,000 | 13,500 | 16,000 | 18,000 | 20,000 |
| Store B | 8,000 | 10,000 | 9,500 | 9,000 | 11,000 | 13,000 | 15,000 |

### 7.2 OSI_Calc_Model_Semantics.md

**§ Calculation Operations and Algebra → LOD Change Operations (new subsection):**

Add after `FilterToRemoveLOD`:

```
#### Pivot(original_state, pivot_dimension, values, measure_expression, agg_function, residual_column=None) → State

**Operation:**
Consumes a dimension from the grain and produces N aggregated columns — one per
static pivot value.  Semantically equivalent to an Aggregate with N conditional
CASE WHEN aggregations, but expressed as a single logical operation.  If
residual_column is set, an additional column captures unmatched rows.

**Validation:**

* pivot_dimension MUST be in original_state.grain
* pivot_dimension MUST be in original_state.columns
* measure_expression MUST reference only columns in original_state
* values MUST have at least one entry
* All output_name values MUST be unique and not collide with existing columns
* If residual_column is set, it MUST NOT collide with any output_name or existing columns
* Aggregation rules (explosion-safe, snapshot-safe) apply to the measure column

**Resulting State:**

* Grain: original_state.grain − {pivot_dimension}
* Columns:
  - All grain columns except pivot_dimension
  - N new aggregated columns, one per value entry
  - If residual_column is set, 1 additional column for unmatched rows
* Column properties (for each pivoted column, including residual):
  - is_agg: True
  - num_aggs: source_measure.num_aggs + 1
  - is_join_exploded: False (aggregation resolves)
  - is_single_valued: False
  - dependencies: {pivot_dimension} ∪ measure_dependencies

**Equivalence:**
Pivot(state, dim, values, expr, AGG, residual_column) ≡
  Aggregate(state, state.grain − {dim},
    [(v.name, "AGG(CASE WHEN dim = v.value THEN expr END)") for v in values]
    + ([(residual_column, "AGG(CASE WHEN dim NOT IN (...) OR dim IS NULL THEN expr END)")]
       if residual_column else []))
```

### 7.3 SQL_EXPRESSION_SUBSET.md

**§ Not Supported in Expressions (line ~188):**

Clarify that `PIVOT`/`UNPIVOT` SQL keywords are not part of the expression language — pivot is handled by the semantic query's `pivot` clause, not by SQL syntax in expressions:

| Construct | Reason |
|:---|:---|
| `PIVOT` / `UNPIVOT` | Pivot is a semantic query operation, not an expression construct.  Use the `pivot` clause in the semantic query or metric definition. |

**§ Conditional Aggregations (line ~358):**

Add a note:

> **Pivot patterns**: The `CASE WHEN` conditional aggregation pattern
> (`SUM(CASE WHEN dim = 'val' THEN expr END)`) is the fundamental building
> block of the `pivot` clause.  When a semantic query includes a `pivot`
> specification, the engine auto-generates these CASE WHEN aggregations.

---

## 8. Implementation Steps

### 8.1 Parsing Layer

1. **Extend `LODQuery`** (or equivalent query model) with an optional `pivot` field containing the pivot specification.
2. **Extend metric model** to support an optional `pivot` field on metric definitions.
3. **Validation**: Ensure pivot dimension is not duplicated in the dimensions list; ensure values are non-empty; ensure output names are unique.

### 8.2 Algebra Layer

1. **Add `Pivot` as a new `PlanOperation`** in the plan step enum (alongside `Aggregate`, `AddColumns`, `Filtering`, etc.).
2. **Implement the `pivot()` pure function** in the algebra module, following the validation and state-change rules defined in §5.
3. **Unit tests**: Grain removal, column generation, property inheritance, validation errors.

### 8.3 Planner Layer

1. **Detect pivot in the query** during plan generation.
2. **Route through pivot algebra** instead of generating N separate CASE WHEN measures. The planner should:
   a. Ensure the pivot dimension is joined into the state
   b. Apply row-level filters
   c. Call `Pivot(state, ...)` instead of `Aggregate(state, ...)` for the pivoted measures
   d. Continue with window functions / composition as normal
3. **Interaction with non-pivoted measures**: If the query has both pivoted and non-pivoted measures, the planner aggregates both in the same step (the pivot dimension is removed from the GROUP BY for both).

### 8.4 Transpiler Layer

1. **CASE WHEN generation**: When transpiling a `Pivot` plan step, generate the N `AGG(CASE WHEN dim = value THEN expr ELSE NULL END)` columns in the SELECT clause.
2. **GROUP BY**: Emit the grain columns *without* the pivot dimension.
3. **Optional dialect optimization**: For Snowflake/DuckDB, generate native `PIVOT` syntax when the feature flag is enabled.

### 8.5 Frontend / Semantic SQL Layer

1. **Parse `pivot` from the query input** (YAML, JSON, or programmatic API).
2. **Expand model-level pivoted metrics** when they are referenced in a query — resolve to the underlying N output columns.
3. **Column name mapping**: Ensure the output column names from the pivot spec are used in ORDER BY, HAVING, and downstream references.

### 8.6 Testing

1. **E2E validation**: Compare pivot query results against manually-written CASE WHEN queries (Q43 is the existing reference).
2. **Grain tracking**: Verify that the pivot dimension is correctly removed from the output grain.
3. **Composition**: Test pivoted columns used in subsequent window functions, HAVING filters, and LOD composition.
4. **Error cases**: Pivot dimension in dimensions list, empty values, duplicate output names, pivot on non-existent dimension.
5. **TPC-DS coverage**: Implement Q43, Q59, Q50, Q62, Q88, Q66, Q99 using the pivot syntax and validate against reference SQL.

---

## 9. Out of Scope

### 9.1 UNPIVOT (Columns to Rows) — Not Included

**UNPIVOT is excluded from this proposal** for the following reasons:

1. **No TPC-DS need**: Zero of the 99 benchmark queries require unpivoting.  The TPC-DS schema is already normalized — multi-channel data lives in separate fact tables, not wide columns.

2. **Synthetic dimension problem**: UNPIVOT creates a new dimension whose values come from *column names* (metadata), not from any physical table.  This synthetic dimension:
   - Has no dataset backing — it doesn't exist in any `fields:` definition
   - Has no relationship path — the planner cannot resolve it through the join graph
   - Cannot participate in `FIXED [dim]` grain specifications
   - Cannot be joined to anything downstream

3. **Column removal semantics**: UNPIVOT consumes N columns and replaces them with 1 value column + 1 label column.  The current algebra has no precedent for removing named columns from a state (operations only add columns or collapse them via aggregation).

4. **Type compatibility validation**: UNPIVOT requires all source columns to be type-compatible, requiring type-checking logic not present in the current expression analysis.

5. **Already covered by `source` SQL**: The `source` field on datasets already accepts SQL queries.  Pre-pivoted (wide) source data can be unpivoted at the dataset definition level:

   ```yaml
   - name: daily_store_sales
     source: >
       SELECT s_store_id, day_name, daily_sales
       FROM wide_store_report
       UNPIVOT (daily_sales FOR day_name IN (sun_sales, mon_sales, ...))
   ```

6. **Industry alignment**: No mainstream semantic layer (Tableau, Looker, Power BI/DAX, dbt/MetricFlow) implements unpivot at the semantic query layer.  All treat it as an ETL / data preparation concern.

### 9.2 Dynamic Pivot — Not Included

**Dynamic pivot (where column values are discovered from the data at runtime) is excluded** for the following reasons:

1. **TPC-DS uses only static pivot**: All 9 pivot queries use hardcoded, known-at-design-time values: days of week (always 7), hour shifts (fixed ranges), delay buckets (fixed thresholds), ship mode types (fixed names).

2. **Non-deterministic schema**: Dynamic pivot produces a variable number of output columns depending on the data.  This breaks the determinism guarantee — the same query definition could produce different column sets on different data, making downstream references, ORDER BY, and composition fragile.

3. **Two-pass execution required**: Dynamic pivot requires first querying for distinct values, then building the pivot query.  This adds latency, requires metadata caching, and introduces race conditions if the data changes between passes.

4. **No semantic layer precedent**: No mainstream semantic layer supports dynamic pivot.  Tableau, Looker, and Power BI handle dynamic pivoting at the *presentation layer* (the UI dynamically places dimension values as columns during rendering), not at the query generation layer.

5. **Industry practice**: The universal approach is for the BI *frontend* to request row-oriented data from the semantic layer and pivot dynamically during visualization.  The semantic layer's job is to produce correct, grain-safe aggregated data — the column-vs-row layout is a presentation concern.

If dynamic pivot is needed in the future, it should be implemented as a two-phase API (query for distinct values → build static pivot spec) rather than as a single-pass algebra operation.

### 9.3 Multi-Dimensional Pivot — Not Included

**Pivoting a single measure across two or more dimensions simultaneously** (e.g., day × shift = 21 columns like `sun_morning_sales`, `sun_afternoon_sales`, ...) is excluded from this proposal.

This would require:
- Cartesian product of value sets (N × M output columns)
- Compound output naming conventions
- A fundamentally different grain operation (removing 2+ dimensions in one step)

This can be approximated today by creating a synthetic combined dimension (e.g., `d_day_name || '_' || t_shift`) and pivoting on that.  A first-class multi-dimensional pivot may be considered in a future extension if demand warrants it.
