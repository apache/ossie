# Proposal: Referential Integrity Settings for Relationships

**Status:** Draft Proposal  
**Author:** will.pugh@snowflake.com  
**Date:** 2026-02-23  
**Related specs:**
- [OSI Core File Format](./OSI_core_file_format.md)
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md)
- [Non-Equijoin Relationships (companion proposal)](./OSI_Proposal_Non_Equijoins.md)

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Design Principles](#2-design-principles)
3. [Tableau Comparison](#3-tableau-comparison)
4. [Proposed Schema Changes](#4-proposed-schema-changes)
5. [Semantics](#5-semantics)
   - [`from_all_rows_match: true`](#51-from_all_rows_match-true)
   - [`to_all_rows_match: true`](#52-to_all_rows_match-true)
   - [What RI Does NOT Do](#53-what-ri-does-not-do)
6. [Interaction with `joins.type`](#6-interaction-with-joinstype)
7. [Ergonomics](#7-ergonomics)
8. [Algebra Changes](#8-algebra-changes)
9. [TPC-DS Impact Analysis](#9-tpcds-impact-analysis)
10. [Proposed Spec Changes](#10-proposed-spec-changes)
    - [OSI_core_file_format.md](#101-osi_core_file_formatmd)
    - [OSI_Core_Abstractions.md](#102-osi_core_abstractionsmd)
    - [OSI_Calc_Model_Semantics.md](#103-osi_calc_model_semanticsmd)
11. [Implementation Steps](#11-implementation-steps)
12. [Out of Scope](#12-out-of-scope)

---

## 1. Motivation

The current OSI `relationships` schema defines how datasets are connected, but today's aggregation join logic defaults to LEFT JOIN to avoid silently dropping rows with unmatched foreign keys. This is the safest default, but it has real costs:

- Queries are more verbose in execution plans (LEFT JOIN produces more work for the optimizer)
- Model authors must annotate individual metrics with `joins: { type: INNER }` to opt in to INNER join behavior — required for 7 of 40 validated TPC-DS queries
- There is no way to declare *in the model* that a relationship is guaranteed referentially intact, allowing the engine to infer the tighter join type automatically

This proposal adds an optional `referential_integrity` object to the `relationships` schema that lets authors declare FK completeness once at the relationship level, eliminating repetitive per-metric `joins.type` boilerplate.

---

## 2. Design Principles

1. **Additive and backward-compatible**: The new `referential_integrity` field is optional. Existing models require no changes.
2. **Declare once, benefit everywhere**: RI settings live on the *relationship*, not on individual metrics. One declaration makes the right join type available across the entire model automatically.
3. **Conservative defaults are preserved**: Without explicit RI declarations, behaviour is identical to today (LEFT JOIN). Trust only what is declared.
4. **No data validation**: OSI trusts declarations. Data quality enforcement is the responsibility of the ETL/data engineering layer.
5. **Explicit `joins.type` still wins**: Model authors can still force any join type on individual metrics regardless of RI settings. RI sets a smarter default; it does not lock anything down.

---

## 3. Tableau Comparison

Tableau's Relationships model (introduced in Tableau 2020.2) allows model authors to declare **performance options** on each side of a relationship:

| Tableau Setting | Side | Meaning |
|:---|:---|:---|
| "Some rows match" (default) | Many-side | Some FK values may not have a PK match — use LEFT JOIN |
| "All rows match" | Many-side | Every FK has a matching PK — LEFT and INNER produce identical results |
| "Some rows match" (default) | One-side | Some PK values may have no FK rows pointing to them |
| "All rows match" | One-side | Every PK has at least one FK row — the dimension table won't lose rows |

Tableau uses these to infer whether to generate INNER or LEFT JOINs, and to suppress or include certain rows in multi-table queries, rather than requiring authors to manually tune join types per workbook.

OSI's version of this concept is slightly different: because OSI operates at the metric/computation level rather than at the viz level, the RI settings should inform the *planner's join type inference* — specifically the aggregation join context (see §Join Type Selection in `OSI_Core_Abstractions.md`).

---

## 4. Proposed Schema Changes

One new optional field is added to the `relationships` schema:

```yaml
referential_integrity:
  from_all_rows_match: boolean   # default: false
  to_all_rows_match: boolean     # default: false
```

Full updated relationship schema:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique identifier for the relationship |
| `from` | string | Yes | The dataset on the many-side (FK side) |
| `to` | string | Yes | The dataset on the one-side (PK side) |
| `from_columns` | array | Yes* | FK columns in the "from" dataset |
| `to_columns` | array | Yes* | PK/UK columns in the "to" dataset |
| `referential_integrity` | object | No | RI declarations (this proposal) |
| `ai_context` | string/object | No | Additional context for AI tools |
| `custom_extensions` | array | No | Vendor-specific attributes |

*`from_columns`/`to_columns` remain required for equijoin relationships. See the companion [Non-Equijoin proposal](./OSI_Proposal_Non_Equijoins.md) for relationships without equi-columns.

The `referential_integrity` object:

| Field | Type | Default | Meaning |
|:---|:---|:---|:---|
| `from_all_rows_match` | boolean | `false` | Every row in `from` has at least one matching row in `to` (FK completeness — no orphan FKs) |
| `to_all_rows_match` | boolean | `false` | Every row in `to` has at least one matching row in `from` (full participation — no uncovered PKs) |

**Example:**

```yaml
relationships:
  # Standard FK with declared RI — every order has a valid customer
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]
    referential_integrity:
      from_all_rows_match: true    # No orphan orders
      to_all_rows_match: false     # Some customers may have no orders (valid)

  # Store fact → date dimension — TPC-DS style, DW RI guaranteed
  - name: store_sales_to_date
    from: store_sales
    to: date_dim
    from_columns: [ss_sold_date_sk]
    to_columns: [d_date_sk]
    referential_integrity:
      from_all_rows_match: true    # All sales rows have a valid date key
```

---

## 5. Semantics

### 5.1 `from_all_rows_match: true`

**Declaration:** "Every row in the `from` (many-side / FK) dataset has at least one matching row in the `to` (one-side / PK) dataset."

In SQL terms: there are no orphan FK rows — `NOT EXISTS (SELECT 1 FROM from_table WHERE from_col NOT IN (SELECT to_col FROM to_table))`.

**Effect on the planner:**

When the planner would otherwise emit a LEFT JOIN to join the `from` dataset to the `to` dataset for aggregation resolution, it may instead emit an INNER JOIN without changing query semantics. The result set is identical because LEFT JOIN NULLs can never occur.

| Without RI | With `from_all_rows_match: true` |
|:---|:---|
| `FROM orders LEFT JOIN customers ON ...` | `FROM orders INNER JOIN customers ON ...` *(safe — no NULLs)* |

The planner MUST still emit LEFT JOIN if `joins.type` is not set AND `from_all_rows_match` is `false` (or absent).

**Effect on NULL handling:** When `from_all_rows_match: true`, the planner may also omit `COALESCE` or `IS NOT NULL` guards on dimension columns sourced from the `to` side at the SQL transpilation layer, since those columns are guaranteed non-NULL after the join.

### 5.2 `to_all_rows_match: true`

**Declaration:** "Every row in the `to` (one-side / PK) dataset has at least one matching row in the `from` (many-side / FK) dataset."

In SQL terms: the PK table is fully covered — no dimension row is unreferenced.

**Effect on the planner:**

This setting matters primarily in **LOD composition joins** when the dimension table is the outer/driving table. For example, when generating a "dimension-first" query (all customers, even those with no orders), `to_all_rows_match: true` tells the planner that no such empty-dimension rows exist, and a FULL OUTER JOIN or RIGHT JOIN is unnecessary.

> **Note:** Unlike `from_all_rows_match`, this does NOT override aggregation join types. LOD composition join types remain mathematically determined by grain relationships (see §LOD Composition Joins in `OSI_Core_Abstractions.md`). Its primary practical effect is enabling the transpiler to skip NULL-coalescing on composition keys for the `to` side.

**Bijection case:** When BOTH `from_all_rows_match: true` AND `to_all_rows_match: true` are declared **AND** the relationship has `cardinality: 1:1` (see the Non-Equijoin companion proposal, which introduces the `cardinality` field to equijoins as well), the relationship is a true bijection — the two datasets are in 1:1 correspondence with no unmatched rows on either side. The planner may use INNER JOIN unconditionally in all contexts for that relationship.

> **Careful:** Full participation on both sides alone (without `cardinality: 1:1`) does not imply bijection. An N:N relationship can have full participation on both sides.

### 5.3 What RI Does NOT Do

- It does **not** validate the data. OSI trusts declarations; data validation is the responsibility of the ETL/data engineering layer.
- It does **not** affect LOD composition joins (grain-to-grain composition). Those are always determined by grain math.
- It does **not** affect filtering joins (semi-joins / EXISTS). Those never produce NULLs anyway.
- It does **not** replace `joins.type`. Model authors can still force INNER/LEFT/RIGHT/FULL on individual metrics regardless of RI settings.

---

## 6. Interaction with `joins.type`

The precedence order for aggregation join type resolution:

| Priority | Source | Description |
|:---|:---|:---|
| 1 (highest) | `joins.type` on the metric | Explicit per-metric override |
| 2 | `referential_integrity` on the relationship | Model-level RI inference |
| 3 (default) | System default | LEFT JOIN |

This means a metric can still force INNER or LEFT even when RI says the opposite — useful for the case where a metric is intentionally more restrictive than the RI declaration (e.g., requiring a matching promotion record even though most sales have no promotion).

**Redundancy warning:** If a metric specifies `joins: { type: INNER }` on a relationship that has `from_all_rows_match: true`, the explicit type is redundant (not an error, but implementations MAY emit a warning to guide cleanup of legacy models).

---

## 7. Ergonomics

**For the model author**, RI settings are a one-time annotation at the relationship level that eliminates the need to scatter `joins: { type: INNER }` across individual metrics.

**Before this proposal** — To get INNER JOIN behavior, each metric must declare it:

```yaml
metrics:
  - name: store_revenue
    expression: SUM(store_sales.ss_net_paid)
    joins:
      type: INNER     # needed because reference SQL uses INNER JOIN

  - name: store_customers
    expression: COUNT(DISTINCT store_sales.ss_customer_sk)
    joins:
      type: INNER     # same boilerplate, repeated

  - name: avg_ticket
    expression: AVG(store_sales.ss_ticket_number)
    joins:
      type: INNER     # repeated again
```

**After this proposal** — Declare once on the relationship:

```yaml
relationships:
  - name: store_sales_to_date
    from: store_sales
    to: date_dim
    from_columns: [ss_sold_date_sk]
    to_columns: [d_date_sk]
    referential_integrity:
      from_all_rows_match: true   # ← one declaration covers all metrics
```

Then all metrics joining via this relationship get INNER join semantics automatically. The `joins: { type: INNER }` annotations on individual metrics become optional overrides for exceptional cases, rather than required boilerplate.

**For the query consumer (AI / tooling)**, RI settings are surfaced as factual declarations about the data that can inform query generation: "Can this join produce NULLs?" becomes a model-level query rather than a runtime concern.

---

## 8. Algebra Changes

**No new algebra operations are required.**

RI settings affect only the *join type selection* within existing operations. The specific changes are:

1. **`ExtendLOD` and `Enrich`**: When constructing the join SQL for a relationship, if `referential_integrity.from_all_rows_match = true`, the default join type becomes `INNER` instead of `LEFT`.

2. **`AddDimensions`**: Same as above — the join type inference consults RI.

3. **NULL guard suppression**: When the SQL transpiler emits scalar expressions over columns sourced from the `to` side of a relationship with `from_all_rows_match: true`, it may omit `COALESCE(..., 0)` or `IS NOT NULL` guards that it would otherwise add defensively.

4. **`_CrossTableJoinInfo` enhancement**: Add RI metadata to the internal join resolution record used during transpilation:

```
JoinResolution:
  relationship_name: str
  join_type: JoinType           # LEFT / INNER / RIGHT / FULL
  ri_from_complete: bool        # mirrors from_all_rows_match
  ri_to_complete: bool          # mirrors to_all_rows_match
```

This allows the transpiler to make contextual decisions without re-reading the model.

---

## 9. TPC-DS Impact Analysis

**7 of 40 validated queries** required `joins: { type: INNER }` annotations on individual metrics to match reference SQL:

> **Q46, Q47, Q57, Q68, Q69, Q79, Q89** — all needed `JoinSpec(type=JoinType.INNER)` on measures to match reference SQL semantics.

These queries all follow the same pattern: the TPC-DS reference SQL uses implicit INNER JOINs (SQL-92 comma-join defaults to INNER), while OSI defaults to LEFT. The reference queries produce the same result because TPC-DS data has referential integrity (the benchmark generates clean synthetic data with no orphan foreign keys).

**With RI settings**, these 7 queries would work without per-metric `joins.type` annotations:

```yaml
# Declare once on the TPC-DS relationships (data is RI-clean)
- name: store_sales_to_customer
  from: store_sales
  to: customer
  from_columns: [ss_customer_sk]
  to_columns: [c_customer_sk]
  referential_integrity:
    from_all_rows_match: true   # TPC-DS guarantees this

- name: store_sales_to_date
  from: store_sales
  to: date_dim
  from_columns: [ss_sold_date_sk]
  to_columns: [d_date_sk]
  referential_integrity:
    from_all_rows_match: true
```

This would eliminate 7 × N per-metric `joins.type` annotations across the model's 133 metrics. The model has 67 relationships; annotating the ~20 fact-to-dimension relationships that TPC-DS guarantees to be RI-clean covers all 7 affected queries.

---

## 10. Proposed Spec Changes

### 10.1 OSI_core_file_format.md

**Section: `## Relationships`**

Add new field to the schema table:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| ... (existing fields) | | | |
| `referential_integrity` | object | No | RI declarations |
| `referential_integrity.from_all_rows_match` | boolean | No | Every `from` row has a match in `to` (no orphan FKs). Default: `false` |
| `referential_integrity.to_all_rows_match` | boolean | No | Every `to` row has at least one match in `from` (full PK coverage). Default: `false` |

Add new example subsection:

```yaml
# RI-annotated equijoin (DW with guaranteed FK completeness)
- name: store_sales_to_date
  from: store_sales
  to: date_dim
  from_columns: [ss_sold_date_sk]
  to_columns: [d_date_sk]
  referential_integrity:
    from_all_rows_match: true
```

### 10.2 OSI_Core_Abstractions.md

**Section: `### Joins`**

Add paragraph after the description of `path` and `type`:

> **Referential Integrity:** Relationship-level RI declarations (`referential_integrity.from_all_rows_match`, `to_all_rows_match`) inform the default join type without requiring per-metric `joins.type` annotations. When `from_all_rows_match: true` is set on a relationship, the planner uses INNER JOIN instead of LEFT for that relationship in aggregation join contexts. Explicit `joins.type` overrides still take precedence (see §Join Type Selection).

**Section: `#### 1. Aggregation Joins (Resolving Fields)`**

Add a row to the join type table for RI-inferred INNER:

| Scenario | Default Join Type | Reasoning |
|:---|:---|:---|
| ... (existing rows) | | |
| N:1, `from_all_rows_match: true` | INNER (inferred) | RI guarantees no NULL rows — INNER is semantically identical to LEFT but enables more aggressive predicate pushdown by the optimizer |

Add note: "RI-inferred INNER joins have lower priority than explicit `joins.type` on the metric."

### 10.3 OSI_Calc_Model_Semantics.md

No changes required. RI affects join type selection only, not the algebra operations themselves.

---

## 11. Implementation Steps

1. **Schema parsing** — Add optional `referential_integrity` object to the `Relationship` model in `models.py`. Fields: `from_all_rows_match: bool = False`, `to_all_rows_match: bool = False`.

2. **Join type inference** — Update the aggregation join type selection logic in `LODPlanner._resolve_measure` (and the scalar dep resolution path) to consult `ri_from_complete` when no explicit `joins.type` is set. Priority stack: explicit `joins.type` > RI > system default (LEFT).

3. **`_CrossTableJoinInfo` enhancement** — Add `ri_from_complete` and `ri_to_complete` booleans to the internal join resolution record so the transpiler can make NULL-guard suppression decisions without re-reading the model.

4. **TPC-DS model update** — Annotate `tpcds.yaml` with `from_all_rows_match: true` on the fact-to-dimension relationships where TPC-DS data guarantees FK completeness. Remove the now-redundant `joins: { type: INNER }` from the 7 affected metrics and verify query results are unchanged.

5. **Tests** — Add test cases covering:
   - RI-inferred INNER join — verify plan matches explicit `joins.type: INNER`
   - `joins.type: LEFT` overrides RI on a relationship with `from_all_rows_match: true`
   - Redundancy warning when `joins.type: INNER` is set on an RI-complete relationship
   - `to_all_rows_match` does not change aggregation join type (only NULL-guard suppression)

---

## 12. Out of Scope

- **Data validation**: The spec does not validate that declared RI is actually true in the data. That is the responsibility of the data engineering / DQ layer.
- **Automatic RI inference**: The system will not inspect data to infer RI settings. Declarations are authoritative.
- **Non-equijoin relationships**: RI settings apply to equijoin relationships only in this proposal. See [OSI_Proposal_Non_Equijoins.md](./OSI_Proposal_Non_Equijoins.md) for RI interaction with non-equijoin relationships.
- **LOD composition join types**: These are mathematically determined and not affected by RI declarations.
