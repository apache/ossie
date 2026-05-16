# Mapping BI Models to the Core OSI Abstractions (Take 2 — Unified Set Model)

Author: will.pugh
Date: 12 March 2026
Status: Draft — corresponds to [Unified Set-Operation Proposal](../specs/OSI_Proposal_Resettable_Filters_take2.md)

# Overview

This document maps Power BI, Tableau, ThoughtSpot, and Looker onto the unified set-operation model for filter and grain proposed in Take 2. Both filter and grain use the same `{mode, exclude, include}` shape:

```yaml
filter:
  mode: RELATIVE | FIXED        # RELATIVE = modify inherited; FIXED = declare from scratch
  exclude: [field_names]         # remove matching clauses/dims from inherited set
  include: [expressions | dims]  # add to the set

grain:
  mode: RELATIVE | FIXED
  exclude: [dim_names]
  include: [dim_names]
```

**Evaluation ordering**: inherit -> evaluate include refs in pre-exclude context -> exclude -> include -> evaluate -> propagate.

For date spline and time intelligence operations, see §On Time Intelligence under Power BI.

---

## Power BI (DAX)

Power BI's DAX language is the most complex mapping target. CALCULATE modifies the filter context, and the evaluation context determines grain. The unified model handles this through parallel `exclude`/`include` on both filter and grain.

### CALCULATE Patterns

| DAX Operation | Example | OSI Filter | OSI Grain |
| :---- | :---- | :---- | :---- |
| Column filter | CALCULATE(SUM(Sales\[Amt\]), Color = "Red") | `exclude: [color]` `include: ["color = 'Red'"]` | `exclude: [color]` — always pair with filter. See §DAX Filter-Grain Coupling. |
| Multiple filters | CALCULATE(SUM(Sales\[Amt\]), Color = "Red", Region = "West") | `exclude: [color, region]` `include: ["color = 'Red' AND region = 'West'"]` | `exclude: [color, region]` |
| KEEPFILTERS | CALCULATE(SUM(Sales\[Amt\]), KEEPFILTERS(Color = "Red")) | `include: ["color = 'Red'"]` | RELATIVE (default) — KEEPFILTERS is additive, no grain change |

### REMOVEFILTERS / ALL Patterns

| DAX Operation | Example | OSI Filter | OSI Grain |
| :---- | :---- | :---- | :---- |
| REMOVEFILTERS(column) | CALCULATE(SUM(Sales\[Amt\]), REMOVEFILTERS(Color)) | `exclude: [color]` | `exclude: [color]` — always pair with filter |
| REMOVEFILTERS(table) | CALCULATE(SUM(Sales\[Amt\]), REMOVEFILTERS(Products)) | `exclude: [products.*]` | `exclude: [all Products dim fields]` |
| ALL() | CALCULATE(SUM(Sales\[Amt\]), ALL()) | `mode: FIXED` | `mode: FIXED, include: []` |
| ALL(column) | CALCULATE(SUM(Sales\[Amt\]), ALL(Color)) | `exclude: [color]` | `exclude: [color]` — synonym for REMOVEFILTERS(column) |
| ALL(table) | CALCULATE(SUM(Sales\[Amt\]), ALL(Products)) | `exclude: [products.*]` | `exclude: [all Products dim fields]` |
| ALLEXCEPT(Products, Color) | CALCULATE(SUM(Sales\[Amt\]), ALLEXCEPT(Products, Color)) | `exclude: [products.size, products.category, ...]` | `exclude: [products.size, products.category, ...]` |

### FILTER(ALL(...)) Patterns

| DAX Operation | Example | OSI Filter | OSI Grain |
| :---- | :---- | :---- | :---- |
| FILTER with ALL | CALCULATE(SUM(Sales\[Amt\]), FILTER(ALL(Products), Price > 100)) | `exclude: [products.*]` `include: ["price > 100"]` | `exclude: [all Products dim fields]` |
| FILTER compound | CALCULATE(SUM(Sales\[Amt\]), FILTER(ALL(Products), Color = "Red" AND Size = "Large")) | `exclude: [products.*]` `include: ["color = 'Red' AND size = 'Large'"]` | `exclude: [all Products dim fields]` |

