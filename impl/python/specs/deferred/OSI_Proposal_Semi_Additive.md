# Proposal: Semi-Additive Measures (Snapshot Safety)

**Status:** Draft Proposal
**Author:** will.pugh@snowflake.com
**Date:** 2026-02-26
**Related specs:**
- [OSI Core Abstractions](./OSI_Core_Abstractions.md)
- [OSI Calc Model Semantics](./OSI_Calc_Model_Semantics.md) §Snapshot Tables, §Aggregation Rules

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Background: Semi-Additive Measures in BI](#2-background-semi-additive-measures-in-bi)
3. [Model Syntax: `snapshot_dimensions`](#3-model-syntax-snapshot_dimensions)
4. [Algebra Semantics](#4-algebra-semantics)
   - [E4002 Safety Check](#41-e4002-safety-check)
   - [Snapshot Dimension Resolution](#42-snapshot-dimension-resolution)
   - [Propagation Through Algebra Operations](#43-propagation-through-algebra-operations)
   - [CASE WHEN Bypass: Dimension Covariance](#44-case-when-bypass-dimension-covariance)
5. [Safe Aggregation Functions](#5-safe-aggregation-functions)
6. [Re-aggregation Interaction](#6-re-aggregation-interaction)
7. [Examples](#7-examples)
   - [Inventory Balance (TPC-DS)](#71-inventory-balance-tpc-ds)
   - [Bank Account Balance](#72-bank-account-balance)
   - [Multi-Snapshot-Dimension Table](#73-multi-snapshot-dimension-table)
8. [Proposed Spec Changes](#8-proposed-spec-changes)
9. [Implementation Status](#9-implementation-status)
10. [Open Questions](#10-open-questions)

---

## 1. Motivation

Semi-additive measures are one of the most common sources of incorrect BI results. A snapshot table records the state of a quantity at regular intervals (e.g., daily account balances, weekly inventory levels). The measure value at each snapshot point is a **point-in-time** quantity, not an incremental delta. Naively applying `SUM` across snapshot periods produces meaningless results — the same balance is counted multiple times.

Every major BI tool (Tableau, Looker, Power BI) provides some mechanism to guard against this, but it is typically either:
- A runtime warning that is easy to ignore, or
- A metadata flag that silently switches to `MAX`/`LAST` without user awareness.

OSI takes a **compiler-enforced safety** approach: the model author declares which dimensions make a field semi-additive, and the algebra rejects unsafe aggregations at plan time with a clear error (E4002). This catches the bug before any SQL is generated.

### TPC-DS Relevance

The `inventory` table in TPC-DS is a canonical semi-additive case. `inv_quantity_on_hand` is snapshotted by `inv_date_sk`. Queries like Q22 and Q44 require careful handling to avoid summing inventory across dates.

---

## 2. Background: Semi-Additive Measures in BI

Kimball's classification of aggregation behavior:

| Category | Definition | Example |
|:---|:---|:---|
| **Fully additive** | Safe to SUM across all dimensions | `sales_amount` |
| **Semi-additive** | Safe to SUM across some dimensions, not others | `account_balance` (not across time) |
| **Non-additive** | Cannot be summed across any dimension | `unit_price`, `ratio` |

Semi-additivity is always **with respect to** one or more specific dimensions — typically the snapshot/time dimension. A balance is additive across accounts (summing balances of different accounts gives total portfolio balance) but not across dates (summing Monday's balance + Tuesday's balance is meaningless).

### Resolution Strategies

When a user wants an aggregate that would violate semi-additivity, there are two valid approaches:

1. **Filter to single snapshot point**: `WHERE date = '2024-01-01'` reduces the snapshot dimension to a single value, making `SUM` safe again (it's just summing across accounts for one date).

2. **Use a snapshot-safe aggregation**: `MAX(balance)`, `AVG(balance)`, `MIN(balance)` produce statistically valid results across snapshot points.

---

## 3. Model Syntax: `snapshot_dimensions`

### Field-Level Declaration

```yaml
datasets:
  - name: inventory
    source: INVENTORY
    primary_key: []
    unique_keys:
      - [inv_item_sk, inv_warehouse_sk, inv_date_sk]
    fields:
      - name: inv_date_sk
        expression: INV_DATE_SK
        dimension: {}
      - name: inv_item_sk
        expression: INV_ITEM_SK
        dimension: {}
      - name: inv_warehouse_sk
        expression: INV_WAREHOUSE_SK
        dimension: {}
      - name: inv_quantity_on_hand
        expression: INV_QUANTITY_ON_HAND
        snapshot_dimensions: [inv_date_sk]     # ← semi-additive declaration
```

### Syntax Rules

- `snapshot_dimensions` is an optional list of field names on any non-dimension field.
- Each entry **must** reference an existing field within the same dataset.
- Each entry **must** reference a dimension field (one with `dimension: {}` set). This is validated at parse time — snapshot dimensions that reference measure fields are rejected because a measure can never be meaningfully "resolved" to a single value via equality filter.
- Multiple snapshot dimensions are supported (e.g., a field semi-additive with respect to both date and account period).
- When omitted or `null`, the field has no semi-additive constraints.

### Type Mapping

| Layer | Type | Default |
|:---|:---|:---|
| YAML (`Field`) | `list[FieldName] \| None` | `None` |
| Planning (`Column`) | `frozenset[str]` | `frozenset()` |

The conversion from nullable list to non-nullable frozenset happens in `PlannerContext.build_initial_state()`.

---

## 4. Algebra Semantics

### 4.1 E4002 Safety Check

When `aggregate()` encounters a column with non-empty `snapshot_dimensions`, it checks:

1. Are **all** snapshot dimensions resolved (single-valued)?
   - If yes → any aggregation is allowed.
   - If no → only [snapshot-safe aggregations](#5-safe-aggregation-functions) are allowed.

2. If an unsafe function (e.g., `SUM`) is used on an unresolved snapshot column, the planner raises **E4002** with:
   - The column name and its snapshot dimensions
   - Which dimensions are unresolved
   - Suggested fixes (filter to single value, or use a safe aggregation)

### 4.2 Snapshot Dimension Resolution

A snapshot dimension is "resolved" when its corresponding column in the `CalculationState` has `is_single_valued = True`. This happens when:

- An equality filter is applied: `filtering(state, ["date = '2024-01-01'"])` sets `is_single_valued = True` on the `date` column.
- A `filter_to_remove()` operation pins the dimension to a single value (used by the LOD planner for cross-grain filters).

Range filters (`date > '2024-01-01'`) do **not** resolve the snapshot dimension because multiple values may still be present.

Resolution is checked by `_all_snapshot_dims_resolved(column, state)`, which looks up each snapshot dimension by name in the state and checks `is_single_valued`.

### 4.3 Propagation Through Algebra Operations

| Operation | `snapshot_dimensions` | `snapshot_join_keys` |
|:---|:---|:---|
| `aggregate()` | **Cleared** (derived value) | **Cleared** (derived value) |
| `add_columns()` | **Union** from dependencies | **Union** from dependencies |
| `filtering()` | **Unchanged** (may resolve via single-valued) | **Preserved** |
| `filter_to_remove()` | Removes pinned dim; marks single-valued when empty | **Preserved** |
| `enrich()` | **Preserved** on both sides | **Set** on right-side columns (see §4.4); left preserved |
| `scalar_enrich()` | **Preserved** | **Set** from shared grain ∩ snapshot dims |
| `add_dimensions()` | **Preserved** | **Set** on new columns, same as `enrich()` |
| `merge()` | **Preserved** per-column | **Preserved** per-column |
| `make_attr()` | **Copied** from source column | **Copied** from source column |
| `filtering_join()` | **Preserved** (state1 only) | **Preserved** (state1 only) |

### 4.4 CASE WHEN Bypass: Dimension Covariance

The E4002 snapshot safety check may be bypassed when a semi-additive column appears inside a CASE WHEN expression — but only when the CASE condition **covaries with the snapshot dimension**. This section defines the algebraic principles that make this determination sound.

#### Principle 1: Dimension Covariance

A column **covaries with** a dimension when its value changes as that dimension changes. Formally, column C covaries with dimension D when:
- D is in the grain of the state that produced C, or
- C was introduced into the state through a join keyed on D (or on a column that itself covaries with D).

The semi-additive safety question for CASE WHEN reduces to: *does the CASE condition covary with the snapshot dimension?*
- **Yes** → the user is partitioning along the snapshot axis. This is an intentional override — the user is deliberately controlling which snapshot rows contribute.
- **No** → the user is partitioning along an orthogonal axis. The snapshot double-counting problem is untouched.

#### Principle 2: Enrichment Establishes Covariance

When `enrich(S1, S2, JoinCondition(left_key, right_key))` introduces columns from S2 into S1, every column from S2 inherits covariance with `left_key`. If `left_key` is a snapshot dimension (or is itself covariant with one), then S2's columns are **snapshot-linked**.

This is the mechanism that connects `d_date` (from `date_dim`) back to `inv_date_sk` (the snapshot dimension on `inventory`). The join `inv_date_sk = d_date_sk` establishes that `d_date` covaries with `inv_date_sk`.

The same principle applies to `scalar_enrich`: when a coarser-grain LOD result shares a grain dimension with the base state, columns from that LOD branch covary with the shared dimension. If the shared dimension is a snapshot dimension, the LOD columns are snapshot-linked.

#### Principle 3: Aggregation Resets Covariance

After aggregation, the output is a derived statistical quantity — its relationship to the original snapshot axis depends entirely on how it is re-introduced to the main state. Both `snapshot_dimensions` and `snapshot_join_keys` are cleared by `aggregate()`.

This is sound because the re-enrichment step (Principle 2) re-establishes exactly the right covariance based on which dimensions the aggregated result joins on:
- LOD aggregated at `{date, warehouse}` and joined back on `date` → covaries with date (snapshot-linked)
- LOD aggregated at `{warehouse}` only and joined back on `warehouse` → does NOT covary with date

#### Principle 4: Transitivity Through Join Chains

Covariance propagates through multi-hop join chains. If `fact → dim1` joins on a snapshot dimension, and `dim1 → dim2` joins on a `dim1` column, then `dim2` columns are transitively snapshot-linked.

Formally: if column C has `snapshot_join_keys = {X}`, and C is used as a left join key in a subsequent enrichment, the right-side columns inherit X in their `snapshot_join_keys`. This ensures that `fiscal_quarter` from a `fiscal_calendar` table (joined through `date_dim` which was joined through `inv_date_sk`) is recognized as snapshot-linked.

#### The `snapshot_join_keys` Column Field

To implement covariance tracking, `Column` carries a `snapshot_join_keys: frozenset[str]` field — the set of snapshot dimension names through which this column was introduced into the state via join. A column with `snapshot_join_keys = {"inv_date_sk"}` was enriched through a join whose left key is (or covaries with) the snapshot dimension `inv_date_sk`.

Initial columns (from `build_initial_state`) have empty `snapshot_join_keys` — they have no join provenance. The field is populated by `enrich()`, `scalar_enrich()`, and `add_dimensions()`, cleared by `aggregate()`, and unioned by `add_columns()` (see §4.3 propagation table).

#### The Bypass Rule

> **E4002 bypass is justified when at least one CASE WHEN condition column covaries with the aggregated column's snapshot dimension.**
>
> Covariance is witnessed by either:
> 1. The condition column is directly named in the snapshot column's `snapshot_dimensions`, OR
> 2. The condition column's `snapshot_join_keys` intersects with the snapshot column's `snapshot_dimensions`.

This admits:
- **Direct reference**: `SUM(CASE WHEN date = '...' THEN balance END)` — `date` is in `balance.snapshot_dimensions`
- **Cross-table join**: `SUM(CASE WHEN d_date < '...' THEN balance END)` — `d_date.snapshot_join_keys` contains `inv_date_sk` which is in `balance.snapshot_dimensions`
- **Multi-hop**: `SUM(CASE WHEN fiscal_quarter = 'Q1' THEN balance END)` — `fiscal_quarter.snapshot_join_keys` contains `inv_date_sk` (transitive through `date_dim`)

And correctly blocks:
- **Unrelated condition**: `SUM(CASE WHEN product = 'X' THEN balance END)` — `product` has empty `snapshot_join_keys` and is not in `balance.snapshot_dimensions`

**Note**: Explosion safety (E4001) still applies unconditionally to all CASE-WHEN-gated columns — join fan-out is a data-level problem orthogonal to user intent.

#### Expression Analysis

The function `_extract_aggregated_value_deps()` returns a `ValueDeps` named tuple with three sets:

| Field | Contents | Used For |
|:---|:---|:---|
| `all_deps` | All columns in the aggregated value position | E4001 explosion safety (unconditional) |
| `direct_deps` | Columns that are direct agg arguments (not CASE-gated) | E4002 always applies to these |
| `case_condition_deps` | Columns referenced in CASE WHEN conditions | Checked against `snapshot_join_keys` for covariance |

---

## 5. Safe Aggregation Functions

The following aggregations are safe on unresolved semi-additive columns:

| Function | Rationale |
|:---|:---|
| `MIN` | Minimum across snapshot points is well-defined |
| `MAX` | Maximum across snapshot points is well-defined |
| `COUNT` | Number of snapshot observations |
| `COUNT_DISTINCT` | Number of distinct values across snapshots |
| `AVG` | Average across snapshot points — valid statistical measure |
| `STDDEV`, `STDDEV_POP`, `STDDEV_SAMP` | Variability across snapshot points |
| `VARIANCE`, `VAR_POP`, `VAR_SAMP` | Variability across snapshot points |
| `ANY_VALUE` | Picks one value (non-deterministic but not double-counting) |
| `ATTR` / `CHECKED_ATTR` | Single-value assertion |
| `ARRAY_AGG`, `ARRAY_CAT` | Collecting values, not summing |
| `ARRAY_UNIQUE_AGG`, `ARRAY_UNION_AGG` | Set operations on values |

`SUM` is the primary unsafe function — it double-counts by adding the same balance across snapshot dates. All unlisted functions are conservatively treated as unsafe.

---

## 6. Re-aggregation Interaction

When a semi-additive measure is used with LOD modifiers (e.g., `FIXED` grain) that require re-aggregation, the snapshot safety check applies at the **first** aggregation step.

If the first aggregation uses a snapshot-safe function (e.g., `MAX`), re-aggregation follows the normal rules:
- `MAX(MAX(balance))` → `MAX(balance)` (distributive, re-aggregates as `MAX`)
- `AVG(balance)` at detail grain → re-aggregation uses `AccumulatorSpec` (algebraic, expands to `SUM`/`COUNT` intermediates)

The snapshot safety check does **not** apply to the re-aggregation step because `aggregate()` clears `snapshot_dimensions` on its output columns. The re-aggregation operates on a derived value, not the raw snapshot measure.

---

## 7. Examples

### 7.1 Inventory Balance (TPC-DS)

**Model:**
```yaml
- name: inv_quantity_on_hand
  expression: INV_QUANTITY_ON_HAND
  snapshot_dimensions: [inv_date_sk]
```

**Safe query — filtered to single date:**
```python
LODQuery(
    dimensions=["inv_item_sk"],
    measures=[MeasureRequest(output_name="qty", expression="SUM(inv_quantity_on_hand)")],
    filters=["inv_date_sk = 2451234"],
)
```
Planner: `filtering` marks `inv_date_sk` as single-valued → `SUM` is allowed.

**Safe query — snapshot-safe aggregation:**
```python
LODQuery(
    dimensions=["inv_item_sk"],
    measures=[MeasureRequest(output_name="avg_qty", expression="AVG(inv_quantity_on_hand)")],
)
```
Planner: `AVG` is in `SNAPSHOT_SAFE_AGGREGATIONS` → allowed without date filter.

**Blocked query — unsafe aggregation:**
```python
LODQuery(
    dimensions=["inv_item_sk"],
    measures=[MeasureRequest(output_name="total_qty", expression="SUM(inv_quantity_on_hand)")],
)
```
Planner raises E4002: "Cannot use ['SUM'] on snapshot column 'inv_quantity_on_hand' with dimensions ['inv_date_sk']."

### 7.2 Bank Account Balance

**Model:**
```yaml
- name: account_balance
  expression: BALANCE
  snapshot_dimensions: [snapshot_date]
```

**CASE WHEN on snapshot dim — allowed:**
```python
expression="SUM(CASE WHEN snapshot_date = '2024-12-31' THEN account_balance END)"
```
`snapshot_date` is directly in `account_balance.snapshot_dimensions` → covariance confirmed → E4002 bypassed.

**Cross-table CASE WHEN — allowed:**
```python
expression="SUM(CASE WHEN CAST(d_date AS DATE) < '2024-06-01' THEN account_balance END)"
```
`d_date` was joined through `snapshot_date` → `d_date.snapshot_join_keys = {snapshot_date}` → covariance confirmed → E4002 bypassed.

**Unrelated CASE WHEN — blocked:**
```python
expression="SUM(CASE WHEN region = 'East' THEN account_balance END)"
```
`region` has no `snapshot_join_keys` and is not in `snapshot_dimensions` → no covariance → E4002 fires.

**Direct SUM — blocked:**
```python
expression="SUM(account_balance)"
```
E4002 fires: "Cannot use ['SUM'] on snapshot column 'account_balance' with dimensions ['snapshot_date']."

### 7.3 Multi-Snapshot-Dimension Table

A table with two snapshot axes (e.g., daily balance by account period):

```yaml
- name: balance
  expression: BALANCE
  snapshot_dimensions: [snapshot_date, accounting_period]
```

- `SUM(balance)` with `snapshot_date = '2024-01-01'` → **blocked** (accounting_period unresolved)
- `SUM(balance)` with both `snapshot_date = '...'` AND `accounting_period = 'Q1'` → **allowed**
- `MAX(balance)` with no filters → **allowed** (MAX is snapshot-safe)

---

## 8. Proposed Spec Changes

### 8.1 OSI_Core_Abstractions.md

Add `snapshot_dimensions` to the Field specification:

> **snapshot_dimensions** (optional, list of field names): Declares which dimension fields make this measure semi-additive. When set, the compiler enforces snapshot-safe aggregation rules (see OSI_Calc_Model_Semantics.md §Aggregation Rules). Each entry must reference an existing dimension field in the same dataset.

### 8.2 OSI_Calc_Model_Semantics.md

The existing §Snapshot Tables and §Snapshot Allowed Aggregations sections already describe the semantics. Four changes:

- **CASE WHEN bypass rule** (§Aggregation Rules): CASE WHEN bypasses E4002 only when the condition covaries with the snapshot dimension (via direct reference or join provenance). See §4.4 for the full principle-based definition.
- **`snapshot_join_keys` Column field** (§Calculation State): add `snapshot_join_keys` to the Column definition, documenting its semantics, propagation, and use in the CASE WHEN covariance check.
- **STDDEV/VARIANCE safe list** (§Snapshot Allowed Aggregations): add STDDEV, STDDEV_POP, STDDEV_SAMP, VARIANCE, VAR_POP, VAR_SAMP with rationale (valid statistical properties across snapshot points, no double-counting).
- **Post-aggregation clearing** (§Aggregation Rules, "After an aggregation"): change `snapshot_dimensions stays the same` to `snapshot_dimensions is cleared` and add `snapshot_join_keys is cleared`. The aggregated result is a derived value — the semi-additive constraint was enforced at the point of aggregation and does not carry forward.

### 8.3 OSI_core_file_format.md

Add `snapshot_dimensions` to the field schema:

```yaml
snapshot_dimensions:  # optional
  type: array
  items:
    type: string
  description: >
    List of dimension field names that make this field semi-additive.
    Each entry must reference a dimension field in the same dataset.
```

---

## 9. Implementation Status

| Component | Status | Notes |
|:---|:---|:---|
| `Field.snapshot_dimensions` (parsing) | ✅ Done | `list[FieldName] \| None`, validated at parse time |
| `Column.snapshot_dimensions` (state) | ✅ Done | `frozenset[str]`, threaded through `PlannerContext` |
| `Column.snapshot_join_keys` (state) | ✅ Done | `frozenset[str]`, join provenance for covariance tracking |
| `Dataset.validate_snapshot_dimensions_exist` | ✅ Done | Checks existence AND dimension-ness |
| E4002 safety check in `_validate_aggregation_safety` | ✅ Done | With state-based resolution lookup |
| `SNAPSHOT_SAFE_AGGREGATIONS` set | ✅ Done | Includes STDDEV/VARIANCE family |
| Propagation: `snapshot_dimensions` | ✅ Done | Union in `add_columns`, cleared in `aggregate`, preserved elsewhere |
| Propagation: `snapshot_join_keys` | ✅ Done | Set in `enrich`/`scalar_enrich`/`add_dimensions`, union in `add_columns`, cleared in `aggregate` |
| Resolution via `filtering()` | ✅ Done | Equality filter → single-valued |
| Resolution via `filter_to_remove()` | ✅ Done | Removes from snapshot_dimensions set |
| CASE WHEN bypass (provenance-aware) | ✅ Done | Covariance check via `snapshot_join_keys` — see §4.4 principles |
| `ValueDeps` NamedTuple | ✅ Done | `all_deps`, `direct_deps`, `case_condition_deps` |
| TPC-DS `inv_quantity_on_hand` annotation | ✅ Done | Both tpcds.yaml and tpcds_duckdb.yaml |
| Unit tests (test_snapshot_safety.py) | ✅ Done | 28 tests covering all scenarios including provenance bypass |
| Unit tests (enrich provenance) | ✅ Done | In test_multi_hop_enrich.py: single-hop, multi-hop, transitivity |

---

## 10. Open Questions

### 10.1 Should `snapshot_dimensions` be metric-level too?

Currently `snapshot_dimensions` is field-level only. A metric like `SUM(inv_quantity_on_hand)` inherits the snapshot constraint from the underlying field. Should we also allow metric-level override? E.g.:

```yaml
metrics:
  - name: latest_inventory
    expression: "MAX(inv_quantity_on_hand)"
    snapshot_dimensions: []    # ← override: this metric resolved it
```

**Current decision**: Not needed. The algebra already handles this — `MAX` is snapshot-safe, so the constraint is satisfied naturally.

### 10.2 Should we warn instead of error for experienced users?

Some users may intentionally want `SUM(balance)` across dates for specific analytical purposes (e.g., "balance-days" calculation). Should we provide an `UNSAFE()` wrapper similar to the explosion-safety escape hatch?

**Current decision**: Defer. The CASE WHEN mechanism already provides an escape hatch when the user explicitly controls which rows contribute. A future `UNSAFE_SNAPSHOT()` wrapper could be added if demand arises.

### 10.3 Automatic last-value resolution

Some BI tools automatically insert `MAX(snapshot_date)` to pick the latest snapshot point. Should OSI support a `default_resolution: LATEST` option?

```yaml
- name: inv_quantity_on_hand
  expression: INV_QUANTITY_ON_HAND
  snapshot_dimensions:
    - dimension: inv_date_sk
      default_resolution: LATEST   # auto-filter to MAX(inv_date_sk)
```

**Current decision**: Out of scope for V1. This would require the planner to auto-inject a subquery for the latest date, which adds complexity. Users can achieve this with an explicit filter or a derived metric.
