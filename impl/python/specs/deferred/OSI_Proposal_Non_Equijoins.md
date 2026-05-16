# Proposal: Non-Equijoin Relationships

**Status:** Draft Proposal  
**Author:** will.pugh@snowflake.com  
**Date:** 2026-02-23  
**Related specs:**
- [OSI Core File Format](./OSI_core_file_format.md)
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md)
- [SQL Expression Subset](./SQL_EXPRESSION_SUBSET.md)
- [Referential Integrity Settings (companion proposal)](./OSI_Proposal_Referential_Integrity.md)
- [ASOF and Range Joins (companion proposal)](./OSI_Proposal_ASOF_and_Range_Joins.md) — structured temporal/SCD Type-2 joins aligned with Snowflake

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Design Principles](#2-design-principles)
3. [Cardinality: Declared vs. Inferred](#3-cardinality-declared-vs-inferred)
   - [How Cardinality Is Inferred Today](#31-how-cardinality-is-inferred-today)
   - [Why Non-Equijoins Require Declared Cardinality](#32-why-non-equijoins-require-declared-cardinality)
   - [Declared Cardinality for Equijoins (Override)](#33-declared-cardinality-for-equijoins-override)
4. [Proposed Schema Changes](#4-proposed-schema-changes)
   - [Structured Temporal Joins (ASOF and Range)](#44-structured-temporal-joins-asof-and-range)
5. [Semantics](#5-semantics)
   - [Condition Expression Language](#51-condition-expression-language)
   - [Self-Join Column Disambiguation](#52-self-join-column-disambiguation)
   - [Cardinality and the Planner](#53-cardinality-and-the-planner)
   - [N:N Non-Equijoins](#54-nn-non-equijoins)
   - [Cardinality and Safety](#55-cardinality-and-safety)
6. [Ergonomics](#6-ergonomics)
   - [Aliased Dimension Joins](#61-aliased-dimension-joins)
   - [Performance Warning for Range Joins](#62-performance-warning-for-range-joins)
7. [Algebra Changes](#7-algebra-changes)
8. [Effect on Grain Calculations](#8-effect-on-grain-calculations)
9. [TPC-DS Impact Analysis](#9-tpcds-impact-analysis)
10. [Proposed Spec Changes](#10-proposed-spec-changes)
11. [Implementation Steps](#11-implementation-steps)
12. [Out of Scope](#12-out-of-scope)

---

## 1. Motivation

The current `relationships` schema only supports equi-joins: the `from_columns`/`to_columns` arrays encode `from_col = to_col` equality conditions. Many real-world analytical patterns require joins on inequality, ranges, or overlapping intervals:

| Category | Example SQL Predicate | Analytical Pattern |
|:---|:---|:---|
| **Range / Interval** | `sale_date BETWEEN promo.start_date AND promo.end_date` | Attribute a sale to the active promotion window |
| **Band / Tier** | `item.price >= tier.low AND item.price < tier.high` | Classify items into pricing tiers |
| **Overlap** | `a.start <= b.end AND b.start <= a.end` | Scheduling conflicts, concurrent sessions |
| **Inequality / Exclusion** | `a.id <> b.id` | Self-comparison — "other orders by the same customer" |

Current workarounds in OSI:
- Range joins: pre-join in the source SQL using a view/CTE, expose as a single dataset
- Band/tier: use a CASE WHEN expression in the metric (works only when bands are static)
- Overlap: no clean workaround
- Inequality: expressible only as an ad-hoc filter expression, not a reusable relationship

Non-equijoin relationships unlock:
1. **Reusability** — A range join declared once can be reused across many metrics
2. **Planner knowledge** — The planner can reason about cardinality and grain at plan time
3. **Aliased dimension support** — Joining the same dimension twice under different roles (e.g., `date_dim` as both `ship_date` and `order_date`) requires two named relationships between the same dataset pair — something the schema change in this proposal enables

---

## 2. Design Principles

1. **Additive and backward-compatible**: All new fields are optional. Existing models require no changes.
2. **Safety is not relaxed**: Non-equijoins follow the same cardinality safety rules as equijoins. The planner gates which operations are allowed based on declared cardinality.
3. **Condition is a scalar predicate, not a new language**: The join condition uses the existing `SQL_EXPRESSION_SUBSET`. No new expression language is introduced.
4. **Cardinality is always declared for non-equijoins**: The engine cannot infer cardinality from an arbitrary predicate (unlike equijoins, where PK/UK metadata is used). Cardinality is required.
5. **Declare intent, not execution**: Model authors declare *what their data means*. The planner decides whether to use a hash join, nested loop, merge join, or EXISTS subquery.

---

## 3. Cardinality: Declared vs. Inferred

This section explains the cardinality model, which is central to why non-equijoins require a schema addition.

### 3.1 How Cardinality Is Inferred Today

For existing equijoin relationships, the planner infers cardinality **from the dataset schema metadata** — specifically, by checking whether the join columns match declared primary keys and unique keys:

```
get_cardinality(relationship):
  to_is_unique  ← to_columns matches to_dataset.primary_key OR to_dataset.unique_keys
  from_is_unique ← from_columns matches from_dataset.primary_key OR from_dataset.unique_keys

  return ("1" if from_is_unique else "N",
          "1" if to_is_unique   else "N")
```

**Example:** `orders → customers` on `orders.customer_id = customers.id`.  
If `customers.id` is the primary key, `to_is_unique = True` → cardinality is `N-1`. The planner knows each order row has at most one matching customer — safe for enrichment and scalar operations.

This inference is **structural**: it derives from the model's declared keys, not from the data itself. It works reliably for equijoins because the uniqueness of an equality join can be determined from key declarations.

### 3.2 Why Non-Equijoins Require Declared Cardinality

For a non-equijoin such as:
```
store_sales.ss_sold_date_sk BETWEEN promotion.p_start_date_sk AND promotion.p_end_date_sk
```
There are no equi-columns to compare against primary keys. The structural inference algorithm has no basis to determine whether each `store_sales` row matches zero, one, or many `promotion` rows — that depends entirely on the data distribution (are promotion windows non-overlapping per item?). Key metadata cannot answer this question.

Therefore, **cardinality is required as an explicit author declaration on all non-equijoin relationships**. The author asserts what the data guarantees; the planner trusts that assertion and gates operations accordingly. An incorrect declaration will not be caught at parse time — it will produce wrong results silently, similar to declaring a wrong primary key.

> **Important:** This is a trust model, not a validation model. If you declare `cardinality: N:1` on a range join and the data has overlapping intervals (multiple matches), the planner will produce incorrect aggregations — it will not warn you. Declare cardinality conservatively (prefer `N:N` when in doubt).

### 3.3 Declared Cardinality for Equijoins (Override)

The `cardinality` field is also optionally available on equijoin relationships. When present, it **overrides the inferred cardinality**. This is useful when:

- The dataset does not declare primary keys in the model (the inference defaults to `N-N` without them)
- The author knows the cardinality from domain knowledge and wants to express it explicitly without adding key declarations
- The author wants to document the intended cardinality as a form of inline assertion

```yaml
# The planner would infer N-N here because catalog_sales has no PK declared,
# but the author knows this is a standard FK relationship:
- name: catalog_sales_to_customer
  from: catalog_sales
  to: customer
  from_columns: [cs_bill_customer_sk]
  to_columns: [c_customer_sk]
  cardinality: N:1    # override: author asserts this is many-to-one
```

**Precedence:** When `cardinality` is declared on an equijoin, it takes precedence over the key-based inference. The planner uses the declared value and does not run the key check.

---

## 4. Proposed Schema Changes

Two new optional fields are added to the `relationships` schema:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `condition` | string | Conditional* | Non-equijoin SQL predicate using qualified column names |
| `cardinality` | enum | Conditional† | Declared cardinality: `N:1`, `1:1`, `N:N` |

*`condition` is required unless `from_columns`/`to_columns` is present. Both may coexist.  
†`cardinality` is **required** when `condition` is present. It is optional (override) for equijoin-only relationships.

**Full updated relationship schema:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique identifier for the relationship |
| `from` | string | Yes | The dataset on the many-side (FK side) |
| `to` | string | Yes | The dataset on the one-side (PK side) |
| `from_columns` | array | Conditional* | FK columns in the "from" dataset |
| `to_columns` | array | Conditional* | PK/UK columns in the "to" dataset |
| `condition` | string | Conditional* | Non-equijoin SQL predicate (generic form) |
| `asof` | object | No | ASOF join spec — see [ASOF and Range proposal](./OSI_Proposal_ASOF_and_Range_Joins.md) |
| `range` | object | No | Range join spec — see [ASOF and Range proposal](./OSI_Proposal_ASOF_and_Range_Joins.md) |
| `cardinality` | enum | Conditional† | `N:1` (default for equijoins), `1:1`, `N:N` |
| `referential_integrity` | object | No | RI declarations — see [companion proposal](./OSI_Proposal_Referential_Integrity.md) |
| `ai_context` | string/object | No | Additional context for AI tools |
| `custom_extensions` | array | No | Vendor-specific attributes |

*Either `from_columns`/`to_columns`, or `condition`, or `asof`/`range` (with equi-keys for ASOF) must be present. At most one of `condition`, `asof`, `range` per relationship.  
†`cardinality` is required when `condition` is present; optional override otherwise. For `asof`/`range`, N:1 is implicit.

**Cardinality enum values:**

| Value | Meaning |
|:---|:---|
| `N:1` | Each `from` row matches at most one `to` row (standard FK). Safe for enrichment and scalar ops. |
| `1:1` | Each `from` row matches at most one `to` row AND vice versa. Safe for symmetric Merge. |
| `N:N` | Unconstrained — multiple matches possible on both sides. Restricted to FilteringJoin only. |

### 4.4 Structured Temporal Joins (ASOF and Range)

For common SCD Type-2 and temporal patterns, the generic `condition` field can be replaced (or complemented) by structured **ASOF** and **Range** join types. These align with [Snowflake's semantic view ASOF and range relationships](https://docs.snowflake.com/en/user-guide/views-semantic/sql.html) and enable engine-specific optimizations.

| Type | Use Case | Structured Form |
|:---|:---|:---|
| **ASOF** | Single-column temporal lookup; intervals implicit from consecutive rows | `asof: { from_column, to_column, match? }` |
| **Range** | Explicit start/end interval; half-open `[start, end)` | `range: { from_column, start_column, end_column }` + `distinct_ranges` on dataset |

**When to use structured vs. generic:**
- Use **ASOF** when joining to a dimension with a single temporal column (e.g., address history by `ca_start_date`).
- Use **Range** when the dimension has explicit `start`/`end` columns and a non-overlapping constraint.
- Use **condition** for band/tier joins, overlap joins, inequality self-joins, or any predicate not covered by ASOF/Range.

See [OSI_Proposal_ASOF_and_Range_Joins](./OSI_Proposal_ASOF_and_Range_Joins.md) for full schema, validation rules, and Snowflake translation.

**Examples:**

```yaml
relationships:
  # Mixed equi + range condition: sales attributed to active promotion window.
  # The equi-key (item match) is AND'd with the date range condition.
  - name: store_sales_to_promotion
    from: store_sales
    to: promotion
    from_columns: [ss_item_sk]
    to_columns: [p_item_sk]
    condition: "store_sales.ss_sold_date_sk BETWEEN promotion.p_start_date_sk AND promotion.p_end_date_sk"
    cardinality: N:1      # author asserts: at most one active promo per item/date
    referential_integrity:
      from_all_rows_match: false   # some sales have no promotion

  # Pure non-equijoin: price tier classification (no equality key)
  - name: item_to_price_tier
    from: item
    to: price_tier
    condition: "item.i_current_price >= price_tier.tier_low AND item.i_current_price < price_tier.tier_high"
    cardinality: N:1

  # Self-join / exclusion pattern — see §5.2 for disambiguation syntax
  - name: catalog_sales_cross_warehouse
    from: catalog_sales
    to: catalog_sales
    condition: "from.cs_order_number = to.cs_order_number AND from.cs_warehouse_sk <> to.cs_warehouse_sk"
    cardinality: N:N

  # Aliased equijoin: two differently-named relationships between the same datasets
  - name: catalog_sales_to_ship_date
    from: catalog_sales
    to: date_dim
    from_columns: [cs_ship_date_sk]
    to_columns: [d_date_sk]
    cardinality: N:1

  - name: catalog_sales_to_order_date
    from: catalog_sales
    to: date_dim
    from_columns: [cs_order_date_sk]
    to_columns: [d_date_sk]
    cardinality: N:1
```

---

## 5. Semantics

### 5.1 Condition Expression Language

The `condition` is a scalar SQL boolean expression using the same subset defined in `SQL_EXPRESSION_SUBSET.md`, subject to these constraints:

- All column references MUST be qualified — see §5.2 for the required qualification syntax
- Only columns from the `from` or `to` dataset may be referenced
- Aggregations are NOT allowed (conditions are row-level predicates)
- Subqueries are NOT allowed
- Parameters (`:param_name`) ARE allowed, enabling dynamic range joins (e.g., `promotion.p_start_date_sk >= :min_date_sk`)

When `from_columns`/`to_columns` are also present, the full join predicate is:

```
(from_col1 = to_col1 AND from_col2 = to_col2 AND ...) AND <condition>
```

The equi-keys are always listed first in generated SQL for optimizer hash-join awareness.

### 5.2 Self-Join Column Disambiguation

When `from` and `to` refer to the same dataset (a self-join), the dataset name alone is ambiguous — both sides share the same name. To resolve which column reference belongs to which side of the join, the `condition` field uses `from.` and `to.` as qualifiers instead of the dataset name:

| Context | Column Reference Syntax |
|:---|:---|
| `from` ≠ `to` (normal join) | `<dataset_name>.<column>` |
| `from` == `to` (self-join) | `from.<column>` and `to.<column>` |

**Self-join example:**

```yaml
- name: catalog_sales_cross_warehouse
  from: catalog_sales
  to: catalog_sales
  condition: "from.cs_order_number = to.cs_order_number AND from.cs_warehouse_sk <> to.cs_warehouse_sk"
  cardinality: N:N
```

The transpiler generates:
```sql
catalog_sales AS cs_from
JOIN catalog_sales AS cs_to
  ON cs_from.cs_order_number = cs_to.cs_order_number
 AND cs_from.cs_warehouse_sk <> cs_to.cs_warehouse_sk
```

The aliases (`cs_from`, `cs_to`) are generated by the transpiler using the relationship name as a seed; they are not exposed in the model syntax.

> **Validation rule:** If `from == to`, the parser MUST require `from.` / `to.` qualifier syntax for all column references in `condition`. Using the dataset name directly when `from == to` MUST be a validation error: "Self-join relationship `<name>` requires `from.<column>` / `to.<column>` syntax in `condition` to disambiguate sides."

For non-self-joins, `from.` and `to.` qualifiers are also accepted as an alternative to `<dataset_name>.<column>`, but the dataset-name form is preferred for clarity.

### 5.3 Cardinality and the Planner

The declared `cardinality` gates which algebra operations the planner may use:

| Cardinality | Allowed Algebra Operations | Notes |
|:---|:---|:---|
| `N:1` | `ExtendLOD`, `Enrich`, `AddDimensions`, `FilteringJoin` | Each `from` row gets ≤1 match; no explosion |
| `1:1` | All of `N:1` plus symmetric `Merge` | Symmetric — safe from either side |
| `N:N` | `FilteringJoin` ONLY | Row explosion would occur in any other context |

When `condition` is present and `cardinality` is absent, the parser MUST raise a validation error:

> "Non-equijoin relationship `<name>` requires a `cardinality` declaration. The engine cannot infer cardinality from an arbitrary predicate — see §3.2 of OSI_Proposal_Non_Equijoins.md."

When only `from_columns`/`to_columns` are present (no `condition`) and `cardinality` is absent, the planner falls back to key-based inference (current behaviour). If `cardinality` is declared, it overrides inference.

### 5.4 N:N Non-Equijoins

An `N:N` non-equijoin is a valid relationship but is **restricted to `FilteringJoin` (semi-join / anti-semi-join) operations only**. This is intentional and safe: semi-joins do not cause row explosion regardless of cardinality.

The main use case is existence-based filters:

```yaml
metrics:
  - name: multi_warehouse_order_count
    expression: COUNT(DISTINCT catalog_sales.cs_order_number)
    filter:
      expression: "EXISTS catalog_sales_cross_warehouse"
```

The N:N non-equijoin `catalog_sales_cross_warehouse` powers the semi-join that decides *which rows to keep*, but never adds columns or causes duplication.

> **Clarification:** N:N relationships are NOT allowed in `AddDimensions`. If a join would cause row explosion, no marker (`is_join_exploded`) can rescue the grain safety of the result. The restriction to `FilteringJoin` is absolute. Any attempt to use an N:N relationship in `ExtendLOD`, `Enrich`, or `AddDimensions` MUST raise a validation error.

### 5.5 Cardinality and Safety

**`N:1` non-equijoin safety:**
- Treated identically to a `N:1` equijoin for all algebra operations
- The planner produces LEFT JOIN by default (or INNER if `referential_integrity.from_all_rows_match: true`)
- Columns from the `to` side are marked `is_join_exploded = False`
- Scalar operations across the join are safe
- **Caveat:** Cardinality is author-declared and not schema-verified. If the data violates the declared cardinality (e.g., overlapping intervals produce multiple matches), the planner will produce incorrect results silently. Declare conservatively.

**`1:1` non-equijoin safety:**
- Treated identically to a `1:1` equijoin
- Symmetric — `Merge` is allowed from either side

**`N:N` non-equijoin safety:**
- Only `FilteringJoin` allowed (see §5.4)
- If incorrectly used in another context, raises a validation error

**Self-join safety:**
- Self-joins MUST declare `cardinality`. There is no structural basis for key inference on a self-join.
- The most common self-join pattern (inequality exclusion) is `N:N`. Only declare `N:1` or `1:1` if you have a domain-specific guarantee (e.g., a window function that ensures uniqueness of the self-join result).

---

## 6. Ergonomics

### 6.1 Aliased Dimension Joins

The TPC-DS pattern of joining `date_dim` multiple times under different roles (e.g., ship date, return date, order date) was previously inexpressible without workarounds. With multiple named relationships pointing to the same `to` dataset — distinguished by their `from_columns` — metrics can specify which path to use via `joins.path`:

```yaml
metrics:
  - name: shipped_revenue
    expression: SUM(catalog_sales.cs_net_paid)
    joins:
      path: [catalog_sales_to_ship_date]   # use the ship-date join path

  - name: ordered_revenue
    expression: SUM(catalog_sales.cs_net_paid)
    joins:
      path: [catalog_sales_to_order_date]  # use the order-date join path
```

Note: these aliased dimension relationships are plain **equijoins** (no `condition` needed). The schema change that enables them is simply allowing multiple named relationships between the same dataset pair — which this proposal formalizes via the `cardinality` field and the updated `name`-keyed graph representation.

> **Path disambiguation is required when multiple relationships exist between the same dataset pair.** When `joins.path` is not specified and multiple relationships connect the same pair, the planner MUST raise an ambiguity error rather than silently picking one.

### 6.2 Performance Warning for Range Joins

Range joins (non-equijoins with interval conditions) can be expensive in execution engines that do not have native range-join operators. The planner SHOULD emit a warning (not an error) when a non-equijoin relationship is used in a context that would generate an unconstrained nested-loop join — specifically when:
- There is no equi-key component (`from_columns`/`to_columns` absent), AND
- The engine does not support range-join acceleration (e.g., no interval tree index is available)

This warning is advisory; the query still executes.

---

## 7. Algebra Changes

**The algebra operations themselves are unchanged** — `ExtendLOD`, `Enrich`, `AddDimensions`, and `FilteringJoin` all accept `join_conditions` parameters that are treated as predicates internally. The changes are in the *validation layer*, *join condition representation*, and *graph layer*:

### 7.1 `JoinCondition` Type Extension

The current implicit `from_col = to_col` condition should be extended to a union type:

```
JoinCondition =
  | EquiJoin(from_col: str, to_col: str)
  | NonEquiExpression(sql_text: str, datasets_referenced: set[str])
```

A relationship's `from_columns`/`to_columns` produces `EquiJoin` conditions; `condition` produces a `NonEquiExpression`. When both are present, the full set is `[EquiJoin(...), ..., NonEquiExpression(...)]` — all AND'd together.

### 7.2 Cardinality Validation in `ExtendLOD` and `Enrich`

The existing rule "Ensure the other_table_state is a '1' side of either 1:1 or N:1" is updated to: check the **declared** cardinality of the relationship being traversed (falling back to key-based inference when `cardinality` is not declared on an equijoin). If the resolved cardinality is `N:N`, these operations MUST raise a validation error.

### 7.3 Self-Join Alias Generation

The SQL transpiler must detect when `from` and `to` are the same dataset and generate unique SQL aliases (e.g., `catalog_sales AS cs_from ... JOIN catalog_sales AS cs_to ...`). The `condition` expression's `from.`/`to.` qualifiers are rewritten to the appropriate alias pair.

### 7.4 Graph Layer: DiGraph → MultiDiGraph

**This is a breaking change to the graph layer.** The current implementation uses `nx.DiGraph`, which stores **exactly one edge** per directed pair `(u, v)`. Adding a second relationship between `catalog_sales → date_dim` silently overwrites the first edge's data.

The graph must be upgraded to `nx.MultiDiGraph` to support multiple named edges per pair. This has downstream effects:

- `find_join_path` must be updated — `nx.shortest_path` on a multigraph returns node paths, but `get_edge_data(u, v)` becomes ambiguous (multiple edges). The path resolver must select the edge by relationship name when `joins.path` is specified, or raise an ambiguity error when multiple edges exist and no path is given.
- `classify_join_type` and `detect_fan_trap` / `detect_chasm_trap` must be reviewed for multigraph correctness.
- The `_relationships_by_endpoints` index (which already stores lists) is compatible with this change.

### 7.5 Path Disambiguation in the Planner

The join path resolver (`joins.path`) must handle multiple relationships between the same pair of datasets. When the query specifies a `joins.path` by relationship name, the resolver should look up the relationship by name (via `_relationships_by_name`) rather than traversing the graph. Graph traversal (`shortest_path`) is used only when no explicit path is given — and must raise an ambiguity error when multiple relationships exist between the same pair.

---

## 8. Effect on Grain Calculations

The proposal claims grain calculation is largely "not affected" by non-equijoin relationships, and this is correct for most cases. However, there are three important nuances:

### 8.1 N:1 Non-Equijoins: Declared, Not Verified Cardinality

For equijoins, the planner *verifies* N:1 structurally using PK/UK metadata. For non-equijoins, declared `N:1` cardinality is **trusted, not verified**. The grain safety of scalar expressions across a non-equijoin N:1 relationship depends entirely on the author's declaration being correct.

If the author declares `cardinality: N:1` on a BETWEEN predicate but the data has overlapping intervals (so one `from` row matches multiple `to` rows), the planner will compute a scalar expression at the `from` grain — believing no explosion occurred — but the join will silently double rows. The grain is corrupted without any error.

**Spec text addition for §TABLE Grain Implementation Notes in `OSI_Core_Abstractions.md`:**
> For non-equijoin relationships, declared cardinality is author-asserted and not schema-verifiable. The TABLE grain algorithm treats `N:1` non-equijoin declarations as structurally equivalent to `N:1` equijoins for grain purposes, but the correctness guarantee is weaker — it relies on the data satisfying the declared cardinality.

### 8.2 Self-Join Grain Is a Special Case

The current TABLE grain algorithm uses join path traversal to find the "many-side of the finest-grained relationship." Self-join relationships (`from == to`) create a self-loop in the graph; the current `find_join_path` short-circuits on same-dataset paths and returns `[]` (no join needed).

For `FilteringJoin` (the only operation allowed on `N:N` self-joins), this is fine: the semi-join preserves the driving side's grain exactly, and the self-loop is never traversed for grain purposes.

For any hypothetical future use of `N:1` self-joins in enrichment contexts, the grain semantics would need to be defined explicitly. This proposal defers that to a future spec update. For now, self-join relationships should only be used in `FilteringJoin` contexts (which require `N:N`), and the grain algorithm is unaffected.

### 8.3 Aliased Dimension Joins and Grain Ambiguity

When multiple relationships exist between the same dataset pair (e.g., `catalog_sales → date_dim` via ship date and order date), the TABLE grain algorithm's path traversal may be ambiguous — it may pick either relationship when computing the grain of an expression that spans those datasets.

The fix is the same as for query planning: when `joins.path` is specified on a metric, grain inference uses that named path. When no path is given and multiple relationships exist between the same pair, grain inference MUST raise an ambiguity error rather than silently picking one.

**Practical implication:** Metrics that reference columns from an aliased dimension (e.g., `date_dim.d_year` via a ship-date join) MUST specify `joins.path` explicitly. The planner cannot infer which date role is intended.

---

## 9. TPC-DS Impact Analysis

### Queries Unblocked by Non-Equijoin Support

**Q16, Q94, Q95 — "Correlated EXISTS on same table with `<>`"**

These queries use the pattern:

```sql
-- Q16 / Q94 / Q95 (simplified)
SELECT cs_order_number, ...
FROM catalog_sales cs1
WHERE EXISTS (
  SELECT 1 FROM catalog_sales cs2
  WHERE cs1.cs_order_number = cs2.cs_order_number
    AND cs1.cs_warehouse_sk <> cs2.cs_warehouse_sk
)
```

With a named `N:N` self-referencing non-equijoin relationship on `catalog_sales`, this becomes expressible:

```yaml
relationships:
  - name: catalog_sales_cross_warehouse
    from: catalog_sales
    to: catalog_sales
    condition: "from.cs_order_number = to.cs_order_number AND from.cs_warehouse_sk <> to.cs_warehouse_sk"
    cardinality: N:N

metrics:
  - name: multi_warehouse_orders
    expression: COUNT(DISTINCT catalog_sales.cs_order_number)
    filter:
      expression: "EXISTS catalog_sales_cross_warehouse"
```

**Q17, Q25, Q29 — "3-way fact join with multiple `date_dim` aliases"**

These queries require joining `date_dim` under multiple roles (ship date, return date, order date). With multiple named equijoin relationships pointing to `date_dim` and `joins.path` disambiguation, these become expressible. The `condition` field is not needed — these are plain equijoins. The enabling change is the MultiDiGraph upgrade and path disambiguation.

### Summary

| Gap | Queries | Existing Settings | With Non-Equijoin Proposal |
|:---|:---|:---|:---|
| Non-equijoin self-join EXISTS | Q16, Q94, Q95 | ❌ Inexpressible | ✅ Named N:N self-join + FilteringJoin |
| Aliased dimension join | Q17, Q25, Q29 | ❌ Inexpressible | ✅ Multiple named equijoin relationships + joins.path |

---

## 10. Proposed Spec Changes

### 10.1 OSI_core_file_format.md

**Section: `## Relationships`**

Update the schema table to add:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `condition` | string | Conditional | Non-equijoin SQL predicate. Column refs must use `<dataset>.<column>` for normal joins or `from.<column>` / `to.<column>` for self-joins |
| `cardinality` | enum | Conditional | `N:1` (default), `1:1`, `N:N` — required when `condition` is present; optional override for equijoins |

Update **Important Notes** to add:
- `from_columns`/`to_columns` are required unless `condition` is present
- When `condition` is present alongside `from_columns`/`to_columns`, both predicates are AND'd
- `cardinality` is required when `condition` is present
- When `from == to` (self-join), `condition` must use `from.` / `to.` qualifier syntax

Add new **Non-Equijoin Example** subsection (see examples in §4).

### 10.2 OSI_Core_Abstractions.md

**Section: `### Joins`**

Add paragraph:

> **Non-Equijoins:** Relationships may include a `condition` field containing a SQL predicate that references both datasets. For normal joins, column references use `<dataset>.<column>`. For self-joins (`from == to`), column references use `from.<column>` and `to.<column>` to disambiguate sides. Non-equijoin relationships require a `cardinality` declaration (see §3.2 of the Non-Equijoin proposal). `N:1` and `1:1` non-equijoin relationships may be used in all aggregation join contexts. `N:N` non-equijoin relationships are restricted to `FilteringJoin` (semi-join / anti-semi-join) use only.

**Section: `#### TABLE Grain Implementation Notes`**

Add note:

> For non-equijoin relationships, declared cardinality is author-asserted and not verifiable from schema metadata. The grain algorithm treats declared `N:1` as structurally equivalent to an inferred `N:1` equijoin for grain purposes, but the correctness guarantee depends on the data satisfying the declaration. When multiple relationships exist between the same dataset pair, grain inference requires an explicit `joins.path` on the metric to avoid ambiguity.

**Section: `### Edge Cases and Validation Rules`**

Add rows:

| Condition | Handling |
|:---|:---|
| Non-equijoin `condition` without `cardinality` | Error — cardinality declaration required |
| `N:N` non-equijoin used in `ExtendLOD`, `Enrich`, or `AddDimensions` | Error — N:N relationships may only be used in FilteringJoin contexts |
| Multiple relationships between same dataset pair, no `joins.path` specified | Error — ambiguous join path; specify `joins.path` by relationship name |
| Self-join `condition` using `<dataset>.<column>` instead of `from.`/`to.` | Error — self-join conditions require `from.`/`to.` qualifier syntax |

### 10.3 OSI_Calc_Model_Semantics.md

**Section: `ExtendLOD`** and **`Enrich`**

Add validation bullet:

> - If the relationship being traversed has `cardinality: N:N` (declared or inferred), this operation MUST raise an error. Use `FilteringJoin` for N:N non-equijoin relationships.

---

## 11. Implementation Steps

1. **Schema parsing** — Add `condition` (optional string) and `cardinality` (optional enum) fields to the `Relationship` model in `models.py`. Update the validator: `from_columns`/`to_columns` are required unless `condition` is present; `cardinality` is required when `condition` is present. Relax the `columns_not_empty` validator to be conditional.

2. **Self-join validation** — Add parser check: when `from == to` and `condition` is present, validate that all column references in `condition` use `from.` or `to.` qualifiers. Raise a validation error if bare dataset-name references are found.

3. **Cardinality resolution** — Update `get_cardinality` / `classify_join_type` in `graph.py` to: (a) return the declared `cardinality` directly when it is present on the relationship, (b) fall back to key-based inference when `cardinality` is absent (equijoin only).

4. **Graph upgrade: DiGraph → MultiDiGraph** — Replace `nx.DiGraph` with `nx.MultiDiGraph`. Update `_add_relationship`, `find_join_path`, `get_relationship_metadata`, `classify_join_type`, `detect_fan_trap`, and `detect_chasm_trap` for multigraph correctness. `find_join_path` must accept an optional `relationship_name` parameter; when provided, it selects the named edge rather than the shortest-path result.

5. **Path disambiguation** — Update the LODPlanner's `_resolve_cross_table_deps` to raise an ambiguity error when multiple relationships exist between a pair and no `joins.path` is specified.

6. **`JoinCondition` extension** — Add `NonEquiExpression(sql_text: str)` to the `JoinCondition` union type. Update the algebra operations (`enrich`, `add_dimensions`, `filtering_join`) to accept and pass through `NonEquiExpression` conditions.

7. **Self-join alias generation in transpiler** — Detect self-join relationships and generate unique aliases (`<dataset>_from`, `<dataset>_to` or relationship-name-based). Rewrite `from.` / `to.` qualifiers in the `condition` to the generated aliases.

8. **N:N validation** — Add validation in `ExtendLOD`, `Enrich`, and `AddDimensions` to reject `N:N` relationships with a clear error message.

9. **TPC-DS model update** — Add the self-join relationships for Q16/Q94/Q95. Add the aliased `date_dim` relationships for Q17/Q25/Q29. Add `joins.path` on the affected metrics. Verify query output matches reference SQL.

10. **Tests** — Add test cases covering:
    - Non-equijoin N:1 range join — verify no explosion, correct scalar result
    - Non-equijoin N:N in FilteringJoin — verify semi-join semantics
    - N:N in Enrich — verify validation error
    - Self-join with `from.`/`to.` syntax — verify correct alias generation
    - Self-join with bare dataset name — verify validation error
    - Aliased dimension (same `to` dataset, two relationships) — verify path disambiguation
    - Declared `cardinality` overrides inferred cardinality for equijoins
    - Multiple relationships between same pair without `joins.path` — verify ambiguity error

---

## 12. Out of Scope

- **Data validation**: The spec does not validate that declared cardinality or RI is actually true in the data.
- **Automatic cardinality inference for non-equijoins**: The system will not inspect data distributions to infer whether a BETWEEN join is N:1 or N:N.
- **Dynamic conditions referencing other metrics**: The `condition` is a row-level predicate only. It may reference `:parameters` but not other metrics or LOD calculations.
- **Non-equijoin LOD composition**: LOD composition joins are mathematically determined; relationship `condition` does not affect them.
- **LATERAL joins and correlated subjoins**: Deferred to a future proposal.
- **Multiple `condition` expressions**: A relationship has exactly one `condition`. Combine multiple predicates with `AND`/`OR` within the single string.
- **N:N equijoins**: Declaring `cardinality: N:N` on a relationship that uses only `from_columns`/`to_columns` is valid (it overrides an inferred N:1), but unusual. A warning SHOULD be emitted if the `to_columns` reference the `to` dataset's declared `primary_key`, since that structurally guarantees 1-side cardinality regardless of the declaration.
