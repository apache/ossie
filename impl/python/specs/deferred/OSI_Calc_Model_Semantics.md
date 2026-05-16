# OSI Calculation Model Semantics

This document builds on the concepts in [Core Semantic Model Abstractions](./OSI_Core_Abstractions.md), with the intention of getting more precise in terms of how to go from a semantic query to a SQL query.

We will do this by breaking down the analytical operation into a set of well-defined plan steps as well as the core state that describes each step.

# Abstractions

## Grain / LOD

A grain defines the set of columns that represent the current granularity of the data. After aggregation, the grain is the GROUP BY columns — the minimal set that uniquely identifies each output row. For unaggregated source tables, the grain is all field names — representing the full dimensionality of the raw data. The actual minimal uniqueness constraint (primary key) is tracked separately in `unique_keys`.

This distinction matters because the grain controls what you can aggregate *to* (new_grain must be a subset of grain), while `unique_keys` controls join cardinality detection on source tables (where the grain is too wide to match against join columns).

## Calculation State

This is the information at each step in the calculation that is used to store enough information for the plan steps.

The information for each step:

* **Grain** – The full set of columns that define the current granularity. For unaggregated source tables, the grain is **all field names** (every column participates in row identification). After aggregation, the grain is the GROUP BY columns. The grain serves two purposes: (1) it defines which columns can appear in a subsequent `Aggregate` as `new_grain` (must be a subset), and (2) it participates in cardinality detection for joins (`grain ⊆ join_cols` implies uniqueness on the join key). Because unaggregated grains are wide (all fields), the grain check rarely fires for source tables — the `unique_keys` check handles that case instead.
* **Unique_keys** – A collection of column sets that are known to uniquely identify rows, independent of the grain. For unaggregated source tables, this includes the primary key and any declared unique constraints. This is the primary mechanism for detecting 1:1 joins on source tables (where `grain` is too wide to be a subset of any typical join key). After aggregation, `unique_keys` are cleared — post-aggregation uniqueness is determined solely by the new grain. Together, `grain` and `unique_keys` provide cardinality detection across the full state lifecycle:

  | State | Grain | Unique_keys | Which fires for cardinality? |
  |---|---|---|---|
  | Unaggregated source | All fields | `[{primary_key}, ...]` | `unique_keys` (grain is too wide) |
  | Post-aggregation | GROUP BY cols | cleared `()` | `grain` (unique_keys empty) |
