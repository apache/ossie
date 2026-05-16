# Specification: Safe Analytics Execution Algorithm & Algebra

**Status:** Implemented (core); composition and advanced filters in progress
**Objective:** To implement a safe, correct-by-construction SQL generation engine based on the OSI Calculation Model, specifically addressing cross-grain filtering, fan-out traps, semi-additive aggregation, metric composition, and chasm-trap (multi-fact) queries.

---

## 1. Algebra Implementation Strategy

The core execution engine transforms a **Semantic Query** (Dimensions, Measures, Filters) into a **linear sequence** of algebraic state transitions.

The full algebra is defined in [OSI_Calc_Model_Semantics.md](./OSI_Calc_Model_Semantics.md).

---

# Algorithm: Safe Semantic Query Execution

**Objective:** Transform a Semantic Query (Dimensions, Measures, Filters) into a sequence of `CalculationState` transitions, resolving each **measure independently** from its source dataset, then composing the results at the query grain.

---

## Phase 1: Measure-First Source Resolution

**Objective:** Determine the source dataset for every measure without requiring a globally-designated "primary" dataset.

### Resolution Order (per measure)

1. **Explicit override** — `MeasureRequest.source_dataset` is set → use it directly.
2. **Field-dependency inference** — Analyze the measure expression's column references:
   - All deps on one dataset → that dataset.
   - Deps on multiple datasets → use the relationship graph to find the unique finest-grain dataset (the one from which all others are forward-reachable via FK edges). This correctly resolves CASE WHEN expressions that mix dimension and fact columns.
   - Inferred source must be a fact table (many-side of some FK). If the inferred dataset is a pure dimension table (snowflake leaf), fall through to the fallback.
3. **Context-aware fallback** (for `COUNT(*)` and other dep-free expressions):
   - Collect all fields referenced in **dimensions + filters** of the query.
   - Find the dataset that can forward-reach all of those field-owning datasets. This is typically the central fact table in a star schema.
   - If multiple fact tables tie (chasm trap), the dominant-vote measure wins.
4. **Last resort** — No graph available: vote-count on all field references across dimensions and filters.

---

## Phase 2: Plan Generation via OSI Algebra

**Objective:** Create a linear sequence of `CalculationState` transitions.

### Step 1: Resolve all measure sources (Phase 1 above)

### Step 2: Resolve dimension metrics and derived dimensions

Classify each query dimension as:
- **Physical field** — exists on a source dataset. Standard handling.
- **Metric-as-dimension** — a FIXED metric used as a query dimension (e.g., `customer_segment = CASE WHEN SUM(amount) > 1000 THEN 'High' END`). These are materialized before branch filtering via a sub-branch: SOURCE → AGGREGATE at the metric's FIXED grain → Enrich back.
- **Derived dimension** — an expression wrapping results (e.g., `YEAR(first_order_date)`). Applied via AddColumns + RefineGrain.

### Step 3: Group measures into branches

Measures that share the same `(source_dataset, effective_grain, filters, join_type)` tuple are placed in the same plan branch. The **chasm-trap case** (two independent fact tables sharing a dimension) naturally produces **two separate branches** — this is the general case, not a special fallback.

### Step 4: Build one branch per group

For each branch:

1. **Initialize state** from the branch's source dataset. → `SOURCE`
2. **Resolve cross-table dependencies** (dimensions + filters that live on foreign tables) via FK traversal. Each branch resolves its own foreign joins independently. → `SOURCE` + `Enrich` per hop
3. **Materialize dimension metrics** (if any). Sub-branch per FIXED metric: SOURCE → Aggregate → Enrich back. → `Aggregate` + `Enrich`
4. **Apply derived dimensions** (if any). → `AddColumns` + `RefineGrain`
5. **Apply filters** (§Phase B below). → `Filtering`, `FilteringJoin`, etc.
6. **Metric composition** (§Phase C below) — resolve metrics that reference other metrics. → `Aggregate` + `Enrich` + `AddColumns`
7. **Aggregate** to the branch's effective grain. → `Aggregate`
8. **Re-aggregate** if INCLUDE grain (finer than query) — see §Phase D. → `Aggregate` (2-pass)
9. **Post-aggregation scalars** — non-aggregate expressions referencing sibling output names, topologically sorted. → `AddColumns`
10. **Window functions** — applied after aggregation. → `AddColumns`
11. **QUALIFY filters** — filters referencing window function results. → `Filtering`