### Other DAX Constructs

| DAX Operation | Example | OSI Filter | OSI Grain |
| :---- | :---- | :---- | :---- |
| VALUES as filter arg | CALCULATE(SUM(Sales\[Amt\]), VALUES(Color)) | `include: ["EXISTS_IN(color, <ctx_field>)"]` | RELATIVE (default) |
| CROSSFILTER | CALCULATE(SUM(Sales\[Amt\]), CROSSFILTER(CustID, ID, BOTH)) | (no filter change) | RELATIVE. `joins: {path: [...], type: FULL}` |
| USERELATIONSHIP | CALCULATE(SUM(Sales\[Amt\]), USERELATIONSHIP(ShipDate, Date)) | (no filter change) | RELATIVE. `joins: {path: [...]}` |
| Context Transition | SUMX(Customers, CALCULATE(SUM(Sales\[Amt\]))) | (no filter change) | `include: [customers.id]` |

### DAX Filter-Grain Coupling (CRITICAL)

In DAX, the filter context IS the grain — visual row/column shelves create per-dimension filters that act as grouping. When CALCULATE/ALL/REMOVEFILTERS removes a filter, it also removes the corresponding grouping if that dimension was providing the grain.

In OSI, filter and grain are independent. **For DAX patterns, always pair filter `exclude` with grain `exclude` on the same columns:**

| Situation | Filter | Grain |
| :---- | :---- | :---- |
| CALCULATE / REMOVEFILTERS / ALL(column) | `exclude: [dim]` | `exclude: [dim]` — always pair both |
| ALL() (remove everything) | `mode: FIXED` | `mode: FIXED, include: []` |
| KEEPFILTERS | `include: ["expr"]` | RELATIVE (default) — no grain change |

**Why always EXCLUDE on grain?** When the reset column is NOT a query dimension, `exclude: [dim]` on grain is a harmless no-op. When it IS a query dimension, EXCLUDE is required — without it, the metric computes at the query grain but with a different filter, causing incorrect join behavior (the CTE uses the reset column as a join key, but only contains rows matching the replacement value).

**Importer guidance**: Always emit grain `exclude` alongside filter `exclude` for DAX patterns. Do not try to determine whether the columns are query dimensions — include EXCLUDE prophylactically. If the measure appears in multiple visuals with different dimension layouts, generate multiple OSI metrics.

**Contrast with ThoughtSpot:** ThoughtSpot decouples grain and filter — `query_filters()-{dim}` changes the filter without the grain. This gives different semantics: each dimension value gets its own unfiltered total, rather than a replicated parent total. This is valid in ThoughtSpot but differs from DAX behavior. See §ThoughtSpot.

### On Time Intelligence

Period-over-period patterns work because `include` expression references are evaluated in the pre-exclude context (step 2 of evaluation ordering):

```yaml
- name: period_start
  expression: MIN(date.date)
  grain:
    mode: FIXED
    include: []

- name: period_end
  expression: MAX(date.date)
  grain:
    mode: FIXED
    include: []

# exclude removes date filter; include adds shifted range.
# period_start/period_end in include are evaluated PRE-exclude,
# so they see the user's original date filter.
# grain exclude pairs with filter exclude (DAX filter-grain coupling).
- name: revenue_last_year
  expression: SUM(orders.amount)
  grain:
    exclude: [date.date]
  filter:
    exclude: [date.date]
    include:
      - "date.date >= DATEADD(year, -1, period_start)
         AND date.date <= DATEADD(year, -1, period_end)"
```

### Weaknesses

| Operation | Supported | Notes |
| :---- | :---- | :---- |
| REMOVEFILTERS(table) / ALL(table) | Yes | Use `exclude: [products.*]` wildcard. |
| ALLSELECTED | No | Requires filter context stack / save-restore. No semantic layer supports this. |
| CROSSFILTER exclusion | No | Would need a join-path exclusion mechanism. |
| VALUES/HASONEVALUE/SELECTEDVALUE | Yes | Via EXISTS\_IN semi-join pattern. |
| Context Transition | Yes | Via `grain: {include: [pk]}` for iterator functions. |

