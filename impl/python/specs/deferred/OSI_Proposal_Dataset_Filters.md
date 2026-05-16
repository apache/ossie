# Proposal: Dataset-Level Filters

**Status:** Draft Proposal  
**Author:** will.pugh@snowflake.com  
**Date:** 2026-03-21  
**Related specs:**
- [OSI Core File Format](./OSI_core_file_format.md)
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md)
- [SQL Expression Subset](./SQL_EXPRESSION_SUBSET.md)

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Industry Survey](#2-industry-survey)
   - [2.7 ThoughtSpot — Table-Level RLS](#27-thoughtspot--table-level-rls)
   - [2.8 Summary](#28-summary)
3. [Design Alternatives](#3-design-alternatives)
   - [3.1 Option A: Always-Applied Filter (Single String)](#31-option-a-always-applied-filter-single-string)
   - [3.2 Option B: Named Segments (Opt-In)](#32-option-b-named-segments-opt-in)
   - [3.3 Option C: Hybrid (Always-Applied + Segments)](#33-option-c-hybrid-always-applied--segments)
   - [3.4 Option D: Unified List with Scope Enum](#34-option-d-unified-list-with-scope-enum)
   - [3.5 Option E: Always-Applied Filter with Scope Enum](#35-option-e-always-applied-filter-with-scope-enum)
   - [3.6 Decision](#36-decision)
4. [Design Principles](#4-design-principles)
5. [Proposed Schema Changes](#5-proposed-schema-changes)
6. [The Two-Stage Execution Model](#6-the-two-stage-execution-model)
7. [Semantics](#7-semantics)
   - [7.1 Dataset Filter Application](#71-dataset-filter-application)
   - [7.2 Cross-Dataset Filter Expressions](#72-cross-dataset-filter-expressions)
   - [7.3 Interaction with Metric Filters](#73-interaction-with-metric-filters)
   - [7.4 Interaction with LOD Grains](#74-interaction-with-lod-grains)
   - [7.5 Interaction with Self-Joins](#75-interaction-with-self-joins)
   - [7.6 Interaction with N:N Filtering Joins](#76-interaction-with-nn-filtering-joins)
   - [7.7 Pervasive Filter Propagation](#77-pervasive-filter-propagation)
8. [Validation Rules](#8-validation-rules)
9. [Ergonomics](#9-ergonomics)
   - [9.8 Pervasive Entitlement Pattern (Simplified)](#98-pervasive-entitlement-pattern-simplified)
   - [9.9 Dimension Security Pattern (Power BI Style)](#99-dimension-security-pattern-power-bi-style)
10. [Hypothetical Scenarios](#10-hypothetical-scenarios)
11. [Algebra Changes](#11-algebra-changes)
12. [Proposed Spec Changes](#12-proposed-spec-changes)
13. [Implementation Steps](#13-implementation-steps)
14. [Out of Scope](#14-out-of-scope)
15. [Industry Mapping](#15-industry-mapping)

---

## 1. Motivation

Today, OSI provides filters at two levels:

| Level | Mechanism | Controlled By |
|:---|:---|:---|
| **Metric** | `FilterSpec` on a metric (expression + query_filters mode) | Model author |
| **Query** | `filters` array in the LODQuery | Query consumer |

There is no way to attach a filter to a **dataset** itself. This creates several gaps:

1. **Data hygiene** — Soft-deleted rows (`is_deleted = true`), test data (`is_test = true`), or invalid records must be filtered in every metric or every query. Forgetting one filter silently corrupts results.

2. **Security / entitlements** — Row-level restrictions often depend on external tables. For example, an entitlement table defines which regions a user can see. Today there is no way to declare "this dataset is restricted to rows matching the user's entitlements" without trusting every metric and query to include the filter.

3. **Semantic clarity** — When a dataset represents a logical view of a physical table (e.g., "active orders" is a subset of the `orders` table), the filter belongs at the dataset level, not scattered across metrics.

4. **Consistency guarantee** — Without a dataset-level filter, there is no way to guarantee that all queries and all metrics see the same restricted view of the data. Each metric or query can independently forget the filter.

These gaps are solved in every major BI tool and semantic layer (see §2). This proposal introduces a **dataset filter** — an always-applied predicate that restricts rows whenever the dataset participates in a query. The filter expression supports cross-dataset references (e.g., `EXISTS_IN` against an entitlement table), enabling security patterns that require lookups against other datasets in the model.

---

## 2. Industry Survey

### 2.1 Tableau — Data Source Filters

Tableau provides **data source filters** that restrict data at the connection level before it reaches any visualization. Two variants exist as of 2025.1:

- **Pervasive filters** — applied to the entire tree of related logical tables. Every query against the data source includes this filter automatically. Users of published data sources cannot see or modify them.
- **Per-table (logical table) filters** — applied to a single logical table, equivalent to filtering the table before connecting it to other tables.

Filter expressions are scoped to a single table's columns. Cross-table predicates are not supported in data source filters.

### 2.2 Looker — LookML Explore Filters

Looker provides `sql_always_where` which injects an invisible, immutable WHERE clause into every query touching an Explore. Unlike Tableau, Looker **does support cross-table references** in `sql_always_where`:

```
explore: order {
  sql_always_where: ${customer.name} <> 'Altostrat Corporation' ;;
  join: customer {
    sql_on: ${order.customer_id} = ${customer.id} ;;
  }
}
```

When the filter references a joined view, Looker automatically ensures the join is included. This is the closest analog to the cross-dataset filter support proposed here.

### 2.3 Power BI — Row-Level Security (RLS)

Power BI uses DAX filter expressions attached to security roles. Filters are applied to dimension tables and **propagate to fact tables through relationships** automatically. This is inherently cross-table: a filter on `Region[AllowedRegion] = USERPRINCIPALNAME()` restricts the region table, and the restriction flows through relationships to restrict fact tables.

By default, Power BI RLS uses single-direction propagation: dimension table filters flow to fact tables through N:1 relationships. Bidirectional propagation (fact-to-dimension) is opt-in per relationship and discouraged for performance reasons. When multiple bidirectional relationships exist, only one may have bidirectional security enabled.

### 2.4 AtScale — Row Security Objects

AtScale provides configurable **scope** for security filters:

- **All** — every query, regardless of which tables are referenced.
- **Fact** — only queries that include metrics from the connected fact table.
- **Related** — only queries selecting dimensions with a direct path to the security object.

### 2.5 Cube.js — Segments

Cube.js segments are opt-in, single-table filters. No always-applied or cross-table support.

### 2.6 dbt / MetricFlow

No explicit dataset-level filter concept.

### 2.7 ThoughtSpot — Table-Level RLS

ThoughtSpot defines row-level security rules at the table level. RLS rules automatically propagate to all dependent objects — worksheets, answers, Liveboards, and searches that rely on the table's data. RLS cannot be defined on worksheets directly, only on their underlying tables. Administrators can optionally disable RLS on individual worksheets, though this is uncommon in production. Worksheet-level filters (distinct from RLS) are also supported for non-security use cases.

### 2.8 Summary

| Tool | Always-Applied | Cross-Table References | Scope |
|:---|:---|:---|:---|
| Tableau | Yes | No (single table only) | Both: pervasive (default) and per-table (2025.1+) |
| Looker | Yes (`sql_always_where`) | Yes (joined view refs) | Pervasive (Explore-level) |
| Power BI | Yes (RLS) | Yes (propagates through relationships) | Pervasive (dim→fact, single-direction default) |
| AtScale | Yes | Implicit (scope-based) | Configurable: All / Fact / Related |
| ThoughtSpot | Yes (table RLS) | Yes (propagates to dependents) | Pervasive (table→worksheet) |
| Cube.js | No | No | Per-table (opt-in only) |
| dbt/MetricFlow | No | No | N/A |

The most expressive tools (Looker, Power BI, ThoughtSpot) support cross-table references in always-applied filters. Critically, **every enterprise BI tool with dataset-level filters supports pervasive propagation** — filters on dimension tables automatically restrict connected fact tables. This is essential for security use cases where a single filter declaration must protect all downstream data. The only tools without pervasive support (Cube.js, dbt) also lack always-applied filters entirely.

---

## 3. Design Alternatives

### 3.1 Option A: Always-Applied Filter (Single String)

Add a single `filter` field to the Dataset model. The predicate is injected into every query touching the dataset.

```yaml
datasets:
  - name: orders
    source: schema.orders
    filter: "is_deleted = false AND order_date >= '2020-01-01'"
    fields: [...]
```

**Pros:**
- Simplest possible schema change (one optional string field).
- No query-format changes — transparent to consumers.
- Mirrors Tableau data source filters and Looker `sql_always_where`.

**Cons:**
- All-or-nothing: the filter is either on or off.

### 3.2 Option B: Named Segments (Opt-In)

Add a `segments` list to the Dataset model. Consumers reference segments by name in queries.

**Pros:**
- Reusable, composable, named filters.

**Cons:**
- No always-applied behavior — consumers must remember to include security filters.
- **Largely redundant with boolean dimension fields** — a model author can already define `is_active: "status = 'active'"` as a dimension field, and consumers can filter with `"filters": ["is_active"]`. Segments add a separate API surface for minimal benefit.

### 3.3 Option C: Hybrid (Always-Applied + Segments)

Combine an always-applied `filter` with optional `segments`.

**Cons:**
- **Segments are redundant** — boolean dimension fields already provide named, reusable, opt-in filter predicates without any spec changes.

### 3.4 Option D: Unified List with Scope Enum

A single `filters` list on the dataset, each entry with a `scope` controlling application.

**Cons:**
- Most complex schema.
- Same redundancy problem as Option C for the `scope: segment` entries.

### 3.5 Option E: Always-Applied Filter with Scope Enum

Extend Option A with an optional `scope` that controls filter propagation. The `filter` field accepts either a bare string (backward-compatible, equivalent to `scope: dataset`) or a structured object with `expression` and `scope`.

Three scope values:

| Scope | Propagation | Industry Analog |
|:---|:---|:---|
| `dataset` (default) | Restricts only the owning dataset. No propagation. | Tableau per-table filter, Cube.js segments |
| `pervasive` | Propagates transitively through all N:1 and 1:1 relationships where the filtered dataset is the one-side (`to_dataset`). | Power BI RLS, Tableau pervasive filter, ThoughtSpot RLS, Looker `sql_always_where` |
| `related` | Propagates to directly connected datasets only (one relationship hop). Not transitive. | AtScale "Related" scope |

```yaml
# Bare string — scope: dataset (backward compatible)
filter: "is_deleted = false"

# Structured form — explicit scope
filter:
  expression: "region = :allowed_region"
  scope: pervasive
```

**Pros:**
- Covers the full spectrum of industry filter behavior.
- Backward-compatible: bare string defaults to `scope: dataset`.
- Pervasive scope eliminates the need to manually add `EXISTS_IN` to every fact table when a dimension filter should restrict the entire star schema.
- `related` provides a middle ground for cases where full transitivity is too broad.

**Cons:**
- Slightly more complex schema than Option A.
- Pervasive propagation is implicit — model authors must understand relationship direction.

### 3.6 Decision

**Recommended: Option E (Always-Applied Filter with Scope Enum)**

The core gap in OSI is the absence of an **always-applied, immutable** dataset filter with configurable propagation scope. Every major enterprise BI tool supports pervasive propagation for security filters. OSI must provide a mapping target for these tools.

Option A (single string, per-table only) closes the data-hygiene gap but leaves a critical security gap: model authors must manually add `EXISTS_IN` filters to every fact table that should be restricted by a dimension filter. This is error-prone, verbose, and lacks the compositional guarantee that pervasive filters provide.

Option E extends Option A with minimal schema complexity while covering the full industry spectrum. The bare-string shorthand preserves backward compatibility with Option A. The "named, opt-in filter" use case (Options B, C, D) remains well-served by **boolean dimension fields**.

---

## 4. Design Principles

1. **Additive and backward-compatible** — The new `filter` field is optional. Existing models require no changes.
2. **Invisible to consumers** — Query authors do not see or interact with dataset filters. They are applied transparently.
3. **Filters compose with AND** — Dataset filters, metric filters, and query filters all compose via AND. No filter can broaden the result set beyond what another filter restricts.
4. **Expressions use the existing SQL subset** — Dataset filters use the same `SQL_EXPRESSION_SUBSET` as metric filters and query filters, including `EXISTS_IN` for cross-dataset references.
5. **Two-stage execution** — Dataset filters are resolved in Stage 1 (dataset materialization), before Stage 2 (query execution). See §6.
6. **No query-format changes** — The LODQuery schema is unchanged. Dataset filters are a model concern, not a query concern.

---

## 5. Proposed Schema Changes

Add an optional `filter` field to the Dataset schema:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `filter` | string \| object \| list | No | Dataset filter(s) — a SQL expression string, a structured object with `expression` and `scope`, or a list of either form. Multiple filters compose via AND; each filter item has its own scope. |

When `filter` is a **string**, it is equivalent to `{expression: <string>, scope: dataset}`.

When `filter` is an **object**, it has these fields:

| Field | Type | Required | Default | Description |
|:---|:---|:---|:---|:---|
| `expression` | string | Yes | — | SQL boolean expression |
| `scope` | enum | No | `dataset` | Propagation scope: `dataset`, `pervasive`, or `related` |

**Scope values:**

- **`dataset`** — The filter restricts only the owning dataset. No propagation. This is the default and the behavior described in the rest of this document for bare-string filters.
- **`pervasive`** — The filter restricts the owning dataset AND propagates transitively through all N:1 and 1:1 relationships where the filtered dataset is the one-side (`to_dataset`). Each receiving dataset gets an implicit `EXISTS_IN` filter using the relationship's join columns. See §7.7 for full propagation semantics.
- **`related`** — Like `pervasive`, but propagates only one relationship hop (not transitive).

**List syntax:** When `filter` is a **list**, each item is parsed independently as either a bare string (default `scope: dataset`) or a structured object. Multiple filter items compose via AND. Each item retains its own `scope`, enabling mixed-scope configurations:

```yaml
# Mixed-scope example: hygiene filter (dataset) + security filter (pervasive)
filter:
  - "is_deleted = false"
  - expression: "EXISTS_IN(region, user_entitlements.region)"
    scope: pervasive
```

In this example, `is_deleted = false` restricts only the owning dataset, while the `EXISTS_IN` filter propagates to connected fact tables. This eliminates the need to combine unrelated filter concerns into a single expression string and enables per-filter scope control.

The expression uses the model's `dialect` for syntax. It may reference:
- **Field names** defined in the owning dataset (single-table predicates). The filter expression references field names, not raw SQL column names. Each field name is resolved to its underlying `expression` before evaluation — the same resolution used for metric expressions and query filters.
- Fields from other datasets via `EXISTS_IN` / `NOT EXISTS_IN` (cross-dataset predicates, resolved via the model's relationship graph).

**Source type:** The `filter` applies regardless of whether the dataset's `source` is a table name or a subquery. When the source is a subquery, the filter applies to the subquery's result set (equivalent to wrapping the subquery and adding a WHERE clause).

**Single-table example:**

```yaml
datasets:
  - name: orders
    source: sales.orders
    primary_key: [order_id]
    filter: "is_deleted = false"
    fields:
      - name: order_id
        expression: order_id
        dimension: {}
      - name: is_deleted
        expression: is_deleted
        dimension: {}
      - name: amount
        expression: amount
```

**Cross-dataset example (entitlement):**

```yaml
datasets:
  - name: user_entitlements
    source: security.entitlements
    primary_key: [user_id, allowed_region]
    fields:
      - name: user_id
        expression: user_id
        dimension: {}
      - name: allowed_region
        expression: allowed_region
        dimension: {}

  - name: orders
    source: sales.orders
    primary_key: [order_id]
    filter: "EXISTS_IN(region, user_entitlements.allowed_region)"
    fields:
      - name: order_id
        expression: order_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}
      - name: amount
        expression: amount

relationships:
  - name: orders_to_entitlements
    from_dataset: orders
    to_dataset: user_entitlements
    from_columns: [region]
    to_columns: [allowed_region]
```

**Pervasive filter example (dimension security):**

```yaml
datasets:
  - name: customers
    source: customers
    primary_key: [customer_id]
    filter:
      expression: "segment = 'Enterprise'"
      scope: pervasive
    fields:
      - name: customer_id
        expression: customer_id
        dimension: {}
      - name: customer_name
        expression: customer_name
        dimension: {}
      - name: segment
        expression: segment
        dimension: {}

  - name: orders
    source: sales.orders
    primary_key: [order_id]
    fields:
      - name: order_id
        expression: order_id
        dimension: {}
      - name: customer_id
        expression: customer_id
        dimension: {}
      - name: amount
        expression: amount

relationships:
  - name: orders_to_customers
    from_dataset: orders
    to_dataset: customers
    from_columns: [customer_id]
    to_columns: [customer_id]
```

With `scope: pervasive`, the filter on `customers` automatically propagates to `orders` via the `orders_to_customers` relationship. The `orders` dataset receives an implicit filter equivalent to `EXISTS_IN(customer_id, customers.customer_id)`. No manual `EXISTS_IN` is needed on the fact table.

Compare with the explicit cross-dataset example above — pervasive scope automates what would otherwise require manual `EXISTS_IN` declarations on every connected fact table.

This is the only schema change. No changes to the LODQuery format, no new model types, no new enums.

---

## 6. The Two-Stage Execution Model

Query execution is logically divided into two stages:

```
┌─────────────────────────────────────────────────┐
│  Stage 1: Dataset Materialization                │
│                                                  │
│  For each dataset with a filter:                 │
│    1. Start with the physical table              │
│    2. Apply the dataset filter expression         │
│       - Single-table predicates: WHERE clause     │
│       - Cross-dataset predicates: resolve via     │
│         relationship graph (SEMI JOIN / EXISTS)   │
│    3. Result: the filtered row set for this       │
│       dataset                                     │
│                                                  │
│  Datasets without filters pass through unchanged  │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│  Stage 2: Query Execution                        │
│                                                  │
│  Operates on the filtered datasets from Stage 1: │
│    1. Join resolution (relationships)             │
│    2. Query-level filters                         │
│    3. Aggregation and grain resolution            │
│    4. Metric-level filters (FilterSpec)           │
│    5. Window functions, ORDER BY, LIMIT           │
└─────────────────────────────────────────────────┘
```

**Key property:** Stage 1 is invisible to Stage 2. By the time query execution begins, each dataset is already its filtered row set. Metrics, query filters, and joins operate on the restricted data without knowledge of the dataset filter.

**Key property:** Cross-dataset filter expressions in Stage 1 do not affect the cardinality or grain of the dataset. An `EXISTS_IN` filter produces a subset of the original rows — it never adds columns or duplicates rows. The dataset's primary key and field structure are unchanged.

**Parameter binding:** Parameters (`:param_name` references) are bound to their supplied values before Stage 1 begins. Parameter resolution is the very first step — by the time any dataset filter is evaluated, all parameter placeholders have been replaced with concrete values.

**Pervasive filter expansion:** Before Stage 1 begins (but after parameter binding), `pervasive` and `related` filters are expanded into concrete `EXISTS_IN` filters on connected datasets. This is a **model-level rewrite** — the implementation walks the relationship graph and generates implicit dataset filters on receiving datasets. After expansion, all filters have `scope: dataset` and the rest of the pipeline (Stage 1 and Stage 2) operates without knowledge of the original scope. See §7.7 for the expansion algorithm.

The expansion order:
1. Parameter binding (`:param_name` → literal values).
2. Pervasive filter expansion (generate implicit `EXISTS_IN` on connected datasets).
3. Stage 1: Dataset materialization (apply all filters, now all `scope: dataset`).
4. Stage 2: Query execution.

**Expansion ordering is immaterial.** The spec prescribes parameter binding before pervasive expansion, but implementations may expand pervasive filters eagerly (e.g., at parse time) provided the observable results are equivalent. This is valid because expansion generates `EXISTS_IN` references to datasets **by name** — the actual filter on the referenced dataset is resolved lazily at plan time, after parameters have been bound. A pervasive filter with a parameter reference (e.g., `user_id = :user_id` with `scope: pervasive`) works correctly regardless of expansion ordering: the expanded `EXISTS_IN` on connected datasets triggers lazy resolution of the source dataset, which by plan time has its parameters bound. This parallels Tableau's Initial SQL parameter pattern where parameters are available at query execution time regardless of definition order.

**Logical model:** The two-stage description is a **logical** model for reasoning about correctness. Implementations are free to merge, reorder, or push down operations (e.g., inlining dataset filters into the main query plan) as long as the observable results are equivalent to executing Stage 1 fully before Stage 2. This is the standard "as-if" rule: any optimization is valid if it produces the same result set.

---

## 7. Semantics

### 7.1 Dataset Filter Application

When a dataset has a `filter`, the predicate is resolved in Stage 1. The result is a filtered row set that replaces the physical table for all subsequent operations.

For **single-table predicates**, this is equivalent to:

```sql
SELECT * FROM physical_table WHERE <predicate>
```

For **cross-dataset predicates** using `EXISTS_IN`, this is equivalent to:

```sql
SELECT * FROM physical_table p
WHERE EXISTS (
    SELECT 1 FROM <remote_table> r
    WHERE p.<local_column> = r.<remote_column>
)
```

For **cross-dataset predicates** using `NOT EXISTS_IN`, this is equivalent to:

```sql
SELECT * FROM physical_table p
WHERE NOT EXISTS (
    SELECT 1 FROM <remote_table> r
    WHERE p.<local_column> = r.<remote_column>
)
```

**NULL safety:** `NOT EXISTS_IN` uses anti-semi-join (NOT EXISTS) semantics, not `NOT IN`. This is critical: `NOT IN` returns zero rows if the subquery contains any NULL value, while `NOT EXISTS` correctly excludes only rows with actual matches. Implementations must use NULL-safe anti-semi-join regardless of the SQL pattern emitted.

Filter expressions reference **field names** (the `name:` key), not raw column names. Each field name is resolved to its underlying `expression` before evaluation. For example, a field `name: is_active` with `expression: "status = 'active'"` can be referenced in a filter as `is_active = true`, which resolves to `(status = 'active') = true`.

This applies regardless of how the dataset participates in Stage 2:

- As the root (anchor) dataset.
- As the `from` side of a relationship (many-side).
- As the `to` side of a relationship (one-side / dimension).
- As the target of a FilteringJoin (SEMI / ANTI_SEMI).

**Dimension table filters and join types:** A dataset filter on a dimension table (the `to` side of a relationship) must not change the join type used in Stage 2. The standard LEFT JOIN behavior (preserving fact-side rows) ensures that a dimension filter restricts the dimension table only, not the fact table. If an implementation used INNER JOIN for a filtered dimension table, the filter would implicitly propagate to the fact table — violating the "filters do not propagate" principle (§7.2). Orders for customers that fail the dimension filter receive NULL dimension values from the LEFT JOIN rather than being dropped.

**Implementation warning — CTE optimization:** Dataset filters on dimension tables (the right side of LEFT JOINs) must NOT be hoisted into the outer query's WHERE clause by CTE optimization passes. Doing so converts the NULL-preserving LEFT JOIN into an effective INNER JOIN, silently dropping fact rows. Implementations using CTE optimization must preserve the LEFT JOIN semantics by keeping the filter on the dimension subquery. Similarly, EXISTS_IN subqueries used as dataset filters must not be "un-nested" by optimizers that fold WHERE conditions across CTE boundaries.

**Field name resolution examples:**

```
filter: "is_active"  →  WHERE (status = 'active')
filter: "is_active AND region = 'US'"  →  WHERE (status = 'active') AND region = 'US'
```

### 7.2 Cross-Dataset Filter Expressions

Dataset filters support the same expression language as query-level filters, including:

- **Simple predicates:** `is_deleted = false`, `status != 'cancelled'`
- **Compound predicates:** `is_deleted = false AND region IN ('US', 'EU')`
- **Parameter references:** `tenant_id = :tenant_id`
- **EXISTS_IN (semi-join):** `EXISTS_IN(region, entitlements.allowed_region)` — keep rows where the local column has a match in the remote dataset.
- **NOT EXISTS_IN (anti-semi-join):** `NOT EXISTS_IN(customer_id, blacklist.customer_id)` — exclude rows where the local column matches the remote dataset.

Cross-dataset references are resolved via the model's relationship graph. The referenced dataset must be reachable via a declared relationship (direct or multi-hop). Although `EXISTS_IN(col_a, other.col_b)` already specifies the join columns, the relationship requirement serves as a **model integrity constraint**: it ensures the model author has explicitly declared that a semantic relationship exists between the two datasets. Without this requirement, a filter could reference any dataset, bypassing the model's declared data topology.

**Cross-dataset filter expressions do not propagate.** A filter on `orders` that references `entitlements` via `EXISTS_IN` does not restrict the `entitlements` dataset itself. Each dataset's filter expression is applied only to the owning dataset. However, the `scope` attribute (§7.7) controls whether the filter's **effect** propagates to other datasets through the relationship graph. With `scope: dataset` (default), no propagation occurs. With `scope: pervasive` or `scope: related`, implicit `EXISTS_IN` filters are generated on connected datasets during the expansion phase.

### 7.3 Interaction with Metric Filters

Metric-level `FilterSpec` is applied in Stage 2, **after** dataset filters. The precedence is:

1. Dataset filter restricts the base rows (Stage 1).
2. The metric's `FilterSpec.expression` is applied to the already-restricted rows (Stage 2).
3. `FilterSpec.query_filters` controls whether query-level WHERE is also applied.

A metric with `query_filters: EXCLUDE` still respects dataset filters. `query_filters: EXCLUDE` only excludes the **query-level** filters — it cannot override dataset-level restrictions. This is important for security: a dataset filter for entitlements cannot be bypassed by a metric that excludes query filters.

### 7.4 Interaction with LOD Grains

Dataset filters are applied in Stage 1, **before** grain resolution. For LOD metrics (FIXED, INCLUDE, EXCLUDE), the dataset filter is already applied before the sub-query that computes the metric at its specified grain.

Example: If `orders` has `filter: "status != 'cancelled'"` and a metric uses `FIXED[customer_id] SUM(amount)`, the FIXED computation only sees non-cancelled orders.

### 7.5 Interaction with Self-Joins

When a dataset is self-joined (e.g., `employees` joined to itself via `manager_id`), the dataset filter is applied to **both instances** of the table. Each alias of the self-joined table receives the filter independently in Stage 1.

### 7.6 Interaction with N:N Filtering Joins

For N:N relationships used in FilteringJoin (SEMI / ANTI_SEMI), dataset filters on both sides are applied in Stage 1, before the existence check in Stage 2.

### 7.7 Pervasive Filter Propagation

When a dataset has `scope: pervasive`, the filter propagates through the relationship graph to restrict connected datasets. The propagation follows these rules:

**Direction:** A pervasive filter propagates from the filtered dataset to datasets that join TO it — i.e., datasets where `to_dataset` is the filtered dataset. In a star schema, this means dimension filters propagate to fact tables.

```
Filtered dimension (to_dataset) ← N:1 ← Fact table (receives implicit filter)
```

**Cardinality gate:** Propagation only occurs through N:1 and 1:1 relationships (inferred from the relationship graph). N:N relationships do NOT propagate pervasive filters. N:N filtering requires explicit `EXISTS_IN` in the filter expression.

**Transitivity:** For `scope: pervasive`, propagation is transitive. If `regions` has a pervasive filter and `customers` joins to `regions`, and `orders` joins to `customers`, then both `customers` and `orders` receive implicit filters. For `scope: related`, propagation is one hop only — `customers` receives the filter but `orders` does not.

**Expansion algorithm:**

1. Collect all datasets with `scope: pervasive` or `scope: related`.
2. For each such dataset D with filter expression F:
   a. Find all relationships where `to_dataset = D` and the relationship is N:1 or 1:1.
   b. For each such relationship R with `from_dataset = T`:
      - Generate an implicit filter on T: `EXISTS_IN(<R.from_columns>, <D.name>.<R.to_columns>)`.
      - If T already has a filter, AND-compose: the existing filter and the new `EXISTS_IN` are both applied.
      - For `scope: pervasive`: recursively propagate — T now acts as if it has a pervasive filter for this predicate, so datasets joining to T also receive implicit filters (using T's join columns, not D's).
      - For `scope: related`: stop after one hop.
3. After expansion, set all filter scopes to `dataset`.
4. Run circular dependency validation on the expanded filter set.

**Transitive propagation detail:** When a pervasive filter on D propagates to T, the implicit filter on T is `EXISTS_IN(from_col, D.to_col)`. If T then transitively propagates to U (because U joins to T via another N:1 relationship), the implicit filter on U is `EXISTS_IN(U_from_col, T.T_to_col)`. Each hop uses its own relationship's join columns. The chain is: U sees only rows that match T, and T sees only rows that match D.

**Recursive filter chain guarantee.** When dataset A's filter references dataset B via `EXISTS_IN`, and B itself has a filter (either authored or injected by pervasive expansion), B's filter is applied recursively before evaluating A's semi-join. This recursion is guaranteed to terminate because the dependency graph is validated as acyclic (§8). Chains of arbitrary depth are supported — for example, A references B, B references C, and C has a simple predicate filter. Implementations may resolve chains via explicit topological sort or via recursive application during plan construction; both strategies produce identical results given the acyclic guarantee.

**Example — star schema:**

```yaml
# customers has pervasive filter: segment = 'Enterprise'
# orders joins to customers via customer_id
# After expansion:
#   customers.filter = "segment = 'Enterprise'" (scope: dataset)
#   orders.filter = "EXISTS_IN(customer_id, customers.customer_id)" (implicit, scope: dataset)
```

**Example — snowflake schema (transitive):**

```yaml
# regions has pervasive filter: continent = 'Europe'
# customers joins to regions via region_id (N:1)
# orders joins to customers via customer_id (N:1)
# After expansion:
#   regions.filter = "continent = 'Europe'" (scope: dataset)
#   customers.filter = "EXISTS_IN(region_id, regions.region_id)" (implicit)
#   orders.filter = "EXISTS_IN(customer_id, customers.customer_id)" (implicit)
```

**Interaction with existing filters:** If the receiving dataset already has a filter, the implicit `EXISTS_IN` composes via AND. For example, if `orders` has `filter: "status != 'cancelled'"` and receives an implicit `EXISTS_IN(customer_id, customers.customer_id)` from a pervasive filter on `customers`, the effective filter on `orders` is `status != 'cancelled' AND EXISTS_IN(customer_id, customers.customer_id)`.

**Interaction with `scope: dataset` dimension filters (§9.6 clarification):** Section 9.6 describes `scope: dataset` dimension filters producing NULL groups via LEFT JOIN. With `scope: pervasive`, the dimension filter ALSO restricts fact tables. This is the fundamental difference: `scope: dataset` on a dimension = "restrict the lookup, fact rows get NULLs." `scope: pervasive` on a dimension = "restrict the lookup AND exclude fact rows that don't match."

---

## 8. Validation Rules

| Rule | Error |
|:---|:---|
| `filter` expression must be non-empty and non-whitespace when present | "Dataset filter expression cannot be empty" |
| For single-table predicates: fields must exist in the dataset | "Dataset filter references unknown field 'X' in dataset 'Y'" |
| For `EXISTS_IN` / `NOT EXISTS_IN`: referenced dataset must exist | "Dataset filter references unknown dataset 'X'" |
| For `EXISTS_IN` / `NOT EXISTS_IN`: referenced dataset must be reachable via relationship graph | "Dataset filter references unreachable dataset 'X' from dataset 'Y'" |
| Expression must be a valid SQL boolean per `SQL_EXPRESSION_SUBSET` | Standard expression parse error |
| Dataset filter must not create circular dependencies | "Circular dataset filter dependency: A → B → ... → A" |
| `scope` must be one of `dataset`, `pervasive`, `related` when present | "Invalid filter scope 'X'. Must be one of: dataset, pervasive, related" |
| After pervasive expansion, the combined filter dependency graph must be acyclic | Same circular dependency error as above, but detected on the expanded graph |
| Pervasive filter expansion must not produce ambiguous paths | "Ambiguous pervasive propagation path from 'X' to 'Y': multiple N:1/1:1 relationships connect them. Specify an explicit `EXISTS_IN` filter on 'Y' instead." |

The **empty/whitespace** rule: a `filter` field that is present but contains only whitespace (e.g., `filter: "  "`) is treated as a validation error, not as "no filter." Implementations should trim the value and reject it if the result is empty.

The **circular dependency** rule is critical. If dataset A's filter references dataset B, and dataset B's filter references dataset A, neither can be materialized first. The dependency graph of cross-dataset filters must be a DAG. Cycles of any length are detected via topological sort and reported with the full cycle path. Examples:

- Two-node cycle: `"Circular dataset filter dependency: orders → customers → orders"`
- Three-node cycle: `"Circular dataset filter dependency: orders → customers → entitlements → orders"`

The **pervasive expansion validation** rule: after expanding pervasive and related filters into concrete `EXISTS_IN` filters, the combined dependency graph is re-checked for cycles. A pervasive filter on dataset A that propagates to dataset B, combined with an explicit filter on B that references A, creates a cycle that is only detectable after expansion. Implementations must validate the expanded graph, not just the authored filters.

The **ambiguous path** rule: when a pervasive or related filter on dataset D would propagate to dataset T, but multiple N:1/1:1 relationships connect T to D (e.g., role-playing dimensions where `orders` joins to `date_dim` via both `order_date_id` and `ship_date_id`), the implementation cannot determine which join columns to use for the implicit `EXISTS_IN`. This is a compile-time error. The model author must use an explicit `EXISTS_IN` filter on T instead, specifying the intended join columns. This mirrors the role-playing dimension disambiguation pattern used in cross-table joins.

**Warning (non-fatal):** A pervasive or related filter on a dataset with no incoming N:1/1:1 relationships has no propagation targets. Implementations should emit a warning: "Pervasive filter on 'X' has no propagation targets — no datasets join to 'X' via N:1 or 1:1 relationships. The filter will only restrict 'X' itself." This is a warning, not an error, because the filter still restricts its own dataset.

---

## 9. Ergonomics

### 9.1 Soft-Delete / Data Hygiene Pattern

The most common use case: exclude soft-deleted or test records.

```yaml
datasets:
  - name: orders
    source: sales.orders
    filter: "is_deleted = false AND is_test = false"
    fields: [...]
```

Every query touching `orders` automatically excludes deleted and test rows. No metric or query needs to remember the filter.

### 9.2 Entitlement Table Pattern

Restrict a dataset based on an external entitlement table:

```yaml
parameters:
  - name: user_id
    type: INTEGER

datasets:
  - name: user_entitlements
    source: security.user_region_entitlements
    primary_key: [user_id, region]
    filter: "user_id = :user_id"
    fields:
      - name: user_id
        expression: user_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}

  - name: orders
    source: sales.orders
    primary_key: [order_id]
    filter: "EXISTS_IN(region, user_entitlements.region)"
    fields:
      - name: order_id
        expression: order_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}
      - name: amount
        expression: amount

relationships:
  - name: orders_to_entitlements
    from_dataset: orders
    to_dataset: user_entitlements
    from_columns: [region]
    to_columns: [region]
```

**Execution flow:**
1. Stage 1: `user_entitlements` is filtered to the current user's rows (`user_id = :user_id`).
2. Stage 1: `orders` is filtered to only regions where the current user has an entitlement (`EXISTS_IN`).
3. Stage 2: All queries and metrics see only entitled orders. No query can bypass the restriction.

Note the **dependency ordering**: `orders` depends on `user_entitlements`, so `user_entitlements` must be materialized first. This is enforced by the DAG validation rule (§8).

### 9.3 Multi-Tenant / Row-Level Security Pattern

Restrict a dataset to a specific tenant using a parameter:

```yaml
parameters:
  - name: tenant_id
    type: INTEGER

datasets:
  - name: orders
    source: sales.orders
    filter: "tenant_id = :tenant_id"
    fields: [...]
```

### 9.4 Blacklist / Exclusion Pattern

Exclude rows that appear in a blacklist table:

```yaml
datasets:
  - name: blacklisted_customers
    source: compliance.blacklist
    primary_key: [customer_id]
    fields:
      - name: customer_id
        expression: customer_id
        dimension: {}

  - name: orders
    source: sales.orders
    primary_key: [order_id]
    filter: "NOT EXISTS_IN(customer_id, blacklisted_customers.customer_id)"
    fields: [...]

relationships:
  - name: orders_to_blacklist
    from_dataset: orders
    to_dataset: blacklisted_customers
    from_columns: [customer_id]
    to_columns: [customer_id]
```

### 9.5 Logical View Pattern

Use a dataset filter to define a logical subset of a physical table:

```yaml
datasets:
  - name: completed_orders
    source: sales.orders
    filter: "status = 'completed'"
    primary_key: [order_id]
    fields: [...]

  - name: all_orders
    source: sales.orders
    primary_key: [order_id]
    fields: [...]
```

### 9.6 Dimension Filter vs. Fact Filter — Choosing the Right Level

A common source of confusion: should the filter go on the dimension table or the fact table?

**Dimension filter** — restricts the dimension table only. Fact rows for non-matching dimension values get NULL dimension attributes (via LEFT JOIN) but are **not excluded**. Use this when the dimension table itself is the entity being restricted (e.g., "only show active customers in customer reports"), and you accept that fact rows for excluded dimension values still contribute to aggregations under a NULL group.

```yaml
# "Active customers" dimension — orders for inactive customers still appear (with NULL customer_name)
datasets:
  - name: customers
    filter: "is_active = true"
```

**Fact filter** — restricts the fact table directly. Fact rows are excluded before any joins. Use this when you want to guarantee that excluded rows never contribute to any metric.

```yaml
# Only enterprise customer orders — non-enterprise orders are completely excluded
datasets:
  - name: orders
    filter: "EXISTS_IN(customer_id, enterprise_customers.customer_id)"
```

**Rule of thumb:** If the intent is "these rows should never appear in any result," filter the fact table (or use `EXISTS_IN` on the fact table referencing a dimension condition). If the intent is "restrict the lookup table used for dimension attributes," filter the dimension table and accept the NULL group.

### 9.7 Opt-In Named Filters (Existing Feature)

For consumers who want reusable, opt-in named filters, boolean dimension fields already work:

```yaml
fields:
  - name: is_high_value
    expression: "amount >= 500"
    dimension: {}
```

Consumers compose these in query filters: `"filters": ["is_high_value"]`.

### 9.8 Pervasive Entitlement Pattern (Simplified)

Compare with §9.2 — the pervasive scope eliminates the need for manual `EXISTS_IN` on every fact table:

```yaml
parameters:
  - name: user_id
    type: INTEGER

datasets:
  - name: user_entitlements
    source: security.user_region_entitlements
    primary_key: [user_id, region]
    filter:
      expression: "user_id = :user_id"
      scope: pervasive
    fields:
      - name: user_id
        expression: user_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}

  - name: orders
    source: sales.orders
    primary_key: [order_id]
    fields:
      - name: order_id
        expression: order_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}
      - name: amount
        expression: amount

  - name: returns
    source: sales.returns
    primary_key: [return_id]
    fields:
      - name: return_id
        expression: return_id
        dimension: {}
      - name: region
        expression: region
        dimension: {}
      - name: refund_amount
        expression: refund_amount

relationships:
  - name: orders_to_entitlements
    from_dataset: orders
    to_dataset: user_entitlements
    from_columns: [region]
    to_columns: [region]

  - name: returns_to_entitlements
    from_dataset: returns
    to_dataset: user_entitlements
    from_columns: [region]
    to_columns: [region]
```

With `scope: pervasive`, BOTH `orders` and `returns` are automatically restricted to entitled regions. Without pervasive scope, the model author would need to add `EXISTS_IN(region, user_entitlements.region)` to each fact table individually — and remember to add it to every new fact table that joins to entitlements in the future.

### 9.9 Dimension Security Pattern (Power BI Style)

The most common enterprise pattern: restrict a dimension table and let the restriction cascade to all fact tables.

```yaml
datasets:
  - name: regions
    source: geo.regions
    primary_key: [region_id]
    filter:
      expression: "continent = 'Europe'"
      scope: pervasive
    fields:
      - name: region_id
        expression: region_id
        dimension: {}
      - name: region_name
        expression: region_name
        dimension: {}
      - name: continent
        expression: continent
        dimension: {}
```

Every fact table that joins to `regions` (directly or transitively through other dimensions) is automatically restricted to European regions. This mirrors Power BI's RLS model where a DAX filter on a dimension table cascades through the star/snowflake schema.

---

## 10. Hypothetical Scenarios

These scenarios validate the two-stage model against edge cases.

### 10.1 Dataset filter + query filter on the same field

**Model:** `orders` has `filter: "status != 'cancelled'"`.  
**Query:** `"filters": ["status = 'completed'"]`.

**Stage 1:** `orders` is filtered to non-cancelled rows (completed + pending).  
**Stage 2:** Query filter further restricts to completed only.  
**Result:** Only completed orders. The two filters compose via AND. Correct.

### 10.2 Dataset filter + metric with `query_filters: EXCLUDE`

**Model:** `orders` has `filter: "region = 'US'"`. Metric `global_revenue` has `query_filters: EXCLUDE`.  
**Query:** `"filters": ["status = 'completed'"]` with measures `[us_revenue, global_revenue]`.

**Stage 1:** `orders` is filtered to US only.  
**Stage 2:** `us_revenue` sees US rows with `status = 'completed'` (query filter applied). `global_revenue` sees US rows without the query filter (EXCLUDE). But both see only US rows because the dataset filter was applied in Stage 1.  
**Result:** `global_revenue` is the grand total of US orders (not all orders). The dataset filter cannot be bypassed. Correct — this is the security guarantee.

### 10.3 Dataset filter on dimension table with LEFT JOIN

**Model:** `customers` has `filter: "is_active = true"`. `orders` has no filter.  
**Query:** SUM(order_total) by customer_name.

**Stage 1:** `customers` is filtered to active customers only.  
**Stage 2:** `orders` LEFT JOINs to filtered `customers`. Orders for inactive customers get NULL `customer_name`.  
**Result:** Revenue grouped by customer name, with a NULL group for orders belonging to inactive customers. The dataset filter does not propagate to `orders` — it restricts the dimension table only. Correct.

### 10.4 Cross-dataset filter with entitlement table

**Model:** `user_entitlements` has `filter: "user_id = :user_id"`. `orders` has `filter: "EXISTS_IN(region, user_entitlements.region)"`.  
**Query:** SUM(amount) by product.

**Stage 1:** `user_entitlements` materialized first (it has a single-table filter). Then `orders` is filtered using the materialized entitlement rows.  
**Stage 2:** Query runs against the restricted `orders`.  
**Result:** Only orders in entitled regions, grouped by product. Correct.

### 10.5 Circular dependency (should fail validation)

**Model:** `orders` has `filter: "EXISTS_IN(customer_id, customers.customer_id)"`. `customers` has `filter: "EXISTS_IN(customer_id, orders.customer_id)"`.

**Validation:** Circular dependency detected: orders → customers → orders. Model is rejected. Correct.

### 10.6 FIXED grain metric with dataset filter

**Model:** `orders` has `filter: "status != 'cancelled'"`. Metric `customer_total` uses `FIXED[customer_id] SUM(amount)`.  
**Query:** dimensions: [product], measures: [revenue, customer_total].

**Stage 1:** `orders` excludes cancelled rows.  
**Stage 2:** `revenue` is SUM(amount) at QUERY grain (by product). `customer_total` is SUM(amount) at FIXED[customer_id] grain. Both operate on the non-cancelled row set from Stage 1.  
**Result:** Both metrics see the same filtered data. The FIXED computation correctly excludes cancelled orders. Correct.

### 10.7 Dataset filter on both sides of a self-join

**Model:** `employees` has `filter: "salary >= 90000"`. Self-join via `manager_id`.  
**Query:** employee names with their manager names.

**Stage 1:** `employees` filtered to salary >= 90000. This applies to both the "from" instance (employee) and the "to" instance (manager).  
**Stage 2:** Self-join between filtered employee and filtered manager.  
**Result:** Only high-salary employees appear. Their managers also only show if they have salary >= 90000 (otherwise NULL from LEFT JOIN). Correct.

### 10.8 Dataset filter on fact table + dataset filter on dimension table

**Model:** `orders` has `filter: "status != 'cancelled'"`. `customers` has `filter: "segment = 'Enterprise'"`.  
**Query:** SUM(order_total) by customer_name.

**Stage 1:** `orders` excludes cancelled. `customers` filters to Enterprise only.  
**Stage 2:** LEFT JOIN. Non-cancelled orders for non-Enterprise customers get NULL `customer_name`.  
**Result:** Revenue by Enterprise customer name, plus a NULL group for non-Enterprise customer orders. Both filters are independent. Correct.

### 10.9 Cross-dataset filter where referenced dataset also has a filter

**Model:** `entitlements` has `filter: "is_active = true"`. `orders` has `filter: "EXISTS_IN(region, entitlements.allowed_region)"`.

**Stage 1:** `entitlements` is materialized first (single-table filter: `is_active = true`). Then `orders` is filtered against the materialized (active-only) entitlements.  
**Result:** Orders are restricted to regions from active entitlements only. Inactive entitlements are excluded. The dependency ordering handles this correctly. Correct.

### 10.10 Dataset filter with NOT EXISTS_IN (blacklist)

**Model:** `blacklist` has no filter. `orders` has `filter: "NOT EXISTS_IN(customer_id, blacklist.customer_id)"`.  
**Query:** SUM(amount) by region.

**Stage 1:** `blacklist` passes through (no filter). `orders` is filtered to exclude any customer_id that appears in the blacklist.  
**Stage 2:** Query aggregates the non-blacklisted orders.  
**Result:** Revenue by region, excluding blacklisted customers. Correct.

---

## 11. Algebra Changes

### 11.1 Source Node Enhancement

The `Source` algebra operation must accept the dataset's filter. For single-table predicates, this is a `Filtering` step. For cross-dataset predicates, this is a `FilteringJoin` (SEMI or ANTI_SEMI).

```
# Single-table filter
Filtering(Source(orders), "is_deleted = false")

# Cross-dataset filter (EXISTS_IN)
FilteringJoin(
  Source(orders),
  Source(user_entitlements),   # already filtered by its own filter
  SEMI,
  ON orders.region = user_entitlements.region
)
```

### 11.2 Dependency Ordering

When building the Stage 1 plan, datasets must be materialized in dependency order. A topological sort of the cross-dataset filter dependency graph determines the order.

### 11.3 No New Algebra Operations

Dataset filters reuse existing algebra operations (`Filtering`, `FilteringJoin`). No new operations are introduced.

---

## 12. Proposed Spec Changes

### 12.1 OSI_core_file_format.md

**Dataset schema:** Add `filter` (optional string) to the dataset definition table.

**New subsection:** "Dataset Filters" explaining the feature, the two-stage model, and examples including cross-dataset filters.

### 12.2 OSI_Core_Abstractions.md

**Filters section:** Add "Dataset-Level Filters" as Stage 1 in the execution model. Update the filter composition rules.

### 12.3 OSI_Calc_Model_Semantics.md

**Filter application in the calculation model:** Specify that dataset filters are resolved before any Stage 2 operations (grain resolution, aggregation, join traversal).

---

## 13. Implementation Steps

1. **Schema models** — Add `FilterScope` enum and `DatasetFilter` model to `models.py`. Change `Dataset.filter` to accept `str | DatasetFilter | None` with bare-string normalization.
2. **Parser** — Parse `filter` from YAML. Add source-location tracking.
3. **Pervasive expansion** — Implement `expand_pervasive_filters()` to walk the relationship graph and generate implicit `EXISTS_IN` filters on connected datasets. Run after parameter binding, before Stage 1.
4. **Validation** — Implement validation rules from §8: non-empty check, field existence (single-table), dataset reachability (cross-dataset), circular dependency detection (topological sort), scope enum validation, post-expansion cycle detection.
5. **Planner** — Build Stage 1 plan: topological sort of dataset filter dependencies, then inject `Filtering` or `FilteringJoin` steps at source initialization. Stage 2 operates on the filtered sources.
6. **Transpiler** — No changes needed if filters are modeled as existing `Filtering` / `FilteringJoin` algebra operations.
7. **Tests** — Unit tests for parsing, validation (including circular dependency), planner injection, pervasive expansion. End-to-end tests with gold SQL.

---

## 14. Out of Scope

- **Named segments / opt-in filters** — The "opt-in named filter" use case is already served by boolean dimension fields (e.g., `is_completed: "status = 'completed'"` with `dimension: {}`). Adding a parallel `segments` mechanism would increase schema surface area without meaningful benefit.
- **Aggregate-level dataset filters** — A `sql_always_having` equivalent (Looker). Aggregate filters belong at the metric level via `FilterSpec`.
- **Conditional filters** — Filters that are required unless an alternative field is filtered (Looker `conditionally_filter`). This is a UX concern for BI tools, not a semantic model feature.
- **Dynamic security context** — Filters based on the authenticated user (Power BI `USERPRINCIPALNAME()`). This requires runtime context injection, which is outside the scope of a static semantic model. (Note: the parameter pattern in §9.2/§9.3 provides a static approximation.)
- **Bidirectional propagation** — Power BI supports opt-in bidirectional cross-filter propagation (fact→dimension). OSI pervasive filters propagate in one direction only (dimension→fact, following N:1 relationship direction). Bidirectional restriction can be achieved by combining a pervasive filter on the dimension with an explicit `EXISTS_IN` on the fact table. This is more explicit than Power BI's bidirectional checkbox and avoids ambiguity with multiple relationship paths.
- **List-of-strings filter syntax** — The `filter` field is a single string. When a dataset has multiple independent filter concerns (soft-delete, security, multi-tenancy), they must be combined with AND in one string. A future backward-compatible extension could allow `filter` to accept either a string or a list of strings (AND-composed), improving maintainability for compound filters without changing semantics.

---

## 15. Industry Mapping

This section maps each major BI tool's filter concepts to OSI equivalents, demonstrating that OSI's three-scope model (`dataset`, `pervasive`, `related`) covers the full industry spectrum.

### 15.1 Tableau

| Tableau Concept | OSI Equivalent |
|:---|:---|
| Pervasive data source filter | `filter: {expression: "...", scope: pervasive}` on the dimension dataset |
| Per-table logical table filter (2025.1+) | `filter: "..."` (bare string, default `scope: dataset`) |
| Extract filter | Out of scope (physical-layer concern) |

Tableau pervasive filters cannot reference other tables in the expression — they are single-table predicates that propagate via relationship traversal. This maps directly to a simple predicate with `scope: pervasive`. Tableau's per-table filter (new in 2025.1) is exactly `scope: dataset`.

### 15.2 Looker

| Looker Concept | OSI Equivalent |
|:---|:---|
| `sql_always_where` (Explore-level) | `filter: {expression: "...", scope: pervasive}` on the Explore's base dataset, OR `scope: dataset` with explicit `EXISTS_IN` for cross-view references |
| `sql_always_having` | Out of scope (use metric-level `FilterSpec`) |
| `conditionally_filter` | Out of scope (BI-tool UX concern) |
| `always_filter` | Out of scope (BI-tool UX concern) |
| `always_join` + cross-view filter | `EXISTS_IN` in the filter expression (OSI infers join inclusion from the relationship graph) |

Looker's `sql_always_where` is Explore-scoped, roughly equivalent to a pervasive filter on the Explore's base view. Looker's cross-view references (`${customer.name}`) in `sql_always_where` map to OSI's `EXISTS_IN` syntax. The key difference: Looker uses `always_join` to force join inclusion; OSI infers join inclusion from the relationship graph.

### 15.3 Power BI

| Power BI Concept | OSI Equivalent |
|:---|:---|
| RLS filter on dimension (single-direction) | `filter: {expression: "...", scope: pervasive}` on the dimension dataset |
| RLS with bidirectional cross-filter | `scope: pervasive` on the dimension PLUS explicit `EXISTS_IN` on the fact table (for reverse direction) |
| `USERPRINCIPALNAME()` / dynamic security | `:user_id` parameter with `scope: pervasive` (static approximation via parameters) |
| Single-direction filter propagation | `scope: pervasive` (default propagation direction matches Power BI's default) |

Power BI's default RLS propagation (single-direction, dimension→fact) maps perfectly to `scope: pervasive`. The propagation direction — from the dimension table through N:1 relationships to fact tables — is identical. Power BI's opt-in bidirectional filtering is handled by combining pervasive propagation in one direction with an explicit `EXISTS_IN` in the other.

### 15.4 AtScale

| AtScale Concept | OSI Equivalent |
|:---|:---|
| Row Security Object, scope=All | `filter: {expression: "...", scope: pervasive}` on a well-connected dimension |
| Row Security Object, scope=Fact | `filter: "..."` (`scope: dataset`) on the fact table, or `scope: pervasive` on a dimension that only connects to fact tables |
| Row Security Object, scope=Related | `filter: {expression: "...", scope: related}` |

AtScale's three-scope model maps directly to OSI's three scope values. AtScale's "All" vs "Fact" distinction is about whether the filter applies when only dimensions are queried (no metrics). In OSI, this is inherent in the relationship topology — a `pervasive` filter on a dimension propagates to all connected datasets regardless of whether the query includes metrics.

### 15.5 ThoughtSpot

| ThoughtSpot Concept | OSI Equivalent |
|:---|:---|
| Table-level RLS rule | `filter: {expression: "...", scope: pervasive}` (ThoughtSpot RLS always propagates to dependent objects) |
| Worksheet filter | `filter: "..."` with `scope: dataset` on the worksheet's source dataset |
| Disable RLS on worksheet | No direct equivalent — OSI filters are immutable and cannot be disabled per-query (this is intentional for security) |

ThoughtSpot's RLS model is closest to Power BI — filters on tables automatically propagate to all dependent objects. This maps to `scope: pervasive`.

### 15.6 Cube.js / dbt

| Concept | OSI Equivalent |
|:---|:---|
| Cube.js segments | Boolean dimension fields (existing OSI feature — no `filter` needed) |
| dbt/MetricFlow | No filter concept — N/A |

Neither tool supports always-applied dataset filters or pervasive propagation. Cube.js segments are opt-in named filters, which map to boolean dimension fields in OSI. dbt/MetricFlow has no filter abstraction at the semantic layer.

### 15.7 Coverage Summary

| Scope Value | Industry Tools Covered |
|:---|:---|
| `dataset` | Tableau per-table (2025.1+), Cube.js segments, any tool's non-propagating filter |
| `pervasive` | Power BI RLS, Tableau pervasive, Looker `sql_always_where`, ThoughtSpot RLS, AtScale "All" |
| `related` | AtScale "Related" |

All six major BI tools with dataset-level filter support (Tableau, Looker, Power BI, AtScale, ThoughtSpot, Cube.js) can express their filter semantics using OSI's three-scope model. No tool requires a scope value that OSI does not provide.

**Note:** Multi-column `EXISTS_IN` is already supported by the `SQL_EXPRESSION_SUBSET` spec (paired argument syntax: `EXISTS_IN(col1, ds.f1, col2, ds.f2)`). Dataset filters fully support this syntax — entitlement tables keyed on composite columns (e.g., `(region, product_line)`) work without workarounds.