### Step 5: Compose branches at query grain

- **Same grain as query** → `Merge` (FULL OUTER JOIN on grain keys).
- **Coarser than query with shared dims** → `Enrich` (LEFT JOIN on shared dims).  The coarser branch's `joins.type` controls the join type (overridable).
- **Empty grain (FIXED [])** → `BroadcastEnrich` (CROSS JOIN — scalar replicated to all rows).
- **Disjoint grain** → `BroadcastEnrich` (CROSS JOIN + W5003 warning — likely unintended).
- **Finer than query** (INCLUDE) → already re-aggregated in step 8, so appears as same-grain.

---

## Phase B: Advanced Filter Handling

### Step 0: Resolve Filter Contexts

Before filter classification, each branch resolves its **filter context** — the set of independent AND-separated clauses that apply to the branch.

For each branch, the filter context is computed by applying the measure's `filter` properties to the parent's filter context (which starts as the query WHERE clause):

1. **Inherit** the parent's filter context (a flat tuple of clause strings).
2. **Apply `reset`**:
   - `false` (default): no change to inherited clauses.
   - `true`: clear all inherited clauses (empty context).
   - `[field_names]`: parse each inherited clause to extract column references; remove any clause where a column reference matches a field in the reset list (after identifier normalization).
3. **Add `expression`**: if the field has `filter.expression`, split it at top-level AND (without flattening parenthesized inner ANDs), and append each piece as an independent clause.

The resulting filter context is what gets decomposed to CNF and classified in subsequent steps. Metrics with different effective filter contexts produce separate branches.

**Critical invariant:** The filter context is always stored as a flat tuple of clause strings. No parenthesization is introduced during context inheritance or expression addition. This ensures that selective reset (`reset: [fields]`) can always reach clauses from any ancestor layer.

**Field-level propagation:** After applying the measure's filter spec, `resolve_effective_filters` also inspects `Field.filter` specs on columns referenced in the metric expression. Each field with its own filter spec contributes to the effective filter context, which naturally produces separate branches when filter sets differ.

### Step 1: CNF Decomposition and Classification

Filters are first decomposed to CNF (conjunctive normal form), then each clause is independently classified and applied.

### Step 1a: Row-Level Filters (Pre-Aggregation)

Scalar predicates (no aggregations, no cross-dataset references) are applied immediately via `Filtering()`.

### Step 2: Semi-Join Filters (EXISTS_IN)

Semi-join filters (expressed via `EXISTS_IN(outer_col, dataset.field)` in the OSI expression language) are handled via `FilteringJoin`:

1. Build right-side state from the target dataset. → `SOURCE`
2. `FilteringJoin(CurrentState, right_state, SEMI, join_conditions)` for positive existence.
3. `FilteringJoin(CurrentState, right_state, ANTI_SEMI, join_conditions)` for `NOT EXISTS_IN`.

This produces clean `WHERE EXISTS (...)` / `WHERE NOT EXISTS (...)` SQL. No row duplication, no extra columns.

### Step 3: Cross-Grain Filters (The "Semi-Join" Loop)

Filters whose **natural grain** differs from the row grain. These reference aggregated values that must be computed at a different grain before they can be used as predicates.

* **Case A: Positive Existence** (e.g., "Show Orders by High Value Customers")
    * *Logic:* `Customer_Total > 1000`
    1.  Create `FilterBranch` state from source table. → `SOURCE`
    2.  `Aggregate(FilterBranch)` to the filter's grain (e.g., Customer).
    3.  `Filtering(FilterBranch)` applied to the aggregate result.
    4.  `FilteringJoin(CurrentState, FilterBranch, SEMI)` on shared keys.

    Implementation note: the current implementation uses Enrich + RefineGrain instead of FilteringJoin for Case A.  This produces more CTEs and leaves extra columns in the state. **TODO:** Switch to the FilteringJoin approach, which produces cleaner SQL (`WHERE EXISTS (...)`) with fewer CTEs.