* **Columns** – These are the columns in the current step.  Columns are their own objects that have:
  * **Name** - This is the name of column, must be unique in the state
  * **Expression** - This is the expression.  It may be either an aggregated column or a dimension
  * **Dependencies** - This is the set of columns this one depends on through its expression
  * **Is_agg** - whether this is an aggregation function or not
  * **Num_aggs** - This is the number of times this has been aggregated.  If this is more than 1, then we need to take into account multi-step aggregation rules.
  * **Is_join_exploded** - if True then this can only be aggregated using [explosion safe aggregations](#explosion-safe-aggregations)
  * **snapshot_dimensions** - this is the list of columns that represent a snapshotting of data.  As a result only [snapshot safe aggregations](#snapshot-allowed-aggregations-semi-additive) can be used when any of these columns are in the dimensions
  * **snapshot_join_keys** - the set of snapshot dimension names through which this column was introduced into the state via join (enrichment provenance). Used by the CASE WHEN bypass rule to determine whether a condition column covaries with the snapshot axis. Empty for initial columns; populated by Enrich, BroadcastEnrich, AddDimensions; cleared by Aggregate; unioned by AddColumns.
  * **Is_single_valued** – Whether this is provably a single value, meaning either
    * All NULLs
    * All a single value with NO NULLs.
* **Expression_ids** – Maps to what expressions this step is involved with calculating

---

## Calculation Operations and Algebra

### LOD Change Operations

These are the operations that will change an LOD.  Many of these will involve aggregations.

#### Aggregate(original_state, new_grain, new_aggs) -> State

This is a basic, safe aggregation step.  It will aggregate to a new grain, but will ensure that all the validation rules are in place to ensure safety.

**Operation:**
Represents an aggregation without pulling any new columns in.

**Validation:**

* `new_grain` MUST be a subset of (or equal to) the current `state.grain`. Aggregation can only go to a coarser grain — you cannot aggregate to a grain that includes columns not already in the grain.
* All new_grain and new_aggs MUST refer to columns that already exist
* New_aggs MUST either refer to a column with is_agg == true (if it is continuing an aggregation) or wrap a non-aggregated column in an aggregation
  * Columns being wrapped by an aggregation must validate the aggregation is allowed by following the [aggregation rules](#aggregation-rules)
* No scalar operations are allowed in new_grain or new_aggs.  Those should be added in a previous AddColumns operation.
* All rules in the [aggregation rules](#aggregation-rules) MUST be followed in order to prevent join explosion or incorrect semi-additive operations.

**Carrying single-valued columns through aggregation:**

Non-grain columns that are marked `is_single_valued=True` (e.g., via MakeAttr) can be carried through aggregation by explicitly including `CHECKED_ATTR(column)` in the `new_aggs` list. `CHECKED_ATTR` provides runtime verification that the value is truly single-valued — if MIN != MAX, the query errors rather than returning incorrect results. The planner is responsible for detecting MakeAttr'd columns that need to survive a GROUP BY and including them as `CHECKED_ATTR` aggregations.

**State Changes:**
The resulting state will be:

* Grain will be the new_grain
* Columns will be the union of
  * new_agg columns after having the [aggregation rules](#aggregation-rules) applied
  * The grain columns (with `is_join_exploded` reset to False)
* Expression_ids will remain the same
* Unique_keys are cleared (post-aggregation uniqueness is determined solely by the new grain)


#### ExtendLOD(original_state, other_table_state, new_grain, join_conditions) -> State

**Operation:**
This is a safe Join/Aggregate operation that will ensure a safe way to add columns to an LOD, particularly in the case that the join key is not the LOD.  E.g. the join key is order_id, but the desired grain to add is order_date.

This operation can be deconstructed into an AddDimensions / Aggregate sequence, but since this is such a common operation it is given its own treatment.

This will extend the LOD of the original state through merging it with another table.  This join MUST be either a 1-1 or 1-many operation.  In order to handle many-to-many operations, they must be broken down through some aggregation operation on both sides.  See [Join explosion and many-to-many joins](#join-explosion--many-to-many-joins) for patterns and limitations of breaking many-to-many joins into different 1-many joins.

**Validation:**
It will ultimately look like Join/Agg operation.  To enforce safety, this will:

* Ensure the other_table_state is a "1" side of either 1-1 or 1-many
* New_grain can be columns in either original_state or other_table_state
* There is no choice in the aggregation columns.  This is to ensure they all come from the original_state side.  Aggregating values from the many side can cause data duplication

**Resulting State:**
The resulting state will be:

* Grain will be the new_grain
* Columns will have the grain + any of the aggregation columns in original_state + any non-aggregation columns that are single-value
* Single_value_columns will be
  * Any of the grain columns that came exclusively from one side was single_value there
  * Any grain column that was:
    * in the join
    * Single_value
    * On the inner side of the join (e.g. outer joins can invalidate)

#### AddDimensions(original_state, additional_state, cols_to_add, join_conditions) -> State

**Operation**
This will add dimensions by joining to a new table.  It will not incur any aggregation, and will mark all the new dimensions with join_explosion if appropriate.  This can be useful to reason about whether a join-before-aggregation is safe for optimization.

Cardinality is auto-detected from the grain and unique_keys of each side relative to the join columns, using the same logic as Enrich. The caller may pass an explicit cardinality override, but auto-detection is preferred to avoid a class of bugs where the caller gets it wrong.

**Validation:**

* Must have valid join conditions
* All cols_to_add must exist in additional_state
* No column name collisions between cols_to_add and existing non-join columns

**Resulting State:**

* All of the columns from both sides
* Explosion marking depends on auto-detected cardinality:
  * **1-to-1** (both sides unique on their join columns): No columns marked as exploded.
  * **1-to-many** (only one side unique on join columns): Columns from the unique (1) side are marked `is_join_exploded=True`, because those values are replicated across the many-side's rows.
  * **Many-to-many** (neither side unique on join columns): ALL columns from both sides are marked `is_join_exploded=True`.
* The grain of the resulting state will be the union of both sides' grains, de-duplicating any columns that appear in the join conditions.
* Unique_keys are not propagated (invalidated by the join).

#### FilterToRemoveLOD(original_state, column_to_filter, value_to_filter) -> State

**Operation**
Used to resolve "Point-in-Time" or "Snapshot" grain conflicts. This operation restricts a specific dimension (usually a time or version column) to a singular value to remove the redundancy inherent in semi-additive data. For example, filtering a "Daily Balance" table to only "End of Month" records so that the balance can be safely summed at the Year grain.

**Validation:**

* `column_to_filter` must be a member of the current `state.grain` or `state.snapshot_dimensions`.
* The `value_to_filter` must be a scalar or a deterministic expression (e.g., `MAX(date)` or `'2023-12-31'`).

**Resulting State:**

* **Grain:** The `column_to_filter` is removed from the active grain (or marked as "Fixed").
* **snapshot_dimensions:** The filtered dimension is removed from this set for all columns.
* **is_single_valued:** The `column_to_filter` is now marked as `True` for the filtered field.
* **is_join_exploded:** If a column's only remaining snapshot dimensions become empty after the filter (i.e., all snapshot-related grain conflicts are resolved), `is_join_exploded` is reset to **False**. This handles the case where a many-to-many was caused solely by the snapshot dimension — once that dimension is pinned to a single value, the join becomes 1-to-many and the explosion is resolved.

#### RefineGrain(state, additional_dims) -> State

**Operation**
Adds functionally-dependent columns to the grain without changing the logical row set. This makes implicit functional dependencies explicit so that downstream operations can use these columns as join keys or GROUP BY dimensions.

A common use case is after an Enrich (N:1 join) brings dimension columns into the state. Those columns are functionally dependent on the join key (which is in the grain), but are not themselves grain members. RefineGrain promotes them into the grain so they can participate in subsequent joins or aggregations.

**Validation:**

* Each column in `additional_dims` MUST exist in `state.columns`
* Each column MUST NOT already be in the grain (idempotent: silently ignored if already present)
* Each column MUST satisfy at least one of the following functional dependency justifications:
  * `is_join_exploded=True` — came from an N:1 Enrich, therefore functionally dependent on the grain via the join key
  * `is_single_valued=True` — provably constant, trivially FD on any grain
  * A scalar column added via AddColumns at the current grain (its value is deterministic per grain row)
  * A materialized dimension-metric (e.g., `COUNT(*)` at FIXED [customer_id]) that was aggregated at a finer grain and enriched back — it has a single value per grain row

By construction, the algebra operations guarantee that every non-grain column in the state satisfies one of the above FD justifications — columns can only enter the state through operations that establish the dependency. The validator confirms column existence and non-grain membership; a defensive FD check is included as a safeguard against code that bypasses the algebra to construct states directly.

**Resulting State:**

* **Grain**: `state.grain | additional_dims`
* **Columns**: Unchanged
* **Expression_ids**: Unchanged

### Same Grain Operations

These are operations that change the state, but the result will ALWAYS be the same grain.

#### AddColumns(state, list[name->expression]) -> State

**Operation:**

Defines new scalar calculations based on existing columns in the state. Window functions (e.g., `RANK() OVER (...)`) are also allowed — they are same-grain operations that operate within the current result set. Implementations should auto-detect window functions rather than requiring a flag.

**Validation:**

* Expressions must only reference columns present in `state.columns`.
* Expressions MUST NOT contain bare aggregation functions (those belong in Aggregate). Window functions wrapping aggregations (e.g., `SUM(x) OVER (...)`) are allowed.
* If an expression combines multiple columns, the engine must check the "Combining Expressions" rules (below) to determine the new column's properties.

**Resulting State:**

* `columns`: Original list + new defined columns.
* `is_join_exploded`: Determined by the [Combining Expressions](#combining-expressions) rule — True only when every non-single-valued dependency is itself exploded (see §Combining Expressions for full rule and rationale).
* `snapshot_dimensions`: A union of all `snapshot_dimensions` from the dependencies.
* `snapshot_join_keys`: A union of all `snapshot_join_keys` from the dependencies.
* `is_agg`: Inherited from dependencies (usually False for scalar AddColumns).

#### Project(state, columns_to_keep) -> State

**Operation:**

Removes unneeded columns from the state without changing the grain or row set. Used to clean up intermediate computation columns (e.g., accumulator intermediates after re-aggregation finalization). This is a same-grain operation — the logical data is unchanged, only the column set is narrowed.

**Validation:**

* All grain columns MUST be present in `columns_to_keep` (the grain cannot reference columns that no longer exist)
* All names in `columns_to_keep` MUST exist in `state.columns`

**Resulting State:**

* `columns`: The subset of original columns whose names are in `columns_to_keep`, preserving original order
* **Grain**: Unchanged
* **Expression_ids**: Unchanged
* All column properties (`is_join_exploded`, `is_agg`, etc.) are preserved on the kept columns

#### MakeAttr(state, [columns]) -> State

**Operation**

Asserts that specified columns are single-valued at the current grain. This is a **metadata-only** operation — it does not change the column's expression or wrap it in an aggregation function. It marks the column as provably single-valued, which cleanses it of explosion risk for future steps.

When a MakeAttr'd column later needs to survive an Aggregate step (i.e., it is not in the new grain), the planner is responsible for including `CHECKED_ATTR(column)` in the aggregation list — a runtime-verified single-value aggregation (implemented as MIN/MAX with equality check). This deferred approach keeps the assertion separate from the aggregation mechanism and avoids prematurely marking non-aggregated columns as `is_agg=True`.

**Validation:**

* Columns must exist in `state.columns`.
* If a column is already `is_single_valued=True`, this is a no-op for that column.

**Resulting State:**

* `is_single_valued`: **True** (This "cleanses" the column of explosion risks for future steps).
* `is_join_exploded`: **False** (The assertion has resolved the redundancy).
* `is_agg`: **Unchanged** — MakeAttr is an assertion, not an aggregation. The column retains its original `is_agg` status.
* `Num_aggs`: **Unchanged** — no aggregation has occurred.
* `expression`: **Unchanged** — no wrapping. The CHECKED_ATTR wrapping is deferred to the Aggregate operation.

#### Merge(state_1, state_2, include_all = True) -> State

**Description**

Combines two distinct calculation paths (e.g., from two different fact tables) that have been brought to the same grain.  This will be the equivalent of a 1-1 join.

* If include_all is **True** (default), this is a **FULL OUTER JOIN** — rows from both sides are preserved.  This is the standard behavior for LOD composition where neither branch should lose rows.
* If include_all is **False**, this is an **INNER JOIN** — only rows present in both branches survive.  This is useful when both branches must agree on the grain values (e.g., "only show regions that have both revenue AND returns").

The join type (FULL OUTER vs INNER) MUST be propagated to the transpiler via step metadata so the correct SQL is generated.

For left or right outer joins, use Enrich.

**Validation:**

* `state_1.grain` must be identical to `state_2.grain`.

**Resulting State:**

* `columns`: Union of all columns from both states.
* `is_join_exploded`: Preserved per-column from their respective origin states.

#### Enrich(state_1, state_2, join_conditions) -> State

**Description**

Combines two distinct calculation paths via a LEFT OUTER JOIN.  This is the standard operation for enriching a finer-grain state with columns from a coarser-grain (or equal-grain) state.

The cardinality is auto-detected from the grain and unique_keys:

* If `state_1.grain` (or any `state_1.unique_keys`) is a **subset of the left join columns**, then state_1 is unique on the join key — this is a **1:1** join.  No explosion.
* Otherwise, multiple state_1 rows can match the same state_2 row — this is **N:1** (state_2 values are replicated).  State_2 columns are marked `is_join_exploded=True`.

**Validation:**

* At least one join condition must be provided
* All left join columns must exist in state_1
* All right join columns must exist in state_2
* state_2 MUST be unique on the right join columns — i.e., `state_2.grain ⊆ right_join_cols` OR any `state_2.unique_keys ⊆ right_join_cols`. This guarantees the LEFT JOIN does not multiply state_1's rows. If state_2 is not unique on the join key, use AddDimensions instead (which properly tracks the explosion).
* Non-join column name collisions are tolerated (left side wins, collision logged)

**Resulting State:**

* **Grain**: `state_1.grain` (preserved — left side defines the rows)
* `columns`: All state_1 columns + state_2 non-join, non-collision columns
* `is_join_exploded`:
  * **1:1**: State_2 columns preserve their existing explosion status (not marked)
  * **N:1**: State_2 columns marked `is_join_exploded=True`
  * State_1 columns always preserve their existing status
* `snapshot_join_keys`: State_2 columns inherit enrichment provenance from the join keys
* `expression_ids`: Union of both states
* `unique_keys`: Preserved from state_1

#### BroadcastEnrich(state, coarser_state) -> State

**Description**

Enriches a state with columns from a coarser-grain or scalar (empty-grain) state via CROSS JOIN.  Used when `coarser_state` has no shared grain dimensions with `state` — typically for FIXED [] grand totals or coarser-grain branches whose grain columns are already present in `state`.

Only non-grain measure columns from `coarser_state` are appended. Grain columns from `coarser_state` are excluded (they would be redundant or conflicting with the base state's grain).

**Validation:**

* No explicit validation — the operation is always safe because it does not change cardinality relative to the base state (every base row gets the same broadcast value).

**Resulting State:**

* **Grain**: `state.grain` (preserved)
* `columns`: state columns + coarser_state non-grain, non-duplicate columns
* `is_join_exploded`: **True** for all appended columns (they are replicated across all base rows)
* `snapshot_join_keys`: Appended columns inherit snapshot provenance from shared grain dimensions
* `expression_ids`: Union of both states
* `unique_keys`: Not propagated

#### Filtering(state_1, column_expression[]) -> State
**Operation**
This will filter state_1 by a set of expressions that evaluate to a boolean.  These will be processed as scalar expressions at the grain of the current state.

**Validation**

* All columns referenced in `expressions` must exist in `state_1.columns`.

**Resulting State:**

* **Grain**: Unchanged.
* **Columns**: Unchanged (no new columns are added; the set of rows is merely restricted).
* **Properties**:
  * `is_join_exploded`: Remains as it was in `state_1`.
  * `is_single_valued`: This may change from `False` to `True` if the filter restricts a column to a single constant value (e.g., `WHERE status = 'Active'`).

#### FilteringJoin(state_1, state_2, include_or_exclude, join_conditions, filter_conditions) -> State
This will filter state_1 by what is in state_2 based on the join.  This will be a semi or anti-semi join.  Since, this does not cause any explosion, the join_conditions can be anything (1-many, many-many, 1-1)

In addition to the join, is a filter_condition.  If this is not set, then the condition is assumed to be just membership.  However, if set, then there can be additional conditions added to the equi or non-equi-join. The semantics are the same as an equijoin, but implementations may be more optimized in the case of complicated join conditions.

**Operation**

Filters `state_1` based on the presence (Semi-Join) or absence (Anti-Semi-Join) of matching records in `state_2`.

**Validation:**

* `join_conditions` must correctly map columns between `state_1` and `state_2`.
* Since this is a semi-join/anti-join, it does **not** cause explosion. Therefore, the cardinality (1-many, many-many) does not need to be restricted.

**Resulting State:**

* **Grain**: Unchanged. `state_1`'s grain is preserved because no columns from `state_2` are appended to the result set.
* **Columns**: Only columns from `state_1` are returned.
* **Properties**:
  * `is_join_exploded`: Remains as it was in `state_1`. This operation is explicitly safe from join explosion because semi-joins do not duplicate rows from the left side.
  * `snapshot_dimensions`: Inherited strictly from `state_1`.

---

# Join Explosion & Many To Many Joins

As mentioned above, our algebra only deals with directly joining 1-many or 1-1 joins.  However, we can often run into cases that a customer has a many-to-many relationship.  There are several reasons why there can be many-to-many joins, and this section breaks out some useful reasons and some analytically safe ways of handling them.

## Membership Check

A user may want to only see deals they are associated with.  However, one user may be involved in many deals and a deal may have multiple people working on it.  This leads to a many-to-many join, however, is not a problem if it is only used for filtering.  E.g. the FilteringJoin operation.  This is because in that case it will not cause any join explosion, so it is safe.

## Snapshot Tables

This is where you may have a snapshot of data that would otherwise be 1-many, but becomes many-many, because you have a copy of the data for every day.

For example imagine account balance and customer.  One account has one customer, and one customer can have multiple accounts.  Normally, this would be a fine 1-many relationship.

However, with a snapshot table, there is one account record for each day.  The PK becomes <date, account> rather than <account>

This makes the relationships many-many, because there are m account records, so a join from customer to account balance will explode.

There are two ways to handle this:

### Filter to 1-many

In this option, the account balance table can be made 1-many if we filter on a specific date.

More generally, if a unique key is <m,n> and another table joins on <n>, then the join can get multiple results.  If we filter m to a single value, then the join will get a single result.

An example of this working would be if we queried for account balances for account 123, we would get one for each day.  If we queried for account balance for account 123 on Jan 3, we would only get one record.

### Snapshot Safe Aggregation to 1-many

In this option, we use a snapshot safe aggregation, such as MAX, MIN, AVG, etc, to aggregate to a single record per account.  This follows the normal "aggregate before join" rules.  However, the aggregations need to be of a special type.

## Team to deals

This is a common pattern where multiple people may work on a deal and a person may work on multiple deals.  This is a classic example of a many-to-many relationship.  All the methods for Snapshot tables still work.  You can filter to one individual to create a 1-many join, or you can use explosion safe aggregations.

There are also two other possible approaches:

### Use ordered Array_Agg to reduce dimensions on one side to array or string

In this example, you can aggregate the team-members per project into a single array to pre-aggregate one side to turn this into a 1-many join.  In the case, you may have an issue of an array as a dimension, but can alleviate that by turning into a human readable string.  It would be up to the user to have the correct expression for handling that case.

## Shared Dimensions

Another common pattern is for a many to many to occur through another table, like a shared dimension.  In this case, there are 3 tables involved: 2 fact tables and a dimension table.  The fact tables have a many-to-1 each to the dimension.

This is a case where we avoid any many-to-many joins by having each fact table aggregate to the LOD of the shared dimensions before they join together.

In the case that the fact tables need to join through multiple dimensions, the same patterns apply, but we just need to join in each dimension, then aggregate.  Both sides do this, and the join at the LOD of the shared dimensions.

---

# Functions & Expressions

## Aggregation Rules

There are a couple of rules around aggregations that MUST be followed.  These provide safe aggregations.  These are based on aggregating a single Column in the Calculation State.  Combining columns should happen as scalar operations:

* If the column's is_join_exploded is set, then it MUST use Explosion Safe Aggregations.
* If the column has snapshot_dimensions then it MUST follow the Snapshot Allowed Aggregations rules.  If both is_join_exploded and snapshot_dimensions are true, then the most restrictive rules are followed (which are the Explosion Safe Aggregations)
* **CASE WHEN bypass (provenance-aware)**: When a column with snapshot_dimensions appears inside a CASE WHEN expression rather than as a direct argument to the aggregation function, the snapshot safety restriction may be bypassed — but only when the CASE condition **covaries with the snapshot dimension**. A condition column covaries if it is either (a) directly named in the aggregated column's snapshot_dimensions, or (b) was introduced into the state through a join keyed on a snapshot dimension (tracked via the column's `snapshot_join_keys` provenance field). Example: `SUM(CASE WHEN d_date < '...' THEN balance END)` is allowed because `d_date` was joined through `inv_date_sk` (a snapshot dimension). But `SUM(CASE WHEN product = 'X' THEN balance END)` is blocked because `product` has no snapshot provenance. The explosion safety rule (E4001) still applies unconditionally to all CASE-WHEN-gated columns.
* After an aggregation
  * Num_aggs must be incremented
  * Is_join_exploded set to False.
  * Is_single_valued is set to False
  * Snapshot_dimensions is cleared (set to empty). The aggregated result is a derived value, no longer a raw snapshot measure — the semi-additive constraint was enforced at the point of aggregation and does not carry forward.
  * snapshot_join_keys is cleared (set to empty).

## Explosion Safe Aggregations

When working on a column that has been join-exploded, there are extra values that have been added with no clear magnitude.  As a result, we can work with operations that work on the domain of values, but not their counts.

The only functions allowed are:

* MIN
* MAX
* COUNT DISTINCT
* ANY_VALUE
* ARRAY_UNIQUE_AGG
* ARRAY_UNION_AGG

Any other aggregation function should result in an error.

## Snapshot Allowed Aggregations (Semi-Additive)

When working with semi-additive metrics, there is normally a set of snapshot dimensions which define snapshots of data.  For example <date> for bank account balance snapshots.  Aggregating across these dimensions have special rules:

1) If the snapshot dimensions are all single-value any aggregation is allowed
2) Otherwise only the following functions are allowed
   1) MIN
   2) MAX
   3) COUNT DISTINCT
   4) COUNT
   5) ANY_VALUE
   6) ARRAY_UNIQUE_AGG
   7) ARRAY_UNION_AGG
   8) AVG
   9) STDDEV, STDDEV_POP, STDDEV_SAMP
   10) VARIANCE, VAR_POP, VAR_SAMP
   11) ARRAY_AGG
   12) ARRAY_CAT

