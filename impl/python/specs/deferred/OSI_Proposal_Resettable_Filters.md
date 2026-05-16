# OSI Grain & Filter Discussion (Take 2\)

**Discussion on concepts to extend**: [OSI Discussion Point: Core Analytic Abstractions](https://docs.google.com/document/d/1si8DqU4arG18ZgX4HnRG5D_zS2X7V1s-vgNY35rvxhM/edit?usp=sharing) with a different filter concept  
**Author(s):**  will.pugh@snowflake.com (Snowflake), \<add your name\>  
**Contributors:** 

---

## 1\. Motivation

[OSI Discussion Point: Core Analytic Abstractions](https://docs.google.com/document/d/1si8DqU4arG18ZgX4HnRG5D_zS2X7V1s-vgNY35rvxhM/edit?usp=sharing) describes a core set of abstractions for the OSI semantic model to act as its analytical base.  One inherent property in that model is the Filter properties.  After evaluating this construct, there were a few weakness we found that this document is meant to address:

1) Treating query filters differently than field filters is confusing.  We should have one filter context.  
2) Having a filter reset rather than something more granular makes some conversions (like PowerBI) more fragile and fails to address some reasonable use cases.  In addition, other tools like Thoughtspot have more fine-grained control over changing grain and filter context.  
3) Any changes to grain or filters, requires sub-query semantics.

This proposal unifies filter and grain into a single set-operation model that:

- Uses the same `{mode, exclude, include, keep_only}` shape for both  
- Closes the grain expressiveness gap  
- Preserves the evaluation ordering that enables period-over-period patterns  
- Maps cleanly to all four major BI tools (Power BI, Tableau, ThoughtSpot, Looker)  
- Introduces a `GRAIN_AGG` function to enable expression based context changes.

---

## 2\. Proposed Model

Both filter and grain are modeled as **set operations on an inherited context**.  Any change in the context acts logically as if it had sub-query isolation.  

```
filter:
  mode: RELATIVE            # or FIXED 
  exclude: [field_names]    # clauses to remove (by column reference matching)
  keep_only: [field_names]  # clauses to keep if in the original context    
  include:                  # clauses to add
    - "expression1"
    - "expression2"

grain:
  mode: FIXED               # or RELATIVE
  exclude: [dim_names]      # dimensions to remove
  keep_only: [dim_names]    # dimensions to keep if in the original context
  include: [dim_names]      # dimensions to add
```

### Modes

| Mode | Filter Meaning | Grain Meaning |
| :---- | :---- | :---- |
| `RELATIVE` (default) | Start from parent's filter context | Start from query's dimensions |
| `FIXED` | Start with an empty context | Start with an empty context |

- `RELATIVE` \= Inherits the filter or grain context from its parent.  If the expression is part of the initial query, the grain will be the query dimensions and the filter will be what is in the where clause. The inherited set is the starting point, then `exclude` removes and `include` adds additional fields/expressions.  
- `FIXED` \= The inherited set is discarded and replaced with the fields in the `include` or `keep_only` list.  `exclude` is ignored, because the context has already been reset.

#### `FIXED` examples

```
# "Unfiltered revenue at top level
- name: unfiltered_revenue
  expression: SUM(orders.amount)
  grain:
    mode: FIXED      # grand total, at empty grain
  filter:
    mode: FIXED      # clear out filters

# "Revenue by region and optionally category, without the color filter"
# Uses region always; adds category if the user queries it.
- name: parent_revenue
  expression: SUM(orders.amount)
  grain:
    mode: FIXED
    keep_only: [region, category, subcategory]   # uses whichever are in query
  filter:
    exclude: [color]
```

Effective grain for `parent_revenue`:

| Query dimensions | Effective grain | Notes |
| :---- | :---- | :---- |
| `[region, color]` | `[region]` | category, subcategory not in query — skipped |
| `[region, category, color]` | `[region, category]` | subcategory not in query — skipped |
| `[region, category, subcategory]` | `[region, category, subcategory]` | all declared dims present |
| `[year]` | `[]` | none of the declared dims in query — grand total |

### Properties