---

## Tableau

Tableau's LOD expressions map directly to the unified model. The key mapping rule is: **Tableau FIXED = `mode: FIXED` on both filter and grain.**

### LOD Expressions

| Tableau Operation | Example | OSI Filter | OSI Grain |
| :---- | :---- | :---- | :---- |
| FIXED LOD | {FIXED [color]: SUM(qty)} | `mode: FIXED` | `mode: FIXED, include: [color]` |
| FIXED with context filter | {FIXED [color]: SUM(qty)} + context filter color='Red' | `mode: FIXED, include: ["color = 'Red'"]` | `mode: FIXED, include: [color]` |
| INCLUDE LOD | {INCLUDE [year]: SUM(qty)} | RELATIVE (default) | `include: [year]` |
| EXCLUDE LOD | {EXCLUDE [color]: SUM(qty)} | RELATIVE (default) | `exclude: [color]` |
| Regular calc | SUM(qty) | RELATIVE (default) | RELATIVE (default) |
| Regular calc + context filter | SUM(qty) + context filter color='Red' | `include: ["color = 'Red'"]` | RELATIVE (default) |

### Importer Note: FIXED LOD = mode: FIXED on Both

Tableau's `FIXED` keyword is a coupled construct — it simultaneously declares the grain and resets the filter context. In the unified model, importers emit `mode: FIXED` on both properties:

- `grain: {mode: FIXED, include: [dims]}` — from the LOD dimension list
- `filter: {mode: FIXED}` — clears all dimension/measure filters

Context filters survive into FIXED LODs. If present, add them to filter include:
`filter: {mode: FIXED, include: ["context_filter_expr"]}`

`INCLUDE` and `EXCLUDE` LODs do NOT reset filters — they use `mode: RELATIVE` on both filter (inherit, no changes) and grain (add or remove dims).

### Table Calculations (Window Functions)

Tableau Table Calculations (RUNNING\_SUM, RANK, WINDOW\_AVG, INDEX, etc.) operate on the post-aggregation visual result set. OSI supports these via **window function expressions** applied after the aggregation step.

| Tableau Table Calc | Example | OSI Expression |
| :---- | :---- | :---- |
| RUNNING\_SUM | Running total of SUM(Sales) | `SUM(total_revenue) OVER (PARTITION BY region ORDER BY product)` |
| RANK | Rank regions by revenue | `RANK() OVER (ORDER BY total_revenue DESC)` |
| WINDOW\_AVG | Moving average of SUM(Sales) | `AVG(total_revenue) OVER (PARTITION BY region ORDER BY product ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING)` |
| RUNNING\_COUNT | Running count of records | `COUNT(*) OVER (ORDER BY order_date)` — requires inline expression |
| INDEX | Row number within partition | `ROW_NUMBER() OVER (PARTITION BY region ORDER BY product)` |

**Importer conversion note:** Tableau Table Calcs are **viz-specific** — the same calculated field can produce different results on different worksheets because the `PARTITION BY` and `ORDER BY` are derived from the visual's dimension layout (the "Compute Using" / "Addressing" / "Partitioning" settings). When converting to OSI, the importer must generate the explicit `PARTITION BY` and `ORDER BY` clauses based on the specific visualization the Table Calc was used in. A single Tableau Table Calc field used on three different worksheets may require three separate OSI metric definitions.

**Future direction — addressing as a first-class property:** To achieve Tableau's flexibility (where a single field definition works across different visuals), OSI would need an **addressing property** for window functions that parallels what `grain` and `filter` do for aggregation context:

```yaml
# Hypothetical future syntax (not yet supported)
- name: running_revenue
  expression: "SUM(total_revenue)"
  window:
    mode: RELATIVE                 # inherit from query context
    partition: query_dimensions()  # partition by query dims
    order: [order_date]            # explicit ordering
    frame: ROWS UNBOUNDED PRECEDING
```

