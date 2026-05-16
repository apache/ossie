# Proposal: ASOF and Range Joins (Temporal / SCD Type-2)

**Status:** Draft Proposal  
**Author:** will.pugh@snowflake.com  
**Date:** 2026-03-18  
**Related specs:**
- [OSI Proposal: Non-Equijoin Relationships](./OSI_Proposal_Non_Equijoins.md) — base framework for non-equijoins
- [OSI Core File Format](./OSI_core_file_format.md)
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [Snowflake Range-Based Relationships](../docs/snowflake_range_based_relationships.pdf) — design reference

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Relationship to Non-Equijoins Proposal](#2-relationship-to-non-equijoins-proposal)
3. [ASOF Joins](#3-asof-joins)
   - [3.1 Semantics](#31-semantics)
   - [3.2 Schema](#32-schema)
   - [3.3 Translation to SQL](#33-translation-to-sql)
   - [3.4 Snowflake Alignment](#34-snowflake-alignment)
4. [Range Joins](#4-range-joins)
   - [4.1 Semantics](#41-semantics)
   - [4.2 Table-Level Constraint](#42-table-level-constraint)
   - [4.3 Schema](#43-schema)
   - [4.4 Translation to SQL](#44-translation-to-sql)
   - [4.5 Snowflake Alignment](#45-snowflake-alignment)
5. [Combined Schema Changes](#5-combined-schema-changes)
6. [Proposed Spec Changes](#6-proposed-spec-changes)
7. [Implementation Notes](#7-implementation-notes)
8. [Out of Scope](#8-out-of-scope)

---

## 1. Motivation

The [Non-Equijoins proposal](./OSI_Proposal_Non_Equijoins.md) introduces a generic `condition` field that can express arbitrary SQL predicates, including range joins. However, two temporal join patterns are common enough and have well-defined semantics that warrant **structured first-class support**:

| Pattern | Use Case | Snowflake Support |
|:---|:---|:---|
| **ASOF** | SCD Type-2 with a single temporal column; "point-in-time" lookup where intervals are implicit (consecutive rows) | `ASOF` keyword in semantic view relationships |
| **Range** | SCD Type-2 with explicit start/end columns; "interval containment" where each dimension row defines a half-open interval | `BETWEEN start AND end EXCLUSIVE` with `DISTINCT RANGE` constraint |

**Benefits of structured ASOF and Range support:**

1. **Engine optimization** — Engines that support native ASOF JOIN (e.g., Snowflake, DuckDB) or range-join operators can generate optimal physical plans.
2. **Snowflake interoperability** — OSI models can be translated to Snowflake semantic view DDL without loss of intent.
3. **Clear author intent** — Model authors explicitly declare temporal semantics rather than encoding them in a generic `condition`.
4. **Validation** — Structured forms enable schema-level validation (e.g., range constraint on the table, ASOF column type checks).

---

## 2. Relationship to Non-Equijoins Proposal

This proposal **extends** the Non-Equijoins proposal. The relationship types are:

| Relationship Type | Expression | Cardinality | Notes |
|:---|:---|:---|:---|
| **Equijoin** | `from_columns` / `to_columns` | Inferred or declared | Base case |
| **ASOF** | `from_columns` / `to_columns` + `asof` | Always N:1 | Structured temporal; requires equi-keys |
| **Range** | `from_columns` / `to_columns` + `range` | Always N:1 | Structured interval; requires table constraint |
| **Condition (generic)** | `condition` (and optionally equi-keys) | Declared | Arbitrary predicate |

**Mutual exclusivity:** At most one of `condition`, `asof`, or `range` may be specified per relationship. Specifying more than one is a **validation error**. This eliminates ambiguity about which form governs translation and keeps the model author's intent unambiguous.

**Fallback:** Any ASOF or Range join can be expressed via the generic `condition` field. The structured forms are optional ergonomic and optimization hints. If an author needs both ASOF and a custom predicate, they must use `condition` only (and lose structured ASOF optimization).

---

## 3. ASOF Joins

### 3.1 Semantics

An **ASOF join** finds, for each row in the `from` dataset, the **single closest matching row** in the `to` dataset based on a temporal (or ordered) column. The intervals on the `to` side are **implicit** — they are defined by consecutive values of the ASOF column, not by explicit start/end columns.

**Key properties:**

- **M:1 cardinality** — Each `from` row matches at most one `to` row (within the equi-partition).
- **Equi-keys required** — ASOF semantics require partitioning. The `from_columns`/`to_columns` define the equi-join keys; the ASOF column is an additional match condition.
- **Match condition** — `from.asof_column >= to.asof_column` (or configurable operator). The match selects the **latest** `to` row whose ASOF value is ≤ the `from` value.
- **Supported types** — DATE, TIME, TIMESTAMP, TIMESTAMP_LTZ, TIMESTAMP_NTZ, TIMESTAMP_TZ, NUMBER (e.g., Unix epoch).

**Example:** Orders joined to customer address history. For each order, find the customer address that was effective at the order date (i.e., the address whose `ca_start_date` is the latest value ≤ `o_ord_date` within that customer).

### 3.2 Schema

Add an optional `asof` object to the relationship:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `from_column` | string | Yes | Column in the `from` dataset (e.g., order date) |
| `to_column` | string | Yes | Column in the `to` dataset (e.g., address start date) |
| `match` | enum | No | `>=` (default), `<=`, `>`, `<` — comparison operator for "closest" match. All four operators are supported by both Snowflake and DuckDB. |

**Validation rules:**

- `from_columns` and `to_columns` MUST be present (ASOF requires equi-keys).
- `asof.from_column` and `asof.to_column` MUST reference columns in `from` and `to` respectively.
- At most one `asof` object per relationship.
- Data types of `from_column` and `to_column` must be coercible (DATE/TIMESTAMP/NUMBER).
- `cardinality` when `asof` is present: always treated as N:1 (declaration optional but allowed for clarity).

**Example:**

```yaml
relationships:
  - name: orders_to_customer_address
    from: orders
    to: customer_address
    from_columns: [o_cust_id]
    to_columns: [ca_cust_id]
    asof:
      from_column: o_ord_date
      to_column: ca_start_date
      match: ">="   # optional; >= is default
    cardinality: N:1
```

### 3.3 Translation to SQL

**Generic SQL fallback (no native ASOF):**

For engines that do not support native ASOF JOIN (e.g., Postgres, Trino, BigQuery), the transpiler emits a window-function-based approach that is widely supported:

```sql
-- Window-function approach: rank candidates, keep closest match
SELECT o.*, ca_ranked.*
FROM orders o
LEFT JOIN (
  SELECT ca.*,
         ROW_NUMBER() OVER (
           PARTITION BY ca.ca_cust_id
           ORDER BY ca.ca_start_date DESC
         ) AS _asof_rn
  FROM customer_address ca
) ca_ranked
  ON o.o_cust_id = ca_ranked.ca_cust_id
 AND o.o_ord_date >= ca_ranked.ca_start_date
 AND ca_ranked._asof_rn = 1
```

Alternatively, engines supporting `LATERAL` can use a correlated subquery:

```sql
-- LATERAL subquery approach (Postgres, Snowflake, DuckDB)
SELECT o.*, ca.*
FROM orders o
LEFT JOIN LATERAL (
  SELECT *
  FROM customer_address ca
  WHERE ca.ca_cust_id = o.o_cust_id
    AND ca.ca_start_date <= o.o_ord_date
  ORDER BY ca.ca_start_date DESC
  LIMIT 1
) ca ON true
```

> **Note:** The window-function approach partitions only by equi-keys and orders by the ASOF column. It then filters to `_asof_rn = 1` to keep only the closest match. The inequality (`o.o_ord_date >= ca.ca_start_date`) in the ON clause ensures only valid candidates are joined. This approach works on all SQL engines but may be less efficient than native ASOF JOIN on large datasets.

**Snowflake SQL (native ASOF JOIN):**

```sql
FROM orders ASOF JOIN customer_address
  MATCH_CONDITION(orders.o_ord_date >= customer_address.ca_start_date)
  ON orders.o_cust_id = customer_address.ca_cust_id
```

> **Note:** Snowflake supports `>=`, `<=`, `>`, and `<` in the ASOF MATCH_CONDITION ([docs](https://docs.snowflake.com/en/sql-reference/constructs/asof-join)). The `=` operator is not supported by Snowflake. DuckDB supports the same set of operators.

### 3.4 Native Engine Support

Both **Snowflake** and **DuckDB** support native `ASOF JOIN` syntax, so the transpiler should emit it directly rather than using LATERAL subqueries or window-function workarounds.

**DuckDB:**
```sql
FROM orders o
ASOF LEFT JOIN customer_address ca
  ON o.o_cust_id = ca.ca_cust_id
  AND o.o_ord_date >= ca.ca_start_date
```

**Snowflake:**
```sql
FROM orders o
ASOF JOIN customer_address ca
  MATCH_CONDITION(o.o_ord_date >= ca.ca_start_date)
  ON o.o_cust_id = ca.ca_cust_id
```

DuckDB uses `ASOF LEFT JOIN` with the inequality in the `ON` clause. Snowflake uses `ASOF JOIN` with a separate `MATCH_CONDITION` clause. Both produce at most one right-side match per left-side row, making them inherently N:1.

### 3.5 Snowflake Alignment

| Snowflake Concept | OSI Equivalent |
|:---|:---|
| `ASOF col` in REFERENCES clause | `asof.to_column` |
| Left table column (implicit from relationship) | `asof.from_column` |
| `MATCH_CONDITION(left op right)` where op in {`>=`,`<=`,`>`,`<`} | `asof.match` (default `">="`) |
| Equi-keys in ON clause | `from_columns` / `to_columns` |
| At most one ASOF per relationship | Validation: at most one `asof` object |

---

## 4. Range Joins

### 4.1 Semantics

A **range join** matches a column in the `from` dataset against a **half-open interval** `[start, end)` defined by two columns in the `to` dataset. Each `to` row represents a distinct, non-overlapping interval.

**Key properties:**

- **M:1 cardinality** — Each `from` row matches at most one `to` row (enforced by the non-overlapping constraint).
- **Explicit intervals** — The `to` dataset has `start_column` and `end_column`; the predicate is `from_col >= start AND from_col < end`.
- **Half-open** — `[start, end)` — start inclusive, end exclusive. Matches Snowflake's `EXCLUSIVE` semantics.
- **NULL handling** — NULL in `start` means "smallest possible value"; NULL in `end` means "largest possible value" (unbounded).

### 4.2 Table-Level Constraint

The `to` dataset MUST declare a **distinct range constraint** indicating that the intervals are non-overlapping. This is a **dataset-level** (table) constraint, not a relationship-level one.

**Proposed dataset schema addition:**

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `distinct_ranges` | array | No | List of `{name?, start_column, end_column}` — each defines a non-overlapping half-open range |

**Example:**

```yaml
datasets:
  - name: promotion
    source: sales.promotion
    primary_key: [p_promo_sk]
    distinct_ranges:
      - start_column: p_start_date_sk
        end_column: p_end_date_sk
        # optional name for the constraint
```

**Validation:** A relationship may use a `range` key only if the `to` dataset declares a matching `distinct_ranges` entry (same start/end columns).

### 4.3 Schema

Add an optional `range` object to the relationship:

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `from_column` | string | Yes | Column in the `from` dataset (e.g., sale date) |
| `start_column` | string | Yes | Start column of the interval in the `to` dataset |
| `end_column` | string | Yes | End column of the interval in the `to` dataset |

**Validation rules:**

- The `to` dataset MUST have a `distinct_ranges` entry with the same `start_column` and `end_column`.
- `from_columns`/`to_columns` may be present (mixed equi + range) or absent (pure range join).
- When present, equi-keys are AND'd with the range predicate.
- `cardinality` when `range` is present: always N:1.

**Examples:**

```yaml
# Mixed equi + range: sales attributed to active promotion per item
relationships:
  - name: store_sales_to_promotion
    from: store_sales
    to: promotion
    from_columns: [ss_item_sk]
    to_columns: [p_item_sk]
    range:
      from_column: ss_sold_date_sk
      start_column: p_start_date_sk
      end_column: p_end_date_sk
    cardinality: N:1

# Pure range: event timestamp within a time period
relationships:
  - name: events_to_time_periods
    from: my_events
    to: my_time_periods
    range:
      from_column: event_timestamp
      start_column: start_time
      end_column: end_time
    cardinality: N:1
```

**Dataset with distinct range:**

```yaml
datasets:
  - name: my_time_periods
    source: analytics.time_periods
    primary_key: [time_period_id]
    distinct_ranges:
      - start_column: start_time
        end_column: end_time
```

### 4.4 Translation to SQL

**Generic SQL:**

```sql
(RHS.start_column IS NULL OR LHS.from_column >= RHS.start_column)
AND (RHS.end_column IS NULL OR LHS.from_column < RHS.end_column)
```

**Full example (mixed equi + range):**

```sql
SELECT ...
FROM store_sales ss
LEFT JOIN promotion p
  ON ss.ss_item_sk = p.p_item_sk
 AND (p.p_start_date_sk IS NULL OR ss.ss_sold_date_sk >= p.p_start_date_sk)
 AND (p.p_end_date_sk IS NULL OR ss.ss_sold_date_sk < p.p_end_date_sk)
```

### 4.5 Snowflake Alignment

| Snowflake Concept | OSI Equivalent |
|:---|:---|
| `DISTINCT RANGE BETWEEN start AND end EXCLUSIVE` on table | `distinct_ranges` on dataset |
| `BETWEEN start AND end EXCLUSIVE` in REFERENCES | `range` object |
| Half-open interval `[start, end)` | Implicit in predicate |
| NULL for unbounded | Same semantics |

---

## 5. Combined Schema Changes

### 5.1 Relationship Schema (Full)

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique identifier |
| `from` | string | Yes | Many-side dataset |
| `to` | string | Yes | One-side dataset |
| `from_columns` | array | Conditional* | FK columns |
| `to_columns` | array | Conditional* | PK/UK columns |
| `condition` | string | Conditional* | Generic non-equijoin predicate |
| `asof` | object | No | ASOF join spec |
| `range` | object | No | Range join spec |
| `cardinality` | enum | Conditional† | N:1, 1:1, N:N |
| `referential_integrity` | object | No | RI settings |
| `ai_context` | string/object | No | AI context |
| `custom_extensions` | array | No | Vendor extensions |

*At least one of: `from_columns`/`to_columns`, `condition`, or `asof`/`range` must be present.  
For `asof`: `from_columns`/`to_columns` required.  
For `range`: may have equi-keys or be pure range.  
†`cardinality` required when `condition` is present; optional override otherwise. For `asof`/`range`, N:1 is implicit.

**Mutual exclusivity:** At most one of `condition`, `asof`, or `range` may be present per relationship. If an author needs both ASOF and a custom condition, they must use `condition` only (and lose structured ASOF optimization).

### 5.2 Dataset Schema Addition

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `distinct_ranges` | array | No | Non-overlapping half-open ranges for range joins |

Each element: `{ name?: string, start_column: string, end_column: string }`.

---

## 6. Proposed Spec Changes

### 6.1 OSI_core_file_format.md

**Datasets:** Add `distinct_ranges` to the dataset schema.

**Relationships:** Add `asof` and `range` to the relationship schema. Update the conditional logic: either `from_columns`/`to_columns`, or `condition`, or `asof`/`range` (with `from_columns`/`to_columns` for ASOF).

### 6.2 OSI_Proposal_Non_Equijoins.md

**Section 4 (Proposed Schema Changes):** Add `asof` and `range` as alternative structured forms. Clarify that `condition` is the generic fallback; `asof` and `range` are preferred when applicable.

**New subsection:** "Structured Temporal Joins (ASOF and Range)" — reference this proposal for full details. Summary: use `asof` for single-column temporal lookup, `range` for explicit interval containment.

**Examples:** Add ASOF and Range examples alongside the existing `condition` examples.

### 6.3 OSI_Core_Abstractions.md

**Joins section:** Mention ASOF and Range as structured non-equijoin types. Same cardinality and grain rules as N:1 non-equijoins in the Non-Equijoins proposal.

---

## 7. Implementation Notes

### 7.1 Transpiler Behavior

- **Snowflake dialect:** Emit `ASOF JOIN ... MATCH_CONDITION(...)` for `asof` relationships; emit `BETWEEN ... EXCLUSIVE` for `range` relationships (and ensure `DISTINCT RANGE` is in the table DDL when generating Snowflake DDL).
- **DuckDB dialect:** Emit `ASOF LEFT JOIN ... ON equi_keys AND asof_col >= to_col` for `asof` relationships; emit range predicates with `IS NULL OR` guards for `range` relationships.
- **Generic dialect:** For engines without native ASOF JOIN support, emit equivalent SQL using a window-function approach (`ROW_NUMBER() OVER (PARTITION BY equi_keys ORDER BY asof_col DESC) = 1` — see §3.3 for full example). For range joins, emit `(start IS NULL OR col >= start) AND (end IS NULL OR col < end)` predicates.

### 7.2 Validation Order

1. Parse `asof` / `range` / `condition`.
2. If more than one present → error: "Relationship may specify at most one of: condition, asof, range."
3. If `asof`: require `from_columns`/`to_columns`; validate column types.
4. If `range`: require matching `distinct_ranges` on `to` dataset.

### 7.3 Backward Compatibility

All new fields are optional. Existing models are unchanged.

---

### 7.4 ASOF Tie-Breaking Behavior

**When multiple dimension rows share the same ASOF column value** (e.g., two
address records with the same `start_date`), the matched row is
**non-deterministic**. Different engines may return different rows:

- **DuckDB**: Returns the last matching row by insertion order.
- **Snowflake**: Returns an arbitrary matching row.
- **LATERAL subquery fallback**: Returns the first row per `ORDER BY ... DESC LIMIT 1`,
  which may differ if there are ties.

**Model authors must ensure uniqueness** on the ASOF column within each
partition defined by the equi-keys. If ties are possible in the data,
consider:

1. Adding a tiebreak column to the equi-keys (e.g., include a sequence number
   in `from_columns`/`to_columns`).
2. Using `distinct_ranges` with explicit `[start, end)` intervals instead of
   ASOF, which guarantees at most one match per partition.
3. Pre-deduplicating the dimension table to ensure uniqueness.

OSI does not currently support a tiebreak column in the ASOF spec itself.
However, model authors can achieve deterministic tiebreaking by adding the
tiebreak column to the equi-key pairs. For example, if addresses have
`(customer_id, start_date, version)`, use `from_columns: [customer_id, version]`
and `to_columns: [customer_id, version]` with `asof` on the date column.

---

## 8. Out of Scope

- **Multiple ASOF columns per relationship** — Snowflake allows at most one; we follow that.
- **Range overlap validation at runtime** — The `distinct_ranges` constraint is declarative; the engine does not validate non-overlap at query time.
- **Open intervals or closed intervals** — Only half-open `[start, end)` is specified, matching Snowflake.
- **ASOF tiebreak column** — A dedicated tiebreak specification is not part of this proposal. Use equi-key extension for deterministic matching (see §7.4).

---

## Appendix A: Snowflake Reference Summary

| Feature | Snowflake DDL | OSI YAML |
|:---|:---|:---|
| ASOF | `REFERENCES t2(equi_col, ASOF asof_col)` | `from_columns`, `to_columns`, `asof: { from_column, to_column }` |
| Range | `DISTINCT RANGE BETWEEN start AND end EXCLUSIVE` on table | `distinct_ranges` on dataset |
| Range | `REFERENCES t2(BETWEEN start AND end EXCLUSIVE)` | `range: { from_column, start_column, end_column }` |
| Predicate | `LHS {>=,<=,>,<} RHS` (ASOF) | `asof.match` (default `">="`) |
| Predicate | `LHS >= start AND LHS < end` (Range) | Implicit from `range` |

---

## Appendix B: Comparison with Generic Condition

| Aspect | ASOF (structured) | Range (structured) | Generic `condition` |
|:---|:---|:---|:---|
| Expressiveness | Single-column temporal | Two-column interval | Arbitrary predicate |
| Equi-keys | Required | Optional | Optional |
| Table constraint | None | `distinct_ranges` required | None |
| Snowflake translation | Native ASOF JOIN | Native range join | Rewritten as predicate |
| Optimization | Engine can use ASOF op | Engine can use range op | Generic join |