* **Case B: Universal Negation** (e.g., "Customers who NEVER bought X")
    * *Logic:* `NOT EXISTS (Product = 'X')`
    1.  Create `FilterBranch` state from source table.
    2.  `Filtering(FilterBranch)` with the positive condition (Product = 'X').
    3.  `Aggregate(FilterBranch)` to the common key (Customer ID).
    4.  `FilteringJoin(CurrentState, FilterBranch, ANTI_SEMI)` on shared keys.

* **Case C: Cohort/Inequality** (e.g., "Orders within 30 days of first order")
    * *Logic:* `OrderDate < FirstOrderDate + 30`
    1.  Create `CohortBranch`. → `SOURCE`
    2.  `Aggregate(CohortBranch)` to calculate the baseline (e.g., `MIN(Date)` per Customer).
    3.  `Enrich(CurrentState, CohortBranch)` to bring the baseline column to the row grain.
    4.  `Filtering(CurrentState)` using the comparison logic.

* **Case D: Mixed-OR** (e.g., `amount > 250 OR SUM(amount) > 1500`)
    1.  Extract aggregate sub-expressions.
    2.  Build sub-branch: `SOURCE` → `Aggregate` at query grain.
    3.  `Enrich` aggregate results back onto source grain as flag columns.
    4.  Rewrite the OR predicate using the materialized flag columns.
    5.  `Filtering(CurrentState)` with the rewritten predicate.

* **Case E: Embedded EXISTS_IN** (EXISTS_IN inside OR, CASE, etc.)
    1.  Build right-side state from the target dataset.
    2.  `Aggregate(right_state)` with DISTINCT on join columns.
    3.  `Enrich(CurrentState, right_state)` — LEFT JOIN to materialize a flag column.
    4.  Rewrite the embedded EXISTS_IN as `_ef_flag IS NOT NULL` (or `IS NULL` for NOT).
    5.  Continue with the rewritten expression in the enclosing filter.

### Step 4: HAVING Filters (Post-Aggregation)

Filters referencing aggregated values at the query grain:

1.  Collected as `pending_having` during filter classification.
2.  Applied as metadata on the Aggregate step.
3.  For re-aggregation branches (INCLUDE grain): HAVING is applied via a sub-branch `Aggregate` + `FilteringJoin(SEMI)` to avoid double-aggregation issues.

### Step 5: Final Aggregation

Safety checks are applied before and during aggregation:
* If a measure uses a column marked `is_join_exploded=True`, only explosion-safe aggregations (`MIN`, `MAX`, `COUNT DISTINCT`) are allowed. `SUM` / `AVG` are blocked unless wrapped in `UNSAFE()`.
* `Aggregate(CurrentState)` to the requested grain.

---

## Phase C: Metric Composition

When a measure's expression references other metrics by name (e.g., `revenue / NULLIF(total_revenue, 0)`), the referenced metrics must be computed first and their results made available.

### Step 1: Collect inner metrics

Recursively trace metric-on-metric references with cycle detection. Group inner metrics by `(grain, filters)` for batched computation.

### Step 2: Compute inner metrics

For each group of inner metrics:

* **Aggregation composition (Phase 8A):** The inner metric contains an aggregation (e.g., `AVG(customer_total)`). Build a sub-branch: SOURCE → [Filter] → Aggregate at inner grain. The outer expression wraps the result in an aggregation (e.g., `AVG(_composed_customer_total)`).

* **Scalar composition (Phase 8B):** The inner metric is a non-aggregate expression referencing other metrics (e.g., `revenue / NULLIF(total_revenue, 0)`). The inner metrics are computed and their values are used directly via AddColumns.

* **TABLE-grain scalars:** Computed via AddColumns with `ensure_scalar_deps` resolving cross-table joins for foreign columns. The outermost scalar's `joins.type` controls all enrichment joins (see [OSI_Core_Abstractions.md §Nested Scalars](./OSI_Core_Abstractions.md)).