This would let the planner derive `PARTITION BY` from the query's dimensions at plan time, just as `grain: RELATIVE` derives `GROUP BY` from the query. Without this, the window function's `PARTITION BY` and `ORDER BY` must be hardcoded in the metric expression.

### Limitations

| Feature | Notes |
| :---- | :---- |
| Table Calculations | ✅ **Supported** via window function expressions. Conversion requires generating viz-specific PARTITION BY / ORDER BY. See §Table Calculations above. |
| Dimension vs Measure Filters | Tableau applies dim filters before LODs, measure filters after. OSI doesn't distinguish — importers decide filter placement. |

---

## ThoughtSpot

ThoughtSpot's `group_aggregate` maps 1:1 to the unified model. The function's three arguments correspond directly to expression, grain, and filter:

```
group_aggregate(measure, grain_parameter, filter_parameter)
```

### Grain Mapping (second argument)

| ThoughtSpot | OSI Grain |
| :---- | :---- |
| `{region, category}` | `mode: FIXED, include: [region, category]` |
| `query_groups()` | `mode: RELATIVE` (default) |
| `query_groups()+{year}` | `include: [year]` |
| `query_groups()-{category}` | `exclude: [category]` |
| `query_groups()-{date}+{year(date)}` | `exclude: [date], include: [year_date]` |

The last row is the **key expressiveness improvement** — ThoughtSpot's mixed-mode `+`/`-` on `query_groups()` maps directly to `exclude` + `include` on grain. This was not expressible in the previous OSI model.

### Filter Mapping (third argument)

| ThoughtSpot | OSI Filter |
| :---- | :---- |
| `query_filters()` | `mode: RELATIVE` (default) |
| `query_filters()-{Ship Mode}` | `exclude: [ship_mode]` |
| `query_filters()+{Ship Mode='air'}` | `include: ["ship_mode = 'air'"]` |
| `{}` (empty) | `mode: FIXED` |
| `{Ship Mode='air'}` | `mode: FIXED, include: ["ship_mode = 'air'"]` |
| `{A='x', B='y'}` | `mode: FIXED, include: ["a = 'x'", "b = 'y'"]` |

### Combined Examples

| ThoughtSpot | OSI Filter | OSI Grain |
| :---- | :---- | :---- |
| `group_aggregate(sum(S), {cust}, {})` | `mode: FIXED` | `mode: FIXED, include: [cust]` |
| `group_aggregate(sum(S), qg(), qf())` | RELATIVE | RELATIVE |
| `group_aggregate(sum(S), qg()-{cat}, qf())` | RELATIVE | `exclude: [cat]` |
| `group_aggregate(sum(S), qg()-{dt}+{yr}, qf()-{ship})` | `exclude: [ship]` | `exclude: [dt], include: [yr]` |
| `group_aggregate(sum(S), qg(), qf()+{ship='air'})` | `include: ["ship='air'"]` | RELATIVE |

### Expressiveness Comparison

| Feature | ThoughtSpot | OSI (Unified) | Notes |
| :---- | :---- | :---- | :---- |
| Selective filter remove | `qf()-{col}` | `exclude: [col]` | 1:1 |
| Additive filter | `qf()+{expr}` | `include: ["expr"]` | 1:1 |
| Combined remove + add filter | Not in one call | `exclude: [col], include: ["expr"]` | OSI is more expressive (CALCULATE pattern) |
| Mixed grain +/- | `qg()-{d1}+{d2}` | `exclude: [d1], include: [d2]` | 1:1 (was a gap, now closed) |
| Full filter reset | `{}` | `mode: FIXED` | 1:1 |
| Explicit filter set | `{expr1, expr2}` | `mode: FIXED, include: ["e1", "e2"]` | 1:1 |

OSI is a **strict superset** of ThoughtSpot's expressiveness — it can express everything ThoughtSpot can, plus the combined remove+add (CALCULATE) pattern that ThoughtSpot cannot do in a single call.