*Rationale for STDDEV/VARIANCE*: These compute valid statistical properties across snapshot time points (e.g., variability of a balance over time). Unlike SUM, they do not double-count — they characterize the distribution of snapshot values. SUM is the primary unsafe function because it adds the same balance multiple times across snapshot dates.

## Combining Expressions

Creating expressions often involves combining multiple columns.  For the purpose of this algebra, all combinations are scalar.  Aggregation expressions are logically aggregated to the same LOD, and then combined through scalar expressions.

When an expression references multiple columns, the default combination rules are:

**Dependencies** are the union of the dependencies of the columns
**Is_agg** starts with False
**Num_aggs** starts at 0
**Is_join_exploded** is True only when every non-single-valued dependency is itself exploded. If any dependency is neither exploded nor single-valued (i.e., it genuinely varies per grain row), the expression result also varies per grain row and is not considered exploded.

*Rationale:* A column from an N:1 Enrich is marked exploded because its value is replicated across many-side rows. But a scalar expression that combines an exploded column with a per-row column (e.g., `CASE WHEN region = 'East' THEN amount * 1.1 ELSE amount END`) produces a unique value per grain row. The per-row dependency "anchors" the result to the grain, making it safe for aggregation. Conversely, if all varying dependencies are exploded (e.g., `exploded_a + exploded_b`) or the only non-exploded dependencies are single-valued constants, the result inherits the explosion and remains unsafe.