| Property | Filter | Grain |
| :---- | :---- | :---- |
| `exclude` | List of field names. Any inherited clause containing a column reference matching a listed field is removed. Supports `table.*` wildcard. | List of dimension names to remove from the inherited grain.  Supports `table.*` wildcard.  |
| `include` | List of filter expression strings. Each is considered a top level independent clause and added to the context. These will not be split up the way the query filter is.  Separate expressions act as if they are semantically combined using the AND operator. | List of dimension names to add to the grain. |
| `keep_only` | List of field names. Only inherited clauses whose column references match a listed field are added;  Complement of `exclude`. Can be combined with `exclude` to remove broadly and rescue specific fields (see ALLEXCEPT pattern). Supports `table.*` wildcard.  | List of dimension names to declare for `FIXED` grain.  Only ones that are in the current grain context will be added. These are \= `keep_only ∩ parent_context_dims`. |

### Defaults

If no filter or grain is specified:

- Filter: `mode: RELATIVE` with no exclude/include — inherit parent context unchanged.  
- Grain: `mode: RELATIVE` with no exclude/include —inherit parent context unchanged.

A filter with `mode: RELATIVE`, no exclude, and no include is a no-op and should be omitted.

### Filter Exclusion Rules

When deciding whether a filter is included through a keep\_only or excluded through an exclude clause is not as simple as grain, because filters can have expressions that include multiple fields.  They can also have sub-expressions such as (A and (B OR C)).  We need to have consistent rules to make sure we get predictable behaviour.

#### Top Level Filters

Filter exclusions will not recursively look through all the filter clauses, but will rather have a concept of the top level filters in a context.  Each of these top level filters will be either included or excluded atomically.

For **query filters** which come in through clauses like WHERE or HAVING, we will need to have a first pass to turn the expression into top level filters.  HAVING filters follow the same decomposition rules as WHERE — they are split at top-level AND into independent clauses.  The implementation places each clause at the correct point in the generated SQL (WHERE vs HAVING) based on the implied grain: clauses that reference only dimensions or raw columns go in WHERE; clauses that reference aggregate expressions go in HAVING.  This will break them out along top level AND clauses.  It will respect parentheses as being atomic, so will not do anything to break them up or find equivalences.

| WHERE clause | Top Level Filters |
| :---- | :---- |
| `Price > 100` | `[“Price > 100”]` |
| `Price > 100 OR quantity > 20` | `[“Price > 100 OR quantity > 20”]` |
| `Price > 100 AND quantity > 20` | `[“Price > 100”,   “quantity > 20”]` |
| `(Price > 100 AND quantity > 20)` | `[“Price > 100 AND quantity > 20”]` |
| `Region = ‘WEST’ AND (Price > 100 AND quantity > 20)` | `[“Region = ‘WEST’”,   “Price > 100 AND quantity > 20”]` |

When filters are added through an INCLUDE statement in the filter context, each filter added will be a top level filter.  No additional splitting will be done, it is a more direct mapping.

| INCLUDE clause | Top Level Filters |
| :---- | :---- |
| `[“Price > 100”]` | `[“Price > 100”]` |
| `[“Price > 100 AND quantity > 20”]` | `[“Price > 100 AND quantity > 20”]` |
| `[“Price > 100”, “quantity > 20”]` | `[“Price > 100”, “quantity > 20”]` |

#### Filter Matching (Normative)

When deciding whether a top-level filter clause matches an `exclude` or `keep_only` field list, implementations **MUST** use the **"any column matches"** rule:

> A top-level clause matches a field list if **any** column reference in the clause's AST resolves (after identifier normalization and `table.*` wildcard expansion) to a field in the list.  A clause that mentions multiple fields will match any of them.

**Examples (against `exclude: [region]`):**

| Clause | Matches? | Why |
| :---- | :---- | :---- |
| `region = 'US'` | Yes | Single column reference matches |
| `UPPER(region) = 'US'` | Yes | Column reference in expression matches |
| `region = 'US' OR status = 'OK'` | Yes | Any-column rule — `region` is referenced |
| `amount > 100` | No | No column reference matches |
| `region.country = 'US'` (with `exclude: [region.*]`) | Yes | Wildcard expands to match nested column |