### Limitations

| Feature | Notes |
| :---- | :---- |
| Chasm trap handling | ThoughtSpot detects and refuses. OSI computes separate branches. |
| SpotIQ | Query generation, not a semantic model concept. |

---

## Looker

Looker's semantic layer (LookML) has the simplest mapping — measures have additive filters only, and grain comes from the query.

### Measures

```lookml
measure: completed_revenue {
  type: sum
  sql: ${amount} ;;
  filters: [status: "completed"]
}
```

```yaml
# OSI equivalent
- name: completed_revenue
  expression: SUM(amount)
  filter:
    include: ["status = 'completed'"]
```

Looker measures always use `mode: RELATIVE` with `include` only. No exclude, no FIXED. Grain is always query-determined (RELATIVE default).

### Derived Tables

Looker's derived tables use explicit SQL with independent GROUP BY and WHERE. These map to metrics with explicit grain and filter:

```yaml
# Looker derived table → OSI metric
- name: customer_lifetime_value
  expression: SUM(amount)
  filter:
    mode: FIXED
    include: ["status = 'completed'"]
  grain:
    mode: FIXED
    include: [customer_id]
```

### Templated Filters

Looker's `{% condition %}` Liquid syntax injects user-provided filter values into derived table SQL. These map to additive filter `include` expressions that reference parameters:

```yaml
filter:
  include: ["region = :region_param"]
```

### Limitations

| Feature | Notes |
| :---- | :---- |
| No grain override | LookML measures have no FIXED/INCLUDE/EXCLUDE. Grain always from query. |
| No filter reset | LookML measures can only add filters, never remove. |
| Derived tables | Full SQL control — grain and filter are independent by construction. |

---

## Cross-Tool Summary

| Capability | Power BI | Tableau | ThoughtSpot | Looker | OSI (Unified) |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Inherit filters | Yes (default) | Yes (non-LOD) | `qf()` | Yes (default) | `mode: RELATIVE` |
| Add filter | KEEPFILTERS | Context filter | `qf()+{e}` | `filters:` | `include: [e]` |
| Remove filter | ALL/REMOVEFILTERS | FIXED (all) | `qf()-{c}` | No | `exclude: [c]` |
| Replace filter | CALCULATE | No | No (one call) | No | `exclude + include` |
| Clear all filters | ALL() | FIXED | `{}` | No | `mode: FIXED` |
| Fixed grain | Via context | FIXED | `{dims}` | Via SQL | `mode: FIXED, include:` |
| Add grain dim | Via context | INCLUDE | `qg()+{d}` | No | `include: [d]` |
| Remove grain dim | Via context | EXCLUDE | `qg()-{d}` | No | `exclude: [d]` |
| Mixed grain +/- | Via context | No | `qg()-{d1}+{d2}` | No | `exclude + include` |
| Window functions | DAX (RANKX, etc.) | Table Calculations | N/A | N/A | `RANK() OVER (...)` in expression |

---

## Known Unsupported Patterns

| Pattern | Source Tool | Why Unsupported | What Would Be Needed |
| :---- | :---- | :---- | :---- |
| ALLSELECTED | Power BI | Requires filter context stack (source-aware, not content-aware). | Save/restore mechanism. No other tool supports this. |
| Dynamic format strings | Power BI | Display-layer concern, not filter/grain. | Format expression on field metadata. |
| CROSSFILTER exclusion | Power BI | Disabling specific join paths. | Join-path exclusion mechanism on `joins` spec. |

### Previously Listed as Unsupported (Now Supported)

| Pattern | Status | Notes |
| :---- | :---- | :---- |
| Table Calculations | ✅ Supported | Window functions (RANK, ROW\_NUMBER, SUM OVER, LAG, LEAD, etc.) are implemented in the planner (Phase 9, Step 3). See §Table Calculations under Tableau. The remaining gap is **addressing as a first-class property** — currently PARTITION BY / ORDER BY must be hardcoded per metric rather than derived from query context. |