**Snapshot_dimensions** is the union of the snapshot dimensions
**snapshot_join_keys** is the union of the snapshot_join_keys from all dependencies

**Is_single_valued:**

* False if any non-deterministic functions are added (e.g. rand())
* Otherwise it is the conjunction (AND) of all the columns

### Examples to think through:

**Multi-step aggregations across tables:**

AVG(SUM(order_value) / COUNT(parts))

SUM(order_value) calculated on orders table
COUNT(parts) calculated on parts table

SUM(order_value) / COUNT(parts) -> No longer agg

**Window Functions**

Window functions (e.g., `RANK() OVER (...)`, `SUM(x) OVER (PARTITION BY ...)`) are same-grain operations — they compute values within the current result set without changing cardinality. They are handled via AddColumns (which auto-detects and allows window functions). At the algebra level, they behave like scalar AddColumns: they produce a new column at the current grain, with properties inherited from their dependencies per the Combining Expressions rules.

---

# Algebra Summary

| Operation | Category | Description |
|---|---|---|
| Aggregate | LOD Change | GROUP BY to a coarser grain with safety checks |
| ExtendLOD | LOD Change | Join + aggregate in one step (derived: AddDimensions + Aggregate) |
| AddDimensions | LOD Change | Add dimension columns via join, tracking explosion safety |
| FilterToRemoveLOD | LOD Change | Pin a grain dimension to a single value, removing it from the grain |
| RefineGrain | LOD Change | Promote functionally-dependent columns into the grain |
| AddColumns | Same Grain | Add scalar/window expressions without changing grain |
| Project | Same Grain | Remove columns from state |
| MakeAttr | Same Grain | Assert single-valuedness of a column (metadata-only; aggregation deferred to Aggregate) |
| Merge | Same Grain | Combine two same-grain states (FULL OUTER or INNER on grain keys) |
| Enrich | Same Grain | N:1 or 1:1 LEFT JOIN — add columns, mark explosion |
| BroadcastEnrich | Same Grain | CROSS JOIN a scalar/coarser-grain value onto every row |
| Filtering | Same Grain | Apply WHERE predicates |
| FilteringJoin | Same Grain | SEMI or ANTI_SEMI join for existence/non-existence filters |