**Rationale for "any column matches":** An alternative rule would be "all columns match" (a clause matches only if every column reference is in the field list).  The any-column rule is easier to reason about, matches ThoughtSpot's semantics, and keeps `exclude` aligned with the user's mental model of "remove filters that mention this field."  This makes PowerBI `CALCULATE` conversions slightly trickier for expressions that mix fields, but the implementation tricks listed in §7 (Power BI BI Tool Validation) cover the common cases.

**Do not** split compound clauses apart or try to find logical equivalences — matching is atomic at the top-level clause granularity defined above.

#### Field Matching

For the most part the field matching will map to the namespace rules in [OSI Discussion Point: Core Analytic Abstractions](https://docs.google.com/document/d/1si8DqU4arG18ZgX4HnRG5D_zS2X7V1s-vgNY35rvxhM/edit?usp=sharing), however, there is a way of matching all the columns in a table, using the \* operator.  For example:  `exclude: [products.*]` will exclude all the columns from the products table.

### Evaluation Ordering

The evaluation ordering spelled out to ensure that:

* Exclude happens before include or filter application  
* Fields in a filter are evaluated post exclude  
* A function `PRE_FILTER()` is added for cases such as time shifting that want to use a field before the excludes stage happens.

Logically, the steps look like:

1. **Inherit** the parent's filter context (for top-level metrics, this is the query WHERE clause decomposed into independent clauses).  This is also the context that `PRE_FILTER()` will operate in — **including when the enclosing scope uses `mode: FIXED`**, because FIXED clears the context only at step 2, *after* PRE_FILTER has captured its value at step 1.
2. **If `mode: FIXED`**, clear the inherited context to the empty set.  (For `mode: RELATIVE` — the default — skip this step.)  This happens *after* PRE_FILTER captures step 1.
3. **Apply `exclude`**: remove any inherited clause whose column references match a field in the exclude list (after identifier normalization). For grain, remove listed dimensions from the inherited grain set.  
4. **Apply `keep_only`**: adds back any removed filters based on the fields listed in keep\_only list that were removed through exclude or by resetting filters through FIXED mode.  This allows us to do something like a table exclusion in exclude, and then pull back in some specific field based filters from that table.  
5. **Apply `include`**: for filters, each expression that is added will act as its own filter.  This will not split include expressions at top-level AND the way the initial query filter will be split. For grain, add listed dimensions to the grain set.  
6. **Evaluate** the field in the resulting context.  The filters will be applied in this context, so any excluded filters will not affect them.  
7. **Propagate** the final context to any child fields referenced in the expression.

#### Exclude-First Ordering Rationale

The ordering `exclude -> keep_only -> include` means:

- You **can** replace: exclude the old value, include the new (DAX CALCULATE pattern).  
- You **can** pull back excluded values with `keep_only` which is evaluated in the parent context  
- You **cannot** remove something you just added — include happens after exclude.  
- Metric references in include expressions see the excluded context.  For concepts like year-over-year that may need pre-exclusion values, the `PRE_FILTER()` function will get the value of the expression before the exclude occurs.

The constraint "cannot remove what you just added" seems like a reasonable constraint.  Other approaches could have a more fine grained ordering of operations to enable that case, but this simplification does not seem to lose generality.

#### Keep\_only with Relative mode

The keep\_only behaviour with relative mode may initially seem counter-intuitive, but by following the same rules as keep\_only in fixed mode it can help address some issues.

Keep\_only only adds to the context (it rescues clauses from the *parent* context that were removed), so for:

- `Mode: RELATIVE + keep_only['field1']` keep\_only here is a no-op, because it will add fields that are in the original context, but RELATIVE has already included fields in the original context. The `keep_only` operation rescues from the parent context, but RELATIVE already preserved it.
- `Mode: RELATIVE + exclude['products.*'] + keep_only['products.field1']` allows us to start with the parent context and be more surgical in how we remove fields.  So in this case, we are able to remove everything from the products table, and then add back `products.field1.`
- `Mode: FIXED + keep_only['x']` clears the context entirely, then rescues only clauses matching `x` from the parent context.  If `x` is not referenced in any parent clause, the result is empty.  This contrasts with `Mode: RELATIVE + keep_only['x']` which is a no-op — the full parent context is already inherited.

A `keep\_only`-only filter spec (e.g., `filter: { keep_only: [date.date] }`) is syntactically valid and treated as unified syntax.  Without `exclude`, it is equivalent to `RELATIVE` with no modifications (a no-op).  The useful patterns are `exclude` + `keep_only` together, or `FIXED` + `keep_only`.

### Validation Rules

- `mode: FIXED` \+ `exclude` is a no-op (nothing to exclude from a fresh context).  Implementations SHOULD warn the user in non-strict mode and raise an error in strict mode, since the user almost certainly did not intend the excludes to be silently dropped.  The equivalent top-level GRAIN\_AGG combination (`FIXED(…) + EXCLUDE(…)` or `KEEP_ONLY(…) + EXCLUDE(…)`) MUST be rejected as a parse error for the same reason.
- `mode: RELATIVE` \+ no exclude \+ no include on filter is a no-op (omit).  
- `mode: FIXED` \+ no include or keep\_only on grain produces the empty grain `[]` (grand total).
- `mode: FIXED` \+ `include: [...]` \+ `keep_only: [...]` unions both:  the effective set is `include ∪ (keep_only ∩ parent_context)`.  This applies symmetrically to filter (effective set = `include ∪ rescued_from_parent`) and grain (effective dims = `include ∪ (keep_only ∩ query_dims)`).  An entry appearing in both `include` and `keep_only` is deduplicated.
- **Filter specs that alter scope require an explicit grain spec.** A metric whose `filter` uses any of `mode: FIXED`, `exclude`, or `keep_only` SHOULD declare a matching `grain` spec as well.  Filter and grain are independent properties (§5.1), but when a filter changes the *scope* of the computation, the grain almost always needs the same change to keep `scope = grain + filter` coherent at the query level.  Implementations SHOULD warn the user in non-strict mode and raise an error in strict mode when a scope-changing filter spec appears without a corresponding grain spec.  The canonical coupling is DAX `CALCULATE(SUM(…), color = "Red")` which maps to both `filter: { exclude: [color], include: ["color = 'Red'"] }` **and** `grain: { exclude: [color] }`.  EXCLUDE on grain is a no-op when the column is not a query dimension, so including it prophylactically is safe and correct.
- **Fields and metrics share the same filter surface.**  Per `OSI_Core_Abstractions.md` §3, a metric is a field at the global namespace whose expression is an aggregation.  The same `FilterSpec` properties (`mode`, `exclude`, `include`, `keep_only`) apply uniformly to both, as do `grain` and `joins`.  A dataset field that declares any scope-altering property (`mode: FIXED`, `exclude`, `keep_only`, explicit `grain`, or `joins`) is evaluated independently — semantically equivalent to a global metric with the same properties whose name the caller could substitute without changing the answer.  Implementations MAY realise this equivalence by promoting such field references to synthetic metrics before planning; the observable answer is identical either way.

### Sub-Function Aliases

The GRAIN\_AGG sub-function names were changed from `FIXED_OPTIONAL`/`FILTER_FIXED_OPTIONAL` to the spec-canonical `KEEP_ONLY`/`FILTER_KEEP_ONLY` to match the YAML property name.  Implementations **MUST** accept both spellings (the old names as legacy aliases) and **SHOULD** produce the same parsed result regardless of which spelling is used.

| Canonical | Legacy alias |
| :---- | :---- |
| `KEEP_ONLY(dim1, …)` | `FIXED_OPTIONAL(dim1, …)` |
| `FILTER_KEEP_ONLY(field1, …)` | `FILTER_FIXED_OPTIONAL(field1, …)` |

Per symmetry with the grain form, `FILTER_KEEP_ONLY` implies `mode: FIXED` on the resulting filter spec — a RELATIVE + keep\_only filter is a no-op (see §2.1 Keep\_only with Relative mode) and would not be the user's intent.

---

## 3\. GRAIN\_AGG: Inline Grain Based Calculations

### Overview

GRAIN\_AGG is a function that allows expressing grain based calculations directly in expressions without pre-defining named metrics. It complements the YAML metric definitions, and uses the same semantics and evaluation ordering.

Anything expressible with GRAIN\_AGG can also be expressed as a named metric, and vice versa.  Conceptually, it should be as if a temporary field was created, then used.

### Syntax

```
GRAIN_AGG(expression, sub_function1, sub_function2, ...)
```

The first argument is always the aggregation expression. Remaining arguments are grain or filter sub-functions in any order, categorized by name prefix.

These arguments will combine to create the equivalent of the field based grain/filter context.  Ordering of parameters also does not matter.  Having a list of parameters with an EXCLUDE after an INCLUDE is still the same as creating a context with an INCLUDE and EXCLUDE.

If there are multiple EXCLUDE, INCLUDE, FILTER\_EXCLUDE or FILTER\_INCLUDE they will append to each other.  Sub functions that set the context (FIXED/KEEP\_ONLY/RELATIVE) can only be used once.

See Evaluation Ordering for more details.

### Grain sub-functions (unprefixed)

* FIXED() — empty FIXED grain (grand total)  
* FIXED(dim1, dim2) — FIXED at declared dims  
* KEEP\_ONLY(dim1, ...) — FIXED adaptive grain (dims ∩ context dims)  
* EXCLUDE(dim1, ...) — RELATIVE exclude  
* INCLUDE(dim1, ...) — RELATIVE include

### Filter sub-functions (FILTER\_ prefix)

* FILTER\_FIXED() — ignore all query filters  
* FILTER\_FIXED('expr', ...) — fixed filter with includes  
* FILTER\_KEEP\_ONLY(field1, …) – include only filters with the listed fields in them  
* FILTER\_EXCLUDE(field1, ...) — remove specific filter clauses  
* FILTER\_INCLUDE('expr', ...) — add filter clauses

### Combining

`# Same as FIXED / keep_only on filter and grain`  
`GRAIN_AGG(SUM(field1), KEEP_ONLY(dim1), FILTER_KEEP_ONLY(dim1))`

`# Same types of sub-functions combine.  This is equivalent of a`   
`# grain of FIXED / keep_only [dim1] / include [dim2]`  
`# filter of RELATIVE / include [dim2]`  
`GRAIN_AGG(SUM(field1), KEEP_ONLY(dim1), INCLUDE(dim2),`   
          `FILTER_EXCLUDE(field2))`

### Logical Subquery Isolation

If either FILTER or grain functions are not specified, RELATIVE is auto-applied to maintain the current context.  However, calling GRAIN\_AGG should be thought of as evaluating with sub-query isolation.  So semantically, it acts as though it is a different query 

### Nesting

If nesting a GRAIN\_AGG in another GRAIN\_AGG call, the expression will run in the context created by the outer GRAIN\_AGG.  E.g.

`# Same types of sub-functions combine.  This is equivalent of a`   
`# grain of FIXED / keep_only [dim1] / include [dim2]`  
`# filter of RELATIVE / include [dim2]`  
`GRAIN_AGG(`  
  `SUM(GRAIN_AGG(count(id), KEEP_ONLY(dim1, dim2))), KEEP_ONLY(dim1),`     
  `INCLUDE(dim2), FILTER_EXCLUDE(field2))`

In this case the inner GRAIN\_AGG would have inherited the *resolved* (effective) grain of the outer GRAIN\_AGG — not the declared grain, but the actual grain after `keep_only \u2229 parent_context_dims` is evaluated.  So if the query dimensions are `[dim1, dim3]`, the outer KEEP\_ONLY(dim1) resolves to `[dim1]`, and the inner KEEP\_ONLY(dim1, dim2) inherits `[dim1]` and resolves to `[dim1]` (dim2 not in the outer's resolved context).  In addition, the inner scope inherits the outer's effective filter context (with field2 filters excluded).

PRE\_FILTER always evaluates against the inherited context of its immediately enclosing scope (step 1 of the evaluation ordering), before that scope's exclude is applied.  "Immediately enclosing scope" means the nearest GRAIN\_AGG or metric definition that contains the PRE\_FILTER call.  For nested GRAIN\_AGG, each scope has its own step 1 context: the inner GRAIN\_AGG's step 1 is the *resolved* context of the outer GRAIN\_AGG (after the outer's full evaluation ordering).  PRE\_FILTER does NOT reach past its enclosing scope to the grandparent context.

### Examples:

This function is similar to Thoughtspots, but attempts to be more SQL friendly.  Here is a set of examples showing similar Thoughtspot vs. GRAIN\_AGG cases.

| ThoughtSpot | GRAIN\_AGG |
| :---- | :---- |
| `group_aggregate(sum(S), {cust_id}, {})` | `GRAIN_AGG(SUM(S), FIXED(cust_id), FILTER_FIXED())` |
| `group_aggregate(sum(S), qg(), qf())` | `SUM(S)` (or `GRAIN_AGG(SUM(S))` — defaults) |
| `group_aggregate(sum(S), qg()-{cat}, qf())` | `GRAIN_AGG(SUM(S), EXCLUDE(cat))` |
| `group_aggregate(sum(S), qg()+{yr}, qf())` | `GRAIN_AGG(SUM(S), INCLUDE(yr))` |
| `group_aggregate(sum(S), qg()-{dt}+{yr}, qf())` | `GRAIN_AGG(SUM(S), EXCLUDE(dt), INCLUDE(yr))` |
| `group_aggregate(sum(S), qg(), qf()-{ship})` | `GRAIN_AGG(SUM(S), FILTER_EXCLUDE(ship))` |
| `group_aggregate(sum(S), qg(), qf()+{s='air'})` | `GRAIN_AGG(SUM(S), FILTER_INCLUDE('s = ''air'''))` |

### Usage in expressions

```
# Percent of total
revenue / NULLIF(GRAIN_AGG(SUM(amount), FIXED()), 0) * 100

# Parent total in hierarchy
revenue / NULLIF(GRAIN_AGG(SUM(amount), EXCLUDE(subcategory), FILTER_EXCLUDE(subcategory)), 0)

# DAX CALCULATE replace pattern
GRAIN_AGG(SUM(amount), EXCLUDE(color), FILTER_EXCLUDE(color), FILTER_INCLUDE('color = ''Red'''))
```

---

## 4\. PRE\_FILTER: Enabling Dual Context Filters

### Overview

PRE\_FILTER functions as a way to separate the outer from the inner filter context.  Although this may seem niche, it is needed to express some important patterns that show up.  One key use case is for time intelligence, where we need to calculate the time-period using the parent context, but we need to filter the rows using the child context.   We will look at period over period as an example in this section.

**NOTE:**   
We will likely want to introduce some time intelligence convenience functions.  However, as part of defining the core abstractions and semantics it is important to be able to model them on first principles.

### Syntax

```
PRE_FILTER(expression)
```

The expression can be any OSI expression, and it will be as if it were evaluated in step 1 of the filter evaluation ordering.

**Interaction with `mode: FIXED`**: Step 1 (inherit) runs before step 2 (FIXED clears the context), so PRE_FILTER always sees the inherited parent context **regardless of the enclosing metric's filter mode**.  A metric with `filter: { mode: FIXED, include: ["date >= PRE_FILTER(MIN(date))"] }` will still see the query WHERE inside the PRE_FILTER expression; only the *outer* context is cleared.  This is essential for FIXED-mode period-over-period patterns where the user wants to discard the enclosing filter but still reference the parent's aggregates to compute a shifted window.

In the case of period-over-period, this is how we get the current range.

To demonstrate this, we will model a revenue\_last\_year field that will come up with a sum of the values of the current date range, shifted by a year.  It needs to use the prefiltered values to determine the correct range, but then needs the filter cleared for evaluating against.

```
# Revenue needs PRE_FILTER to get the min & max of date.date to create the date 
# range, but then needs the cleared filter on date.date in order to actually
# evaluate the row against the filter

name: revenue_last_year
 expression: SUM(orders.amount)
 grain: { exclude: [date.date] }
 filter:
   exclude: [date.date]
   include:
     - "date.date >= DATEADD(year, -1, PRE_FILTER(MIN(date.date)))
        AND date.date <= DATEADD(year, -1, PRE_FILTER(MAX(date.date)))"
```

If we did not have PRE\_FILTER, then we get into a quandary where the existing date filter needs to get removed, so we can filter to last year.  However, we need the MIN and MAX of the date range to exist with the parent filter in order to calculate the new range..

---

## 5\. Symmetry Between Filter and Grain

The unified model makes the parallel structure explicit:

| Concept | Filter | Grain |
| :---- | :---- | :---- |
| Inherit everything | `mode: RELATIVE` (default) | `mode: RELATIVE` (default) |
| Declare from scratch | `mode: FIXED,  include: [exprs]` | `mode: FIXED,  include: [dims]` |
| Declare, adapt to query | `mode: FIXED, keep_only: [fields]` | `mode: FIXED, keep_only: [dims]` |
| Remove specific items | `exclude: [fields]` | `exclude: [dims]` |
| Add specific items | `include: [exprs]` | `include: [dims]` |
| Replace (remove \+ add) | `exclude: [field], include: ["new_expr"]` | `exclude: [dim1], include: [dim2]` |
| Clear all | `mode: FIXED` (no include) | `mode: FIXED` (no include) |

### Filter-Grain Independence

Filter and grain remain **independent, orthogonal properties** — changing one does not imply changing the other. This is consistent with many of the major BI tools (see §6).

However, the unified shape makes it easy to express operations that affect both simultaneously when needed (e.g., DAX `ALL()` which clears both filter and grain):

```
filter:
  mode: FIXED
grain:
  mode: FIXED
```

---

## 6\. Unchanged from Previous Model

### TABLE Grain

`TABLE [table_name]` is a special grain for scalars. It is orthogonal to RELATIVE/FIXED and can coexist:

```
grain:
  mode: TABLE
  table_name: lineitem
```

TABLE grain is unchanged by this proposal — it defines the natural row grain for scalar expressions that cross tables, which is a different concept from the set-operation model for aggregation grain.

---

## 7\. BI Tool Validation

### Power BI (DAX)

PowerBI conflates the grain and filters, so many of the operations will need to address both.

| DAX Operation | Proposed OSI |
| :---- | :---- |
| CALCULATE(SUM(Sales), Color \= "Red") | `filter: {exclude: [color],          include: ["color = 'Red'"]}`  `grain: {exclude: [color]}` |
| CALCULATE(SUM(Sales), ALL()) | `filter: {mode: FIXED}` `grain:  {mode: FIXED}` |
| CALCULATE(SUM(Sales), KEEPFILTERS(Color \= "Red")) | `filter: {include: ["color = 'Red'"]}`  (no grain change) |
| CALCULATE(SUM(Sales), REMOVEFILTERS(Color)) | `filter: {exclude: [color]}`  `grain: {exclude: [color]}` |
| CALCULATE(SUM(Sales), REMOVEFILTERS(Products)) | `filter: {exclude: [products.*]}` `grain: {exclude: [products.*]}` |
| CALCULATE(SUM(Sales), ALL(Products)) | `filter: {exclude: [products.*]}`  `grain: {exclude: [products.*]}` |
| ALLEXCEPT(Products, Color) (relative context, remove Products, then add back `products.color` ) | `filter: {    exclude: [products.*],    keep_only: [products.color]}`  `grain: {    exclude: [products.*],    keep_only: [products.color]}`  |
| FILTER(ALL(Products), Price \> 100\) | `filter: {exclude: [products.*],           include: ["price > 100"]}` `grain:  {exclude: [products.*]}` |
| SUMX(Customers, CALCULATE(SUM(Sales))) | `grain: {include: [customers.id]}` |

**Period-over-period** (the critical test):

```
name: revenue_last_year
 expression: SUM(orders.amount)
 grain: { exclude: [date.date] }
 filter:
   exclude: [date.date]
   include:
     - "date.date >= DATEADD(year, -1, PRE_FILTER(MIN(date.date)))
        AND date.date <= DATEADD(year, -1, PRE_FILTER(MAX(date.date)))"

```

**Grain Note**: When DAX removes filters on grouping dimensions, the grain also changes. In the proposed model, this is expressed naturally by adding `exclude` to both filter and grain:

```
# DAX REMOVEFILTERS(Products) when products.color is on the visual rows
filter:
  exclude: [products.*]
grain:
  exclude: [products.color]
```

**Table Filter Note:**  My understanding is that when DAX gets a filter on more than one column, it adds that to the table as a filter.  In this case, our filter matching logic may incorrectly match the column when it should not.  In order to address this, converters would need to see the table filter case, and then:

* Create a boolean field on the dataset with the filter expression in it  
* Add the field to the filter context

That way, functions that clear all the filters from the table, e.g. dataset\_name.\*, would still remove the filter, but operations on the individual fields used by the filter would not overly aggressively remove it.

### Tableau

| Tableau Operation | Proposed OSI |
| :---- | :---- |
| {FIXED \[color\]: SUM(qty)} | `filter: { mode: FIXED }` `grain: { mode: FIXED, include: [color] }` |
| {FIXED \[color\]: SUM(qty)} with context filter | `filter:{mode: FIXED, include: ["color='Red'"]}` `grain: {mode: FIXED, include: [color]}` |
| {INCLUDE \[year\]: SUM(qty)} | `grain: {mode: RELATIVE, include: [year]}` |
| {EXCLUDE \[color\]: SUM(qty)} | `grain: {mode: RELATIVE, exclude: [color]}` |
| Regular calc with context filter | `filter: {include: ["color = 'Red'"]}` |

Tableau's FIXED LOD \= `mode: FIXED` on both filter and grain. INCLUDE/EXCLUDE LODs \= `mode: RELATIVE` on both (grain has include/exclude, filter inherits unchanged). The mapping is direct.

### ThoughtSpot

The ThoughtSpot group\_aggregate function maps closely to this current model and the FIELD\_AGG function.

| ThoughtSpot | Proposed OSI |
| :---- | :---- |
| `group_aggregate(sum(S), {cust_id}, {})` | `grain: {mode: FIXED, include: [cust_id]}` \+ `filter: {mode: FIXED}` |
| `group_aggregate(sum(S), query_groups(), query_filters())` | (defaults — no filter/grain spec needed) |
| `group_aggregate(sum(S), query_groups()-{cat}, qf())` | `grain: {exclude: [cat]}` |
| `group_aggregate(sum(S), query_groups()+{yr}, qf())` | `grain: {include: [yr]}` |
| `group_aggregate(sum(S), qg()-{dt}+{yr_dt}, qf())` | `grain: {exclude: [dt], include: [yr_dt]}` |
| `group_aggregate(sum(S), qg(), qf()-{ship})` | `filter: {exclude: [ship]}` |
| `group_aggregate(sum(S), qg(), qf()+{ship='air'})` | `filter: {include: ["ship = 'air'"]}` |

The ThoughtSpot mixed-mode grain (`query_groups()-{dim1}+{dim2}`) maps directly to `exclude: [dim1], include: [dim2]`. This was the expressiveness gap that motivated this proposal.

### Looker

Looker's additive-only filter model maps trivially:

```
# LookML: filters: [status: "completed"]
filter:
  include: ["status = 'completed'"]
```

Looker has no grain overrides (grain comes from the query) and no filter resets. All Looker patterns are expressible with `mode: RELATIVE, include: [...]`.

---

## 8\. Errata and Questions

### 8.1 Should `exclude` on grain remove from the inherited grain or from all possible dimensions?

`EXCLUDE [color]` means "remove color from the current context idempotently." If color is not in the current dimensions, it's a no-op.  This should be the same for both filters and grain.

### 8.2 Should filter `include` be a list of strings or a single expression string?

Both are allowed, but they are semantically a little different.  When a filter is added in an include, that is the unit of filter that will be used for exclusion later on.  We will NOT do the partitioning by AND that we do for the query filter.

So, using a list of filters is the best practice to make sure they are more easily excluded later on.

9\. Appendix

### 9.1 Filter matching logic

For inclusion and exclusion there are a few approaches we could have taken:

1. Match if any field in the expression matches  
2. Match if all fields in the expression match  
3. Replace sub clauses if fields in them are reset

The current proposal uses \#1, because of the perceived simplicity.  
\#2 has a reasonable semantic, but gets complicated by a few cases:

* Users may wonder why an expression was not removed, if they forget to exclude all the columns in an expression  
* We would need to see if we need to track exclusion state across contexts, to know if one field excluded part of filter and then a later one excluded the rest

\#3 would likely get complicated quickly.  This would involve finding expressions that use the field and replacing the exact expressions.  This can get tricky when dealing with NOT operations, functions and deep hierarchies.  