* **AGG() decomposition:** When the outer expression uses `AGG(inner_metric)`, the inner metric is expanded into accumulator intermediates based on its aggregation category:
  * **Distributive** (SUM, COUNT, MIN, MAX): re-aggregate directly (e.g., COUNT → SUM for re-agg)
  * **Algebraic** (AVG, STDDEV, VARIANCE): maintain `<sum, count>` (or `<sum, sum_sq, count>`) intermediates, finalize after re-aggregation
  * **Holistic** (MEDIAN, COUNT DISTINCT): accumulate via `ARRAY_AGG`, recompute from merged arrays

### Step 3: Rewrite outer expression

Replace metric names with composed column references. Classify the rewritten expression as aggregation (stays in measures) or scalar (applied via AddColumns immediately).

### Step 4: Supplementary aggregation

If a branch has both composed scalars and regular (non-referencing) aggregation measures, the regular measures need a separate aggregation pathway since the composed scalars have already consumed the raw columns. Build a supplementary sub-branch: SOURCE → Filter → Aggregate → Enrich back onto the composition state.

---

## Phase D: Re-Aggregation (INCLUDE grain)

When a metric's effective grain is finer than the query grain (INCLUDE mode), the result must be re-aggregated.

### Step 1: Classify measures

Split into **distributive** (SUM, COUNT, MIN, MAX) and **accumulator-based** (AVG, STDDEV, MEDIAN, etc.).

### Step 2: Aggregate to finer grain

* Distributive measures use their expression directly.
* Algebraic measures expand into accumulator intermediates (e.g., AVG → `SUM(col)` as `_sum`, `COUNT(col)` as `_count`).
* Holistic measures use `ARRAY_AGG(col)` as intermediate.

### Step 3: Re-aggregate to query grain

* Distributive: use `build_reagg_expression()` (e.g., COUNT → SUM for re-agg).
* Algebraic: combine intermediates (e.g., `SUM(_sum) / SUM(_count)` for AVG).
* Holistic: `MEDIAN(ARRAY_CONCAT_AGG(_values))` or similar.

### Step 4: Finalize accumulators

Apply `AddColumns` with finalize expressions, then `Project` to remove intermediate columns.

---

## 2. Algebra Operations Reference

| Operation | Description |
|---|---|
| `Aggregate` | GROUP BY to a coarser grain with safety checks |
| `ExtendLOD` | Join-then-aggregate to a new grain (derived: AddDimensions + Aggregate) |
| `AddDimensions` | Add dimension columns via join, tracking explosion safety |
| `FilterToRemoveLOD` | Pin a grain dimension to a single value, removing it from the grain |
| `RefineGrain` | Promote functionally-dependent columns into the grain |
| `AddColumns` | Add scalar/window expressions without changing grain |
| `Project` | Remove columns from state |
| `MakeAttr` | Assert single-valuedness of a column |
| `Merge` | Combine two same-grain states (FULL OUTER or INNER JOIN on grain keys) |
| `Enrich` | N:1 or 1:1 LEFT JOIN — add columns, mark explosion |
| `BroadcastEnrich` | CROSS JOIN a scalar/coarser-grain value onto every row |
| `Filtering` | Apply WHERE / HAVING predicates |
| `FilteringJoin` | SEMI or ANTI_SEMI join for existence / non-existence filters |

---

## 3. Critical Test Cases

### Test Case 1: The "High-Water Mark" (Aggregate-to-Detail)
* **Query:** "Show me specific line items for Orders that totalled > $500."
* **Execution Path:**
    1.  Aggregate to Order grain → `Order_Total = $550`.
    2.  Filter `Order_Total > 500`.
    3.  `FilteringJoin(SEMI)` back to line items.
* **Pass Criteria:** Order A's items ($300, $250) are returned even though neither individually exceeds $500.

