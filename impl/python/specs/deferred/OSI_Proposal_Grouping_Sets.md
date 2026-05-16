# Proposal: ROLLUP and GROUPING SETS for OSI

**Status:** Draft Proposal
**Author:** will.pugh@snowflake.com
**Date:** 2026-02-23
**Related specs:**
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md)
- [SQL Expression Subset](./SQL_EXPRESSION_SUBSET.md)
- [Pivot Operator Proposal](./OSI_Proposal_Pivot_Operator.md)

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Design Principles](#2-design-principles)
3. [Syntax](#3-syntax)
   - [Grain-Level: `grouping_sets` Property](#31-grain-level-grouping_sets-property)
   - [Grain-Level: `grouping_columns` Property](#32-grain-level-grouping_columns-property)
   - [Query-Level: `grouping_sets` and `grouping_columns`](#33-query-level-grouping_sets-and-grouping_columns)
   - [ROLLUP and CUBE Shorthands](#34-rollup-and-cube-shorthands)
4. [LOD / Grain Semantics](#4-lod--grain-semantics)
   - [Grain Rule](#41-grain-rule)
   - [CalculationState Changes](#42-calculationstate-changes)
   - [Interaction with LOD Modes](#43-interaction-with-lod-modes)
   - [Composition with Non-Grouping-Sets Metrics](#44-composition-with-non-grouping-sets-metrics)
   - [Composition of Two Grouping-Sets Branches](#45-composition-of-two-grouping-sets-branches)
   - [Re-aggregation and Grouping Sets](#46-re-aggregation-and-grouping-sets)
5. [The GROUPING() Expression Function](#5-the-grouping-expression-function)
   - [GROUPING()](#51-grouping)
   - [GROUPING_ID()](#52-grouping_id)
   - [Where GROUPING() Can Be Used](#53-where-grouping-can-be-used)
   - [Expression Examples](#54-expression-examples)
6. [Algebra Operation](#6-algebra-operation)
   - [GroupingAggregate Operation Definition](#61-groupingaggregate-operation-definition)
   - [Relationship to Aggregate](#62-relationship-to-aggregate)
   - [Column Ordering](#63-column-ordering)
   - [Safety Infrastructure Compatibility](#64-safety-infrastructure-compatibility)
   - [Position in the Algebra](#65-position-in-the-algebra)
7. [SQL Generation](#7-sql-generation)
   - [Semantic SQL Syntax](#71-semantic-sql-syntax)
8. [Proposed Spec Changes](#8-proposed-spec-changes)
   - [OSI_Core_Abstractions.md](#81-osi_core_abstractionsmd)
   - [OSI_Calc_Model_Semantics.md](#82-osi_calc_model_semanticsmd)
   - [SQL_EXPRESSION_SUBSET.md](#83-sql_expression_subsetmd)
9. [Implementation Steps](#9-implementation-steps)
10. [Out of Scope](#10-out-of-scope)

---

## 1. Motivation

Several TPC-DS queries require producing **multiple aggregation levels in a single result set** — detail rows alongside subtotals and grand totals.  At least 11 of the 99 benchmark queries use this pattern:

| Query | ROLLUP Dimensions | Pattern |
|:---|:---|:---|
| Q5, Q77, Q80 | `channel` | Channel profitability with subtotals |
| Q14a, Q14b | `channel, i_brand_id` | Cross-channel with ROLLUP |
| Q18 | `i_item_id, ca_country, ca_state, ca_county` | Catalog sales with geographic ROLLUP |
| Q22 | `i_product_name, i_brand, i_class, i_category` | Inventory with product hierarchy ROLLUP |
| Q27 | `i_item_id, s_state` | Store sales with ROLLUP by item/state |
| Q36, Q86 | `i_category, i_class` | Revenue with ROLLUP + RANK |
| Q67 | `i_category, i_class, i_brand, i_product_name, d_year, d_qoy, d_moy, s_store_id` | Store sales ROLLUP + window functions |
| Q70 | `s_state, s_county` | Store sales ROLLUP on state + RANK |

Today, OSI has no concept of ROLLUP or GROUPING SETS.  The documented workaround is to execute separate queries at each aggregation level, or handle at the presentation layer.  This is the **last remaining true spec gap** from the TPC-DS analysis.

ROLLUP / GROUPING SETS would:

1. **Close the final TPC-DS gap**: All ~11 ROLLUP queries become expressible
2. **Enable subtotal/grand-total reports**: A single query produces detail + summary rows
3. **Generate optimal SQL**: The transpiler emits native `GROUP BY ROLLUP(...)` or `GROUP BY GROUPING SETS(...)` — far more efficient than N separate queries UNION'd
4. **Align with the grain model**: `grouping_sets` is a natural extension of the grain specification — it describes "at what set of grains should this computation occur"

---

## 2. Design Principles

1. **Grain property, not dimension property**: `grouping_sets` lives on the grain specification (metric or query level), consistent with OSI's principle that analytical context properties belong to fields/metrics, not to dimensions.
2. **Extends the grain, doesn't replace it**: `grouping_sets` is an orthogonal modifier on the grain — the base grain mode (QUERY, FIXED, INCLUDE) still determines the finest aggregation level, and `grouping_sets` adds coarser levels.
3. **Preserves row uniqueness**: Auto-generated `GROUPING()` columns become part of the effective grain, ensuring the CalculationState invariant (grain uniquely identifies rows) is preserved.
4. **GROUPING() as an expression function**: `GROUPING(dim)` is available wherever post-aggregation expressions are valid, not limited to a declarative property.
5. **ROLLUP as shorthand**: `ROLLUP` and `CUBE` are syntactic sugar for common grouping set patterns, not separate concepts.

---

## 3. Syntax

### 3.1 Grain-Level: `grouping_sets` Property

The `grouping_sets` property is added to the grain specification on a metric (or measure request).  It defines which combinations of the grain's dimensions should be aggregated:

```yaml
metrics:
  - name: revenue_with_subtotals
    expression: SUM(ss_ext_sales_price)
    grain:
      mode: FIXED
      dimensions: [i_item_id, s_state]
      grouping_sets:
        - [i_item_id, s_state]    # detail rows
        - [i_item_id]             # item subtotals (state rolled up)
        - []                      # grand total (all rolled up)
    grouping_columns:
      s_state: g_state            # auto-add GROUPING(s_state) as "g_state"
```

**Grain `grouping_sets` schema:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `grouping_sets` | array of arrays, or string | No | Explicit list of dimension subsets to aggregate to, OR a shorthand string (`ROLLUP`, `CUBE`).  Each inner array is a subset of the grain's `dimensions`.  If not set, standard single-level aggregation (current behavior). |

**Rules:**

1. Every dimension list in `grouping_sets` MUST be a subset of the grain's `dimensions`.
2. The grain's `dimensions` list SHOULD appear as one of the grouping sets (the detail level).  If omitted, the detail level is not produced — only subtotals/totals.
3. `grouping_sets` is orthogonal to `mode` — it works with QUERY, FIXED, INCLUDE.  It does NOT work with EXCLUDE (the dimensions have already been removed) or TABLE (TABLE grain is for scalars).

### 3.2 Grain-Level: `grouping_columns` Property

The `grouping_columns` property requests `GROUPING()` indicator columns in the metric's output.  These columns distinguish "real NULL" from "subtotal NULL":

```yaml
grain:
  mode: FIXED
  dimensions: [i_item_id, s_state]
  grouping_sets: ROLLUP
  grouping_columns:
    i_item_id: g_item       # GROUPING(i_item_id) → column named "g_item"
    s_state: g_state         # GROUPING(s_state) → column named "g_state"
```

**Grain `grouping_columns` schema:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `grouping_columns` | boolean or object | No | If `true`, auto-generates `GROUPING(dim)` columns for every dimension in the grain, using default names (`__grouping_<dim>`).  If an object, maps dimension names to output column names.  Only valid when `grouping_sets` is set. |

**Rules:**

1. Each key in the `grouping_columns` object MUST be a dimension present in the grain's `dimensions`.
2. Each value MUST be a unique column name, not colliding with other columns in the state.
3. When `grouping_columns: true`, the default output name is `__grouping_<dim_name>__`.
4. GROUPING columns become part of the **effective grain** for row uniqueness (see [§4.1](#41-grain-rule)).

### 3.3 Query-Level: `grouping_sets` and `grouping_columns`

When grouping sets apply to the entire query (all metrics participate), the properties can be set at the query level:

```yaml
query:
  dataset_name: store_sales
  dimensions: [i_item_id, s_state]
  grouping_sets: ROLLUP
  grouping_columns:
    s_state: g_state
  measures:
    - { output_name: agg1, metric_name: ss_avg_quantity }
    - { output_name: agg2, metric_name: ss_avg_list_price }
    - { output_name: agg3, metric_name: ss_avg_coupon_amt }
  where: "cd_gender = 'M' AND cd_marital_status = 'S' AND cd_education_status = 'College' AND d_year = 2002"
  order_by:
    - { name: i_item_id }
    - { name: s_state }
```

**Query-level schema:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `grouping_sets` | array of arrays, or string | No | Applied to the query's GROUP BY.  Affects all metrics in the query.  Same syntax as grain-level `grouping_sets`. |
| `grouping_columns` | boolean or object | No | Requests GROUPING() columns in the query output.  Same syntax as grain-level `grouping_columns`. |

**Interaction between query-level and grain-level:**

| Metric has `grouping_sets`? | Query has `grouping_sets`? | Behavior |
|:---|:---|:---|
| No | No | Standard aggregation (current behavior) |
| No | Yes | Query-level grouping sets applied to all metrics |
| Yes | No | Metric's grouping sets applied to its own branch |
| Yes | Yes | Metric's grain-level `grouping_sets` takes precedence for that metric's branch.  Query-level applies to other metrics. |

### 3.4 ROLLUP and CUBE Shorthands

For common patterns, string shorthands are supported in place of explicit grouping set lists:

**ROLLUP** — progressive removal from right to left:

```yaml
# These are equivalent:
grouping_sets: ROLLUP
grouping_sets:
  - [i_item_id, s_state]     # detail
  - [i_item_id]              # item subtotal
  - []                       # grand total

# For dimensions [a, b, c], ROLLUP expands to:
# [[a,b,c], [a,b], [a], []]
```

**CUBE** — all 2^N combinations:

```yaml
# These are equivalent:
grouping_sets: CUBE
grouping_sets:
  - [i_item_id, s_state]
  - [i_item_id]
  - [s_state]
  - []

# For dimensions [a, b, c], CUBE expands to:
# [[a,b,c], [a,b], [a,c], [b,c], [a], [b], [c], []]
```

**Partial ROLLUP** — only some dimensions participate:

```yaml
# GROUP BY i_item_id, ROLLUP(s_state)
# i_item_id is always present; s_state is rolled up
dimensions: [i_item_id, s_state]
grouping_sets:
  - [i_item_id, s_state]     # detail
  - [i_item_id]              # item subtotal (state rolled up)
# Note: no [] entry — grand total not included
```

This explicit form is more flexible than the ROLLUP/CUBE shorthands and handles the common `GROUP BY a, ROLLUP(b, c)` SQL pattern.

---

## 4. LOD / Grain Semantics

### 4.1 Grain Rule

**Grouping sets do NOT change the declared grain.  They add rows at coarser aggregation levels.  The effective grain is widened to include GROUPING() indicator columns for row uniqueness.**

Formally:

```
declared_grain = {dimensions from grain spec}
effective_grain = declared_grain ∪ {GROUPING(d) for d in declared_grain}
```

Example — `GROUP BY ROLLUP(i_item_id, s_state)`:

| i_item_id | s_state | GROUPING(i_item_id) | GROUPING(s_state) | Level |
|:---|:---|:---|:---|:---|
| AAA | TN | 0 | 0 | Detail |
| AAA | CA | 0 | 0 | Detail |
| AAA | NULL | 0 | 1 | Item subtotal |
| BBB | TN | 0 | 0 | Detail |
| BBB | NULL | 0 | 1 | Item subtotal |
| NULL | NULL | 1 | 1 | Grand total |

Without GROUPING columns, the grain `{i_item_id, s_state}` is NOT unique — a subtotal row `(AAA, NULL)` would collide with a detail row where `s_state` is genuinely NULL.  The GROUPING columns disambiguate.

**Why this is the right model:** The grain tracks row uniqueness.  GROUPING columns are the minimal addition needed to preserve this invariant.  The declared grain (the user-visible dimensions) remains unchanged — the GROUPING columns are metadata about *which aggregation level* a row belongs to.

**Structural NULLs in dimension columns:**

After a GroupingAggregate, the declared grain columns are **nullable by rollup**.  In subtotal and total rows, rolled-up dimension columns contain NULL — not because the data is NULL, but because the dimension was aggregated away.  Downstream operations must be aware of this:

| Context | Behavior |
|:---|:---|
| `add_columns("UPPER(s_state)")` | Returns NULL for subtotal rows where `GROUPING(s_state) = 1`.  This is correct SQL behavior. |
| `filtering("s_state = 'TN'")` | Excludes subtotal/total rows (NULL ≠ 'TN').  Use `GROUPING(s_state) = 0 AND s_state = 'TN'` for explicit intent. |
| Composition join on `s_state` | Subtotal rows (s_state = NULL) will NOT match detail rows from a non-rollup branch.  See [§4.4](#44-composition-with-non-grouping-sets-metrics). |

The dimension columns are semantically valid only when `GROUPING(dim) = 0`.  When `GROUPING(dim) = 1`, the column value is structurally NULL (meaning "all values of this dimension").

**Exiting the grouping state:**

To return to a "normal" (non-rollup) state, a user can:

1. **Filter to detail rows:** `HAVING GROUPING(dim) = 0` for all dims → all GROUPING columns become constant 0.
2. **Project away GROUPING columns:** After filtering, the GROUPING columns are constant and can be removed via `project()`.  The grain narrows back to the declared dimensions.

This two-step pattern (filter + project) is the canonical way to "exit" the grouping state when only detail rows are needed downstream.

### 4.2 CalculationState Changes

After a `GroupingAggregate` operation, the resulting `CalculationState` has:

| Property | Value |
|:---|:---|
| **grain** | `declared_grain ∪ {grouping_column_names}` |
| **columns** | Declared grain columns + GROUPING columns + aggregated measure columns |

**GROUPING column properties:**

| Property | Value |
|:---|:---|
| `is_agg` | `True` (it's computed by the aggregation step) |
| `num_aggs` | 1 |
| `is_join_exploded` | `False` |
| `is_single_valued` | `False` |
| `dependencies` | `{the dimension it's a grouping indicator for}` |

The GROUPING columns have integer values: `0` = the dimension is at detail level in this row; `1` = the dimension is rolled up (aggregated away) in this row.

### 4.3 Interaction with LOD Modes

`grouping_sets` is an orthogonal modifier on the grain mode.  It works with:

| Grain Mode | `grouping_sets` | Effective Behavior |
|:---|:---|:---|
| **QUERY** | `ROLLUP` | `GROUP BY ROLLUP(query_dims)` — subtotals of the query dimensions |
| **FIXED [dims]** | `ROLLUP` | `GROUP BY ROLLUP(fixed_dims)` — subtotals of the fixed dimensions |
| **FIXED [dims]** | explicit sets | `GROUP BY GROUPING SETS(...)` at the specified combinations of fixed dims |
| **INCLUDE [dims]** | `ROLLUP` | `GROUP BY ROLLUP(query_dims ∪ include_dims)` — the INCLUDE dimensions participate in the rollup |
| **EXCLUDE** | ❌ | Error — EXCLUDE removes dimensions; there's nothing to roll up |
| **TABLE** | ❌ | Error — TABLE grain is for scalars, not aggregations |

### 4.4 Composition with Non-Grouping-Sets Metrics

When a query mixes grouping-sets metrics with non-grouping-sets metrics:

```yaml
measures:
  - { metric_name: revenue_with_subtotals }   # has grouping_sets: ROLLUP
  - { metric_name: customer_count }            # standard QUERY grain
```

**Behavior:** Each metric is its own branch (per the standard LOD composition model).

- The rollup branch produces rows at multiple levels: `{item, state}`, `{item}`, `{}`
- The non-rollup branch produces rows at one level: `{item, state}`
- Composition: FULL OUTER JOIN on the **declared dimensions** (NOT the effective grain)

**Composition join mechanics:**

The composition join uses only the declared grain dimensions (`i_item_id`, `s_state`), NOT the GROUPING indicator columns.  This means:

- **Detail rows** (GROUPING = 0 for all dims): Both sides have real dimension values → the join matches normally.  Both `revenue` and `customer_count` are populated.
- **Subtotal rows** (GROUPING = 1 for some dims): The rolled-up dimension is NULL on the ROLLUP side.  The non-ROLLUP side has no row with NULL for that dimension → no match.  The non-ROLLUP metric is NULL for these rows.
- **Grand total row** (GROUPING = 1 for all dims): All dimensions are NULL → no match with any non-ROLLUP row.  Non-ROLLUP metrics are NULL.

Result:

| i_item_id | s_state | g_state | revenue | customer_count |
|:---|:---|:---|:---|:---|
| AAA | TN | 0 | 500 | 12 |
| AAA | CA | 0 | 300 | 8 |
| AAA | NULL | 1 | 800 | NULL |
| NULL | NULL | 1 | 2000 | NULL |

The non-rollup metric has NULL for subtotal/total rows — it was only computed at the detail level.  This is the natural, correct behavior of composition.

**Planner warning:** The planner SHOULD emit a **W5004** informational warning when composing grouping-sets and non-grouping-sets branches, as the NULL-filled subtotal rows may surprise users unfamiliar with the composition model.

**Planner optimization:** When both metrics share the same base table, filters, and join paths, the planner MAY merge them into the same `GROUP BY ROLLUP(...)` step — which gives `customer_count` at every level:

| i_item_id | s_state | g_state | revenue | customer_count |
|:---|:---|:---|:---|:---|
| AAA | TN | 0 | 500 | 12 |
| AAA | NULL | 1 | 800 | 20 |
| NULL | NULL | 1 | 2000 | 50 |

This optimization is valid because SQL's `GROUP BY ROLLUP(...)` computes ALL aggregates at every level.  The planner decides based on branch compatibility.

### 4.5 Composition of Two Grouping-Sets Branches

When two independent metrics both have `grouping_sets` and are composed in the same query:

**Same rollup dimensions — works naturally:**

```yaml
measures:
  - metric_name: revenue     # ROLLUP(category, product)
  - metric_name: quantity    # ROLLUP(category, product)
```

Both branches produce the same set of grouping levels.  The composition join matches rows at every level because both sides have the same GROUPING column values.  GROUPING columns from both branches agree.

**Different rollup dimensions — restricted:**

```yaml
measures:
  - metric_name: revenue     # ROLLUP(category, product)
  - metric_name: quantity    # ROLLUP(category, region)
```

These branches produce DIFFERENT aggregation-level combinations.  Both generate a `__grouping_category__` column, but the rollup levels are different: Branch A has `{category, product}`, `{category}`, `{}` while Branch B has `{category, region}`, `{category}`, `{}`.

**This is problematic:**

- The `__grouping_category__` columns have the same name but are computed independently in different branches.  At the `{category}` subtotal level, both agree (both = 0), but the companion columns differ.
- The composition join on declared dimensions produces a **partial cross-product** at subtotal levels — a `{category}` subtotal from Branch A has `product = NULL`, which doesn't match any specific product subtotal from Branch B.

**Rule:** Two grouping-sets branches in the same query MUST have **compatible grouping sets** — the rollup must apply to the same set of dimensions.  If the grouping sets differ, the planner raises a validation error:

> E4006: Cannot compose metrics with different grouping_sets dimensions.  'revenue' uses ROLLUP(category, product) but 'quantity' uses ROLLUP(category, region).  All grouping-sets metrics in the same query must roll up the same dimensions.

If the user needs different rollups for different metrics, they should execute separate queries.

### 4.6 Re-aggregation and Grouping Sets

When a metric with `grouping_sets` is at a grain **finer** than the query grain (e.g., INCLUDE adds extra dimensions), the planner's re-aggregation step must interact with grouping sets correctly.

**Rule:** Grouping sets apply at the **final aggregation step** — the one that produces the query-grain output.  If the metric requires an inner aggregation at a finer grain followed by re-aggregation to the query grain, the grouping sets are applied only to the re-aggregation step:

```
Inner aggregation (no grouping sets) → Re-aggregation with ROLLUP
```

This ensures that the rollup produces subtotals of the query-grain result, not subtotals of the finer-grain intermediate.  The inner aggregation is a standard `Aggregate`; only the outer re-aggregation becomes a `GroupingAggregate`.

---

## 5. The GROUPING() Expression Function

### 5.1 GROUPING()

```sql
GROUPING(dimension_name) → INTEGER (0 or 1)
```

Returns `0` if the dimension is at detail level in the current row, `1` if the dimension was rolled up (aggregated away).

### 5.2 GROUPING_ID()

```sql
GROUPING_ID(dim1, dim2, ...) → INTEGER (bitmask)
```

Returns a bitmask where each bit corresponds to a dimension: `1` = rolled up, `0` = detail.  The first argument is the most significant bit.

Example: `GROUPING_ID(i_item_id, s_state)` returns:
- `0` (binary `00`) for detail rows
- `1` (binary `01`) for item subtotals (s_state rolled up)
- `3` (binary `11`) for grand total (both rolled up)

**CalculationState properties for GROUPING_ID columns:**

When `GROUPING_ID()` is used as a measure expression, the resulting column has the same properties as individual `GROUPING()` columns:

| Property | Value |
|:---|:---|
| `is_agg` | `True` (computed by the GROUP BY step) |
| `num_aggs` | 1 |
| `is_join_exploded` | `False` |
| `is_single_valued` | `False` |
| `dependencies` | `{dim1, dim2, ...}` (all listed dimensions) |

The expression analyzer classifies `GROUPING_ID()` as AGGREGATE_LEVEL, identical to `GROUPING()`.

### 5.3 Where GROUPING() Can Be Used

`GROUPING()` is a **post-aggregation** function — it's computed as part of the GROUP BY and is available wherever aggregated values are available:

| Context | Allowed? | Rationale |
|:---|:---|:---|
| **`grouping_columns` property** (grain or query) | ✅ | Declarative — the primary way to request GROUPING columns in the output. |
| **Ad-hoc measure expression** | ✅ | `{ output_name: g_state, expression: "GROUPING(s_state)" }` — treated as AGGREGATE_LEVEL by the expression classifier. |
| **HAVING / aggregate filter** | ✅ | `HAVING GROUPING(s_state) = 0` — filter to detail rows only. |
| **ORDER BY** | ✅ | `ORDER BY GROUPING(s_state), s_state` — subtotals sort after detail. |
| **Window function** | ✅ | `RANK() OVER (PARTITION BY GROUPING(s_state) ORDER BY revenue DESC)` — ranking within each level. |
| **Scalar CASE WHEN** (post-agg) | ✅ | See [§5.4 Expression Examples](#54-expression-examples). |
| **WHERE (pre-aggregation)** | ❌ | GROUPING() doesn't exist before the GROUP BY. |
| **Metric `expression`** | ❌ | A metric's expression is the aggregation itself — GROUPING() is a side-effect of the aggregation, not an input to it. |

**Validation:** The expression classifier MUST check that `GROUPING(dim)` references a dimension that is part of an active `grouping_sets`.  If no `grouping_sets` is active, `GROUPING()` is a validation error.

### 5.4 Expression Examples

**Level label column:**

```yaml
measures:
  - output_name: level_label
    expression: >
      CASE WHEN GROUPING(s_state) = 1 AND GROUPING(i_item_id) = 1 THEN 'Grand Total'
           WHEN GROUPING(s_state) = 1 THEN 'Item Subtotal'
           ELSE 'Detail'
      END
```

**Filter to subtotals only:**

```yaml
where: "GROUPING(s_state) = 1"
# This is classified as AGGREGATE_LEVEL (HAVING) by the filter classifier,
# since GROUPING() is post-aggregation.
```

**Ordering: detail first, then subtotals:**

```yaml
order_by:
  - { name: "GROUPING(s_state)", direction: ASC }   # 0 (detail) before 1 (subtotal)
  - { name: s_state, direction: ASC }
```

---

## 6. Algebra Operation

### 6.1 GroupingAggregate Operation Definition

#### GroupingAggregate(original_state, new_grain, new_aggs, grouping_sets, grouping_columns=None) → State

**Operation:**
A variant of `Aggregate` that produces rows at multiple aggregation levels within a single step.  Semantically equivalent to a UNION ALL of separate `Aggregate` operations at each grouping set level, but executed as a single `GROUP BY GROUPING SETS(...)` or `GROUP BY ROLLUP(...)`.

**Parameters:**

| Parameter | Type | Description |
|:---|:---|:---|
| `original_state` | CalculationState | The input state. |
| `new_grain` | frozenset[str] | The finest grain (declared grain).  Must be a subset of `original_state` column names. |
| `new_aggs` | list[(name, expression)] | The aggregation columns, same as `Aggregate`. |
| `grouping_sets` | list[frozenset[str]] | The set of dimension subsets to aggregate to.  Each must be a subset of `new_grain`. |
| `grouping_columns` | dict[str, str] or None | Optional mapping from dimension name → output column name for GROUPING() indicators.  If None, GROUPING columns are still generated with default names (needed for grain uniqueness). |

**Validation:**

1. All validation rules from `Aggregate` apply (column existence, aggregation safety, etc.).
2. Each grouping set in `grouping_sets` MUST be a subset of `new_grain`.
3. `grouping_sets` MUST have at least one entry.
4. If `grouping_columns` is provided, each key MUST be a dimension in `new_grain`.
5. All output names (from `grouping_columns` values) MUST be unique and not collide with existing columns.

**Resulting State:**

- **Grain**: `new_grain ∪ {grouping_column_names}` — the declared grain widened with GROUPING indicator columns.
- **Columns**:
  - All declared grain columns (from `new_grain`)
  - GROUPING indicator columns — one per dimension in `new_grain` (either from `grouping_columns` mapping or auto-generated as `__grouping_<dim>__`)
  - Aggregated measure columns (from `new_aggs`)
- **Column properties**: Same as `Aggregate` for the measure columns.  GROUPING columns have `is_agg: True`, `num_aggs: 1`, `is_join_exploded: False`.
- **expression_ids**: Preserved from `original_state`.

**Equivalence:**

`GroupingAggregate(state, grain, aggs, grouping_sets, ...)` is semantically equivalent to:

```
UNION ALL of:
  for gs in grouping_sets:
    Aggregate(state, gs, aggs)
    + AddColumns(GROUPING(d) = 0 if d in gs else 1, for d in grain)
```

This equivalence guarantees correctness.  The transpiler MAY generate the more efficient `GROUP BY GROUPING SETS(...)` SQL instead of a literal UNION ALL.

### 6.2 Relationship to Aggregate

`GroupingAggregate` is a **strict superset** of `Aggregate`:

```
Aggregate(state, grain, aggs)
  ≡ GroupingAggregate(state, grain, aggs, grouping_sets=[grain])
```

A single-element `grouping_sets` containing all the grain dimensions is equivalent to a standard aggregation.  The GROUPING columns would all be 0 for every row.

This means the implementation can optionally unify `Aggregate` and `GroupingAggregate` into a single operation with an optional `grouping_sets` parameter, defaulting to `[grain]`.

**Composition via `enrich`, not `merge`:**

The existing `merge()` algebra operation requires **identical grains** on both sides.  A GroupingAggregate branch has `effective_grain = declared_grain ∪ GROUPING_columns`, which differs from a non-grouping branch's grain (`declared_grain` only).  Therefore, `merge()` is NOT the composition path for mixed grouping/non-grouping branches.

Instead, the planner uses the existing LOD composition mechanism: FULL OUTER JOIN via `enrich` on the shared declared dimensions.  This is the same path used when branches have different grains (e.g., FIXED coarser-than-query).  The GROUPING columns from the rollup branch are carried through as additional columns, not as join keys.

### 6.3 Column Ordering

Output columns appear in the following deterministic order:

1. Declared grain columns, in the order specified in `new_grain`
2. GROUPING indicator columns, in the same order as their corresponding dimensions
3. Aggregated measure columns, in the order specified in `new_aggs`

### 6.4 Safety Infrastructure Compatibility

All aggregation safety rules from the existing algebra apply:

- **Explosion safety**: If a measure column has `is_join_exploded: True`, only explosion-safe aggregations are allowed — same as `Aggregate`.
- **Snapshot safety**: If snapshot dimensions are involved, snapshot-safe aggregation rules apply — same as `Aggregate`.
- **GROUPING columns themselves**: These are safe by construction — they're metadata produced by the GROUP BY, not user-defined aggregations on data columns.

**Auto-generated GROUPING column name collision:**

When `grouping_columns` is `true` (or omitted, triggering auto-generation for grain uniqueness), the auto-generated names `__grouping_<dim>__` MUST be validated against all existing column names in the state AND all output names from `new_aggs`.  If a collision is detected, the planner MUST raise a clear error suggesting the user provide explicit `grouping_columns` names.

### 6.5 Position in the Algebra

`GroupingAggregate` is classified as an **LOD Change Operation**, alongside `Aggregate`, `Pivot`, `ExtendLOD`, etc.  It replaces `Aggregate` in the pipeline when grouping sets are requested.

**Pipeline position:**

```
Base Joins → Row Filters → GroupingAggregate / Aggregate / Pivot → Window Functions → Composition → Final Output
```

The planner decides which operation to use based on the metric's grain spec:
- No `grouping_sets`, no `pivot` → `Aggregate`
- `grouping_sets` present → `GroupingAggregate`
- `pivot` present → `Pivot`
- Both `grouping_sets` and `pivot` → Error (mutually exclusive — you cannot simultaneously roll up and consume a dimension)

---

## 7. SQL Generation

The transpiler generates SQL for grouping sets using native syntax:

**ROLLUP:**

```sql
SELECT i_item_id, s_state,
       GROUPING(s_state) AS g_state,
       AVG(ss_quantity) AS agg1,
       AVG(ss_list_price) AS agg2,
       AVG(ss_coupon_amt) AS agg3
FROM store_sales
JOIN date_dim ON ss_sold_date_sk = d_date_sk
JOIN item ON ss_item_sk = i_item_sk
JOIN store ON ss_store_sk = s_store_sk
JOIN customer_demographics ON ss_cdemo_sk = cd_demo_sk
WHERE cd_gender = 'M' AND cd_marital_status = 'S'
  AND cd_education_status = 'College' AND d_year = 2002
GROUP BY ROLLUP(i_item_id, s_state)
ORDER BY i_item_id, s_state
LIMIT 100
```

**Explicit GROUPING SETS:**

```sql
SELECT i_item_id, s_state,
       GROUPING(i_item_id) AS g_item,
       GROUPING(s_state) AS g_state,
       SUM(ss_ext_sales_price) AS total_sales
FROM ...
GROUP BY GROUPING SETS (
    (i_item_id, s_state),
    (i_item_id),
    ()
)
```

**Partial ROLLUP** (`GROUP BY a, ROLLUP(b, c)`):

When the grouping sets don't include the empty set `[]` but always include certain dimensions, the transpiler can detect the partial pattern:

```sql
-- grouping_sets: [[item, state], [item]]
-- item is always present → GROUP BY i_item_id, ROLLUP(s_state)
GROUP BY i_item_id, ROLLUP(s_state)
```

This optimization is equivalent to the explicit `GROUPING SETS` form but more concise.

**Partial ROLLUP decomposition algorithm:**

The transpiler determines anchor dimensions (always present) vs. rolled-up dimensions:

```
anchor_dims = intersection of all grouping sets
rollup_dims = declared_grain − anchor_dims
```

If the remaining grouping sets (after removing anchor_dims from each) form a ROLLUP pattern (progressive right-to-left removal), emit `GROUP BY anchor_dims, ROLLUP(rollup_dims)`.  Otherwise, emit `GROUP BY GROUPING SETS(...)`.

**Detection rule for ROLLUP pattern:** Given sets S₁ ⊃ S₂ ⊃ ... ⊃ Sₙ where each Sᵢ₊₁ = Sᵢ − {rightmost element}, the sets form a ROLLUP of the ordered dimensions.  If the sets don't follow this progressive pattern, fall back to explicit GROUPING SETS.

**Fallback (UNION ALL):**

For databases that don't support `GROUPING SETS` or `ROLLUP`, the transpiler generates a UNION ALL of separate queries:

```sql
SELECT i_item_id, s_state, 0 AS g_item, 0 AS g_state, SUM(sales) AS total_sales
FROM ... GROUP BY i_item_id, s_state
UNION ALL
SELECT i_item_id, NULL, 0 AS g_item, 1 AS g_state, SUM(sales)
FROM ... GROUP BY i_item_id
UNION ALL
SELECT NULL, NULL, 1 AS g_item, 1 AS g_state, SUM(sales)
FROM ...
```

The UNION ALL form is the reference semantics.  Native ROLLUP/GROUPING SETS is the optimization.

### 7.1 Semantic SQL Syntax

**Variant A — `SELECT SEMANTIC_AGG`:**

```sql
SELECT SEMANTIC_AGG
  DIMENSIONS i_item_id, s_state
  MEASURES
    AVG(ss_quantity) AS agg1,
    AVG(ss_list_price) AS agg2,
    GROUPING(s_state) AS g_state
  {GROUPING_SETS ROLLUP}
WHERE cd_gender = 'M' AND d_year = 2002
ORDER BY i_item_id, s_state
LIMIT 100
```

**Variant B — `SELECT SEMANTIC`:**

Variant B supports **two alternative syntaxes** for grouping sets — native SQL and property block:

*Native SQL syntax (preferred for SQL-savvy users):*

```sql
SELECT SEMANTIC
  i_item_id,
  s_state,
  AVG(ss_quantity) AS agg1,
  AVG(ss_list_price) AS agg2,
  GROUPING(s_state) AS g_state
GROUP BY ROLLUP(i_item_id, s_state)
WHERE cd_gender = 'M' AND d_year = 2002
```

The `parse_semantic_select` parser recognizes `GROUP BY ROLLUP(...)`, `GROUP BY CUBE(...)`, and `GROUP BY GROUPING SETS(...)` via sqlglot's native AST support.  The grouped dimensions define both the declared grain and the grouping sets.

```sql
-- Partial ROLLUP:
GROUP BY i_item_id, ROLLUP(s_state)

-- Explicit GROUPING SETS:
GROUP BY GROUPING SETS ((i_item_id, s_state), (i_item_id), ())
```

*Property block syntax (for Variant B and Variant A):*

```sql
SELECT SEMANTIC
  i_item_id,
  s_state,
  AVG(ss_quantity) AS agg1,
  GROUPING(s_state) AS g_state
GROUP BY i_item_id, s_state
  {GROUPING_SETS ROLLUP}
WHERE cd_gender = 'M' AND d_year = 2002
```

**Property block syntax:**

```
{GROUPING_SETS ROLLUP}
{GROUPING_SETS CUBE}
{GROUPING_SETS ((a, b), (a), ())}
```

The `GROUPING_SETS` property is parsed within the existing curly-brace `{...}` property block mechanism.  `GROUPING()` and `GROUPING_ID()` are used directly in the measure list as expression functions.

---

## 8. Proposed Spec Changes

### 8.1 OSI_Core_Abstractions.md

**§ Grain (Level of Detail) (line ~299):**

Add `grouping_sets` and `grouping_columns` to the grain property description:

> **Grouping Sets**: An optional modifier on any grain mode (except EXCLUDE and TABLE) that causes the aggregation to produce rows at multiple levels — the declared grain plus progressively coarser sub-grains.  Uses the SQL `GROUP BY GROUPING SETS(...)` or `GROUP BY ROLLUP(...)` mechanism.
>
> **Grouping Columns**: When grouping sets are active, GROUPING() indicator columns can be requested.  These columns return 0 (detail level) or 1 (rolled-up level) for each dimension, disambiguating real NULLs from subtotal NULLs.

**§ Grain Modes at a Glance (line ~498):**

Add note:

> Any grain mode (except EXCLUDE and TABLE) may include `grouping_sets` to produce multi-level aggregation output.  The effective grain includes GROUPING() indicator columns for row uniqueness.

**§ Quick Reference → Common Patterns (line ~520):**

Add:

| Pattern | Grain Setup |
|:---|:---|
| Subtotals + grand total | `grouping_sets: ROLLUP` on the grain — produces detail + subtotal + total rows |
| Geographic hierarchy rollup | `FIXED [country, state, city]` with `grouping_sets: ROLLUP` |
| Custom aggregation levels | `grouping_sets: [[a,b], [a], []]` — explicit control over which levels |

**§ Schema Extensions → Extended Metrics Schema (line ~536):**

The `grain` object gains two new optional fields:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `grouping_sets` | array of arrays or string | No | Dimension subsets to aggregate to, or `ROLLUP`/`CUBE` shorthand |
| `grouping_columns` | boolean or object | No | Request GROUPING() indicator columns in the output |

**§ Edge Cases and Validation Rules (line ~816):**

Add:

| Condition | Handling |
|:---|:---|
| `grouping_sets` with `EXCLUDE` grain mode | Validation error — EXCLUDE removes dimensions, nothing to roll up |
| `grouping_sets` with `TABLE` grain mode | Validation error — TABLE is for scalars |
| `GROUPING()` without active `grouping_sets` | Validation error — GROUPING() only valid with grouping sets |
| `grouping_sets` combined with `pivot` | Validation error — mutually exclusive on the same metric |

**§ Appendix A (new pattern):**

Add **Pattern 14: Multi-Level Aggregation (ROLLUP)**:

```
metrics:
  - name: revenue
    expression: SUM(orders.amount)

query:
  dimensions: [products.category, products.subcategory]
  grouping_sets: ROLLUP
  grouping_columns:
    products.category: g_category
    products.subcategory: g_subcategory
  measures: [revenue]
```

Result:

| category | subcategory | g_category | g_subcategory | revenue |
|:---|:---|:---|:---|:---|
| Electronics | Phones | 0 | 0 | 200,000 |
| Electronics | Laptops | 0 | 0 | 250,000 |
| Electronics | NULL | 0 | 1 | 450,000 |
| Furniture | Chairs | 0 | 0 | 80,000 |
| Furniture | Tables | 0 | 0 | 120,000 |
| Furniture | NULL | 0 | 1 | 200,000 |
| NULL | NULL | 1 | 1 | 650,000 |

### 8.2 OSI_Calc_Model_Semantics.md

**§ Calculation Operations and Algebra → LOD Change Operations (new subsection):**

Add after `Pivot` (or after `FilterToRemoveLOD` if pivot is not yet added):

```
#### GroupingAggregate(original_state, new_grain, new_aggs, grouping_sets, grouping_columns=None) → State

**Operation:**
A variant of Aggregate that produces rows at multiple aggregation levels.
Semantically equivalent to UNION ALL of Aggregate at each grouping set level,
with GROUPING() indicator columns added for row uniqueness.

**Validation:**

* All Aggregate validation rules apply
* Each grouping set MUST be a subset of new_grain
* grouping_sets MUST have at least one entry
* If grouping_columns provided, each key MUST be in new_grain

**Resulting State:**

* Grain: new_grain ∪ {grouping_column_names}
* Columns:
  - Declared grain columns
  - GROUPING indicator columns (is_agg: True, num_aggs: 1)
  - Aggregated measure columns
* Column properties same as Aggregate for measures

**Equivalence:**
GroupingAggregate(state, grain, aggs, gs) ≡
  UNION ALL of [Aggregate(state, gs_i, aggs) + GROUPING columns for gs_i in gs]
```

### 8.3 SQL_EXPRESSION_SUBSET.md

**§ Aggregation Functions (new subsection — "Grouping Functions"):**

| Function | Syntax | Description | Context |
|:---|:---|:---|:---|
| `GROUPING` | `GROUPING(dimension)` | Returns 0 if dimension is at detail level, 1 if rolled up | Post-aggregation only.  Requires active `grouping_sets`. |
| `GROUPING_ID` | `GROUPING_ID(dim1, dim2, ...)` | Returns bitmask of rolled-up dimensions | Post-aggregation only.  Requires active `grouping_sets`. |

**§ Not Supported in Expressions (line ~188):**

Add:

| Construct | Reason |
|:---|:---|
| `GROUP BY ROLLUP(...)` / `GROUP BY GROUPING SETS(...)` / `GROUP BY CUBE(...)` | These are query-structural modifiers, not expression constructs.  Use the `grouping_sets` property on the grain or query. |

---

## 9. Implementation Steps

### 9.1 Parsing Layer

1. **Extend `GrainSpec`** with optional `grouping_sets` and `grouping_columns` fields.
2. **Extend `LODQuery`** with optional `grouping_sets` and `grouping_columns` fields.
3. **Validation**: Ensure grouping set entries are subsets of grain dimensions; ensure `grouping_columns` names are unique; reject EXCLUDE/TABLE with grouping_sets.

### 9.2 Algebra Layer

1. **Add `GroupingAggregate` as a new `PlanOperation`** (or extend `Aggregate` with an optional `grouping_sets` parameter).
2. **Implement the `grouping_aggregate()` pure function** with validation and state-change rules from §6.
3. **Auto-generate GROUPING columns** and include them in the effective grain.
4. **Unit tests**: Multi-level output, GROUPING column values, grain uniqueness, partial rollup, composition.

### 9.3 Expression Layer

1. **Add `GROUPING()` and `GROUPING_ID()` to the expression analyzer** as recognized functions.
2. **Classify as AGGREGATE_LEVEL**: The filter classifier marks `GROUPING()` as post-aggregation.  The expression analyzer treats them as aggregate-level functions (like SUM, COUNT) for the purpose of filter routing (WHERE vs HAVING).
3. **Validate context**: `GROUPING()` is only valid when `grouping_sets` is active.  Raise a clear error otherwise.
4. **Window function compatibility**: `GROUPING()` references in window function `PARTITION BY` and `ORDER BY` clauses must be recognized by the expression analyzer and passed through to the transpiler.  Since GROUPING columns are regular columns in the post-GroupingAggregate state, window functions reference them by their output column name (e.g., `RANK() OVER (PARTITION BY g_state ORDER BY revenue DESC)`).

### 9.4 Planner Layer

1. **Detect `grouping_sets`** on the query or metric grain during plan generation.
2. **Route through `GroupingAggregate`** instead of `Aggregate` when grouping sets are present.
3. **Branch optimization**: When multiple metrics share the same branch and one has grouping_sets, the planner MAY apply grouping_sets to the entire branch (all metrics compute at all levels).
4. **Composition**: Use FULL OUTER JOIN (via `enrich`, not `merge`) between grouping-sets and non-grouping-sets branches.  Emit W5004 warning for mixed composition.
5. **Validate compatible grouping sets**: When multiple metrics have `grouping_sets`, validate that they roll up the same set of dimensions (see [§4.5](#45-composition-of-two-grouping-sets-branches)).
6. **Re-aggregation**: When a grouping-sets metric requires re-aggregation (finer-than-query grain), apply grouping sets only at the final aggregation step (see [§4.6](#46-re-aggregation-and-grouping-sets)).

### 9.5 Transpiler Layer

1. **Emit `GROUP BY ROLLUP(...)`** when the grouping sets match the ROLLUP pattern.
2. **Emit `GROUP BY GROUPING SETS(...)`** for explicit sets.
3. **Detect partial ROLLUP** (`GROUP BY a, ROLLUP(b, c)`) and emit the optimized form.
4. **UNION ALL fallback** for databases without GROUPING SETS support.
5. **Render `GROUPING()` and `GROUPING_ID()`** in SELECT, HAVING, ORDER BY.

### 9.6 Frontend / Semantic SQL Layer

1. **Parse `{GROUPING_SETS ...}` property block** in both SEMANTIC_AGG and SEMANTIC variants.
2. **Parse native `GROUP BY ROLLUP(...)`** in Variant B via sqlglot AST recognition.  Detect `Rollup`, `Cube`, and `GroupingSets` nodes in the GROUP BY clause and convert to the canonical `grouping_sets` representation.
3. **Parse `GROUPING()` and `GROUPING_ID()`** as expression functions in measure lists.

### 9.7 Testing

1. **E2E validation against TPC-DS**: Q27 (ROLLUP by item/state), Q18 (ROLLUP on demographics), Q22 (ROLLUP over product hierarchy), Q36/Q86 (ROLLUP + RANK).
2. **Grain uniqueness**: Verify GROUPING columns prevent row collisions between detail and subtotal rows.
3. **Composition**: Mixed grouping-sets + non-grouping-sets metrics in the same query.
4. **GROUPING() in filters**: `HAVING GROUPING(dim) = 0` to filter to detail-only.
5. **GROUPING() in window functions**: RANK partitioned by grouping level.
6. **Error cases**: GROUPING() without active grouping_sets; grouping_sets on EXCLUDE grain; grouping set not a subset of grain dimensions.

---

## 10. Out of Scope

### 10.1 CUBE — Deferred

`CUBE` (all 2^N dimension combinations) is defined as a shorthand in §3.4 and can be expressed via explicit `grouping_sets`.  However, dedicated testing and optimization for CUBE patterns is deferred.  No TPC-DS queries use CUBE.

### 10.2 Nested Grouping Sets

SQL allows nested grouping sets like `GROUPING SETS ((a, b), ROLLUP(c, d))`.  This proposal supports only flat grouping sets (each entry is a list of dimensions).  Nested forms can be expanded to their flat equivalents by the parser.

### 10.3 GROUPING SETS Combined with Pivot

Combining `grouping_sets` and `pivot` on the same metric is explicitly disallowed in this proposal.  Pivot consumes a dimension from the grain; grouping_sets adds rows at coarser levels.  These are conceptually opposite operations and their interaction would be complex (does the pivot apply at every rollup level? only the detail level?).

If both are needed, the user should compose them as separate metrics — one with pivot, one with grouping_sets — and let the composition system merge them.

### 10.4 Automatic Total Column Names

Some BI tools automatically label subtotal rows (e.g., "All States" instead of NULL).  This proposal does not include automatic labeling — the user can add label columns via `CASE WHEN GROUPING(dim) = 1 THEN 'All' ELSE dim END` expressions (see [§5.4](#54-expression-examples)).  Automatic labeling may be considered as a future convenience feature.