### Test Case 2: Universal Negation (The "NOT EXISTS" Trap)
* **Query:** "Customers who have **NEVER** bought Socks."
* **Execution Path:**
    1.  Identify Socks Buyers → {Cust 2}.
    2.  `FilteringJoin(ANTI_SEMI)` against {Cust 2}.
* **Pass Criteria:** Returns Cust 1 only (Cust 2 bought Hat AND Socks — Hat transaction must not appear).

### Test Case 3: The Cohort (Inequality Join)
* **Query:** "Transactions within 30 days of Customer's first purchase."
* **Execution Path:**
    1.  `SELECT MIN(Date) GROUP BY Cust_ID` → first purchase dates.
    2.  `Enrich` first-purchase date onto transaction rows.
    3.  Filter `Trans_Date <= Min_Date + 30`.
* **Pass Criteria:** Trans A (Jan 15) kept; Trans B (Feb 15) dropped.

### Test Case 4: The Chasm Trap (Multi-Fact Join Explosion)
* **Query:** "Total Sales (from `sales`) and Count of Returns (from `returns`) per Customer."
* **Execution Path** (measure-first architecture):
    1.  Branch A: `sales` → join `customer` → aggregate to `{customer_id}` grain.
    2.  Branch B: `returns` → join `customer` → aggregate to `{customer_id}` grain.
    3.  Merge branches at `{customer_id}` grain via FULL OUTER JOIN.
* **Pass Criteria:** Count = 2 (from returns), Sales = sum of 2 orders. No cross-multiplication.

### Test Case 5: Scalar expressions across tables
* Across 1-1 joins
* Across 1-many joins with `is_join_exploded` safety
* Across snapshot tables filtered to 1-many via `FilterToRemoveLOD()`

### Test Case 6: Metric Composition
* **Query:** "Percent of total revenue by region" — `revenue / NULLIF(total_revenue, 0) * 100`
* **Execution Path:**
    1.  Compute `total_revenue` at FIXED [] (grand total) as inner metric.
    2.  Compute `revenue` at QUERY grain [region] as regular measure.
    3.  BroadcastEnrich the scalar total onto the query-grain result.
    4.  AddColumns to compute the ratio.
* **Pass Criteria:** Each region shows its percentage; percentages sum to 100%.

### Test Case 7: Re-Aggregation (INCLUDE grain)
* **Query:** "Average customer order total by region" — `AVG(customer_total)` where `customer_total = SUM(amount)` at INCLUDE [customer_id]
* **Execution Path:**
    1.  Aggregate `SUM(amount)` to [region, customer_id] grain (finer than query).
    2.  Re-aggregate: `SUM(_sum) / SUM(_count)` to [region] grain (weighted average, not average-of-averages).
* **Pass Criteria:** Result is the weighted average, NOT `AVG(AVG(amount))`.

---

## 4. Syntax for Handling Exploded Aggregations

When `is_join_exploded=True`:
* **Allowed:** `MIN`, `MAX`, `COUNT(DISTINCT x)`, `ANY_VALUE`, `ARRAY_UNIQUE_AGG`, `ARRAY_UNION_AGG`
* **Blocked:** `SUM`, `AVG`, `VAR`, `STDDEV`
* **Override:** `UNSAFE(expression)` — disables the check for the enclosed expression

---

## 5. LODQuery API (measure-first)

```python
LODQuery(
    dimensions=["region", "segment"],   # query grain
    measures=[
        # Source inferred from field deps: SUM(amount) → sales
        MeasureRequest(output_name="total_sales", expression="SUM(amount)"),
        # Source inferred: SUM(return_amount) → returns  (separate branch)
        MeasureRequest(output_name="total_returns", expression="SUM(return_amount)"),
        # Explicit override required when expression has no field deps
        MeasureRequest(output_name="cnt", expression="COUNT(*)",
                       source_dataset="sales"),
    ],
    filters=["region = 'US'"],
)
```

`dataset_name` has been removed. The planner derives the source dataset for each branch from `MeasureRequest.source_dataset` (explicit) or field-dependency analysis (automatic). This eliminates the three-pass primary-dataset inference and makes the chasm-trap case the general case rather than a special fallback.
