# OSI Discussion Point: Core Analytic Abstractions

**Current Status:** Draft for internal review  
**Last Updated:** 25 Feb 2026  
**Discussion on concepts to extend**: [OSI Core Metadata Specification](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md) with core analytical abstractions around grain, filters and join paths.  
**Author(s):**  will.pugh@snowflake.com (Snowflake), \<add your name\>

**Working Group**

| Lead(s) | Participants |
| :---- | :---- |
| Will Pugh, Snowflake Khushboo Bhatia, Snowflake  | LLyod Tabb, Malloy Dianne Wood, Atscale Lior Ebel, Salesforce   Quigley Malcolm, DBT Kurt, Relational AI Justin Talbot, Databricks Pavel Tiunov, Cube Damian Waldron, Thoughtspot Oliver Laslett, Lightdash Martin Traverso, Starburst |

**Relevant Ideas (from forums):**

| Idea | Relevance |
| :---- | :---- |
| [Top level "mertics" vs. dataset-level “measure”s](https://github.com/open-semantic-interchange/OSI/discussions/29) | This proposal suggests thinking about metrics as fields, and loosening up restrictions on them being aggregations or not. This allows for dataset-level “metrics” by allowing dataset fields use aggregations. |
| [Cumulative and other "expansions" to metrics](https://github.com/open-semantic-interchange/OSI/discussions/39)  | This proposal includes parameters that can override the specifiers for window functions to allow changing time windows through parameter changes |
| [Support for cross-dataset dimensions & single-dataset measures](https://github.com/open-semantic-interchange/OSI/discussions/27) | This defines the semantics for cross dataset calculations, and the properties to make them composable. |
| [Relationship Semantics](https://github.com/open-semantic-interchange/OSI/discussions/24) | Addresses the semantics of crossing relationships for many different types of relationships. |
| [Add explicit datasets reference to Metrics](https://github.com/open-semantic-interchange/OSI/discussions/18) | Proposes an implicit semantic for resolving metrics that cross tables. |
| [Add “entity / grain” as a first-class concept](https://github.com/open-semantic-interchange/OSI/discussions/12) | Proposes creating grain as a core concept for OSI |
| [Inner join in relationships](https://github.com/open-semantic-interchange/OSI/discussions/11) | Does not directly address this, but allows join type overrides at the field level |

## Overview

The OSI Core Metadata Specification defines a way to describe a schema, but does not describe enough information for how to do calculations via that schema.

This document discusses potential extensions to support the core properties needed to compose analytical calculations, and the semantics to ensure this is done in a safe and reproducible way. It focuses on the minimal set of properties needed to express and compose analytical operations.

It will only focus on the core abstractions needed for defining analytical calculations, and save other topics for later specs.

Analytical operations are broken into three main abstractions:

- **Semantic Query** is the query over the semantic model  
- **Analytical context** determines when and how the calculation is run  
- **Expression language** determines how the calculation itself is written  
- **Namespace** determines how fields are grouped and addressed from expressions

This document addresses namespacing in a later section, however, this mainly determines how a field/metric is looked up, rather than core analytical behaviour.  That is an orthogonal concept.  Instead, this ensures a table grain concept that can be used by either approach to ensure that the rows and aggregations make sense regardless of how the fields are structured.

**NOTE: For the purpose of this document, we use Field to represent either OSI fields or metrics.  They can be aggregated or non-aggregated.  This is because the base set of properties should drive the way either is evaluated.**  

### Semantic Query

This proposal will not go into specific syntax for a semantic query.  That can be addressed in later specs  However, in order to describe the behaviour of fields, it is difficult without some concept of defining a query.  We define the clauses we expect as the minimum parts of a query that will describe the clauses of a semantic query.

| Clause | Description | Field Requirements |
| :---- | :---- | :---- |
| **Dimensions** | Defines the resulting grain of the query, and therefore what the results are aggregated to.  They can be thought of as an analogy to the `group by` clause in SQL.  However, semantic queries will often require many steps in order to bring each metric to the final grain. | Scalar fields or fields aggregated to a fixed level of detail |
| **Measures** | The fields that are being aggregated.  These need to have an aggregation function associated with them. | Metrics or other fields that have an aggregation around them.  All measures are aggregated to the query's grain. |
| **Where Filter** | An expression used to filter the values before the final aggregation.  They can accept fields that are aggregated to a fixed level of detail or unaggregated fields.  However, aggregations that occur at the final grain of the query should be handled by the having clause. | Filters apply at the natural grain of the fields they are applied to.This means we can combine row and aggregation filters into the same clause.  The aggregation filters will occur at the grain of the final query LOD. Filters on Window functions will happen after the LOD results are calculated.  E.g. after measure are filtered so they logically happen after the “HAVING” stage (like WINDOW functions in SQL)  |
| **Parameters** | A set of values attached to parameter names that can be used within the query as bound parameters. | These are values that are set, and cannot be fields |
| **Order By** | list of fields to sort the results by.  The ordering needs to happen on fields in the final result. E.g. a dimension or metric.  Many semantic models support custom sort orders, OSI does not yet support this, but in the future the order by may take these into account. | This is the sort order that can use any of the fields in Dimensions or Measures. |
| **Limit** | Limits the number or results to return | This is a number for limiting results. |

The query itself will end up being broken up into many steps.  However, it will guarantee safety from chasm, gap and fanout traps through the aggregation and join rules described below.

### Analytical Context

The analytical context determines how the query is broken down into different query steps.  It is composed of a set of four properties used to define how calculations are done, and two scopes that determine which objects the properties apply to.

*NOTE*: For the purpose of this document, Fields and Metrics can be used interchangeably unless explicitly called out.

#### Scopes

The two scopes available to fields in the analytical context are:

- **Query Scope** defines the properties that were specified in the semantic query.  
- **Calculation Scope** refers to the properties that were defined directly on the field or metric.

Field scope always overrides query scope when they are both defined.

#### Properties

There are four properties that compose the analytical context and inform our query plan.  These are:

- **Grain** controls the granularity a metric is calculated to  
- **Filters** control whether the field participates in the query's filter and whether it has additional filters added  
- **Joins** control any additional behaviour for which join paths to use. This is to allow calculations to handle cases with ambiguous join paths or requirements to have INNER, OUTER semantics based on whether the calc wants all rows or only matching ones.  
- **Parameters** control how fields can be modified as part of the query for changing aspects like rolling window sizes or date ranges

Each property can be optionally added to a field or metric.  If they are not added, the defaults are based on the query scope:

| Context | Query Scope | Calculation Scope |
| :---- | :---- | :---- |
| **Grain** | Determined by query's Dimensions | Overrides via `FIXED`, `INCLUDE`, `EXCLUDE` |
| **Filters** | Query's filter/where clause (initial filter context) | Field-level `reset` and `expression`; filter context propagates through field references |
| **Joins** | Default left joins | Path disambiguation; type overrides |
| **Parameters** | Set to defaults unless overridden | Referenced via `:param_name` syntax |

These properties are meant to define the core abstractions needed for rich analytical definitions.  For many applications, the definitions should be simple.  
However, for more complicated ones these properties can be composed through defining several fields and metrics with different properties to handle complicated requirements.

### Grain (Level of Detail)

Controls the granularity at which a metric is calculated—independent of the query's dimensions.

By default, metrics calculate at the query's grain (the dimensions in the query). However, many analytical calculations require computing values at a different granularity:

- **Percent of total**: The denominator must be calculated at the grand total level (no dimensions), regardless of what the user queries  
- **Customer usage frequency**: Must be calculated per-customer, but can then show up in a dimension for a cohort analysis  
- **Subcategory as percent of category**: The category total must exclude the subcategory dimension to get the total for all categories

Grain modes:

- `QUERY` (default): Use the query's dimensions  
- `FIXED [dims]`: Calculate at exactly these dimensions, ignoring query dimensions  
- `INCLUDE [dims]`: Add dimensions to the query grain (ensures finer granularity)  
- `EXCLUDE [dims]`: Remove dimensions from the query grain (ensures coarser granularity)  
- `TABLE [table_name]`: This is a special grain to match a table.  For scalars, this defines what determines a “row”.  For aggregations, this is a shorthand for FIXED with no dimensions, so it will aggregate at the table.  

#### Grain For Scalars

Grain is commonly used to describe the dimensions a calculation is aggregated to, however, it is also useful for describing a “row” for scalars that cross tables as well.  To this, we define a concept of TABLE grain, which maps directly to a table.  Tables can always become finer by getting replicated to the existing grain of another table.  However, they cannot become coarser, without aggregation.

The rules for determining the grain of a table is:

1) Find the join path needed to include all the fields for the scalar.  By default this will be the table the physical column are on.  Otherwise, TABLE grain, will determine this.  
2) If no natural grain is able to match all columns, fail.  
3) Find the finest grain of all the tables needed for the join, and that will be the TABLE grain of this expression (and determine what a row is)

![][image1]

As an example, imagine the expression:  
effective\_line\_price \= (L\_EXTENDEDPRICE \* (1 \- L\_DISCOUNT)) \+ (O\_TOTALPRICE \* 0.01)

Needs to use both the tables LINEITEM and ORDERS.  With no set grain this will look at the join path from ORDERs to LINEITEM and fine the finest grain table.  In this case, that will be LINEITEM.  So the default grain will become TABLE\[LINEITEM\]

In addition, you could create a field that explicitly sets the grain to be a finer grain.  E.g. if an expression was CUSTOMER.NAME with grain: TABLE\[ORDERS\], it would be as if ORDER had another column that had the customer name.  This is acceptable, because name becomes finer grain.  Making an ORDERs field at the grain of CUSTOMERS would not work.

### Filter

Controls how the data used by this field or metric is filtered. Filters operate through a **Filter Context** — a propagating set of independent clauses that flows from parent to child through field references.

#### Filter Context

The filter context is the set of filter clauses that apply when evaluating a field. It starts with the query's WHERE clause and is modified by each field's filter properties as the evaluation descends through field references.

**Semantics:**

1. At the top level, the filter context is the query filter (the WHERE clause), decomposed into independent AND-separated clauses.
2. When evaluating a field:
   - If `reset` is `false` (default), the field starts with its parent's filter context.
   - If `reset` is `true`, the field starts with an empty filter context (no filters).
   - If `reset` is a list of field names, the field starts with the parent's filter context, but removes any independent clauses that contain a column reference matching a field in the reset list.
3. The field's `filter.expression` (if any) is split at top-level AND into independent clauses and appended to the context.

> **Subquery Independence Principle**
>
> If a field is referenced from multiple places with different filter contexts, it is logically evaluated independently for each context. This means the same field definition can produce different results depending on which parent references it, because each reference carries its own filter context. This is equivalent to each reference being computed in its own subquery. Implementations MUST ensure that filter context differences produce separate computation branches — never shared state between references.

#### Filter Properties

- `reset`: Controls how the field inherits its parent's filter context.
  - `false` (default): Inherit parent's filter context unchanged.
  - `true`: Clear all inherited filters. The field behaves as a precomputed value at its grain.
  - `[field_name, ...]`: Selectively remove inherited clauses containing references to listed fields.
  - `[table_name.*, ...]`: Wildcard — removes all inherited clauses referencing any column from the named dataset. Equivalent to listing every field of the table. Can be mixed with specific field names (e.g., `[products.*, orders.region]`).
- `expression`: A filter expression specific to this field/metric. Added to the context as independent AND-separated clauses.

#### Evaluation Ordering

When evaluating a field's filter context, the ordering is:

1. The field inherits its parent's filter context.
2. Any fields referenced in `filter.expression` are evaluated using the inherited (pre-reset) context, following their own filter properties. If a referenced field has no reset, it sees the full inherited context. If it has its own reset, that applies independently per subquery independence.
3. `reset` is applied to the inherited context.
4. The resolved `filter.expression` is added as an independent clause.
5. The field's main expression is evaluated in the resulting context.
6. This final context is what propagates to any child fields.

#### Independent Clauses

When `reset` contains a list of fields, clauses are removed based on the concept of **independent clauses** — clauses separated by AND that are therefore separable from one another:

| Filter Expression | Independent Clauses |
| :---- | :---- |
| `A AND B AND C` | 3 clauses: `A`, `B`, `C` |
| `A OR B OR C` | 1 clause: `A OR B OR C` |
| `A AND (B OR C)` | 2 clauses: `A`, `(B OR C)` |
| `A AND (B AND C)` | 2 clauses: `A`, `(B AND C)` — parens not flattened |
| `NOT (A AND B)` | 1 clause: `NOT (A AND B)` — NOTs are not De Morgan-ed |
| `NOT (A OR B)` | 1 clause: `NOT (A OR B)` — NOTs are not De Morgan-ed |

A clause is removed if **any** column reference within it matches a field in the reset list (after identifier normalization). Clauses combined via OR are considered dependent — removing part of an OR could reduce the row set, violating the invariant that filter removal only increases rows.

#### Examples

- **Unfiltered denominator**: For "percent of total" where the total should include all data even when the user filters to a specific region. Use `reset: true`.
- **Metric-specific filter**: Metrics that always apply certain conditions (e.g., "recent revenue" that always filters to last 30 days). Use `expression` with `reset: false`.
- **DAX CALCULATE pattern**: Replace one filter while keeping others. Use `reset: [field]` + `expression`.
- **Period-over-period**: Use `reset: [date_field]` + `expression` referencing shifted date boundaries. The boundaries (`period_start`, `period_end`) are defined as FIXED [] metrics that compute MIN/MAX of the date field — because they have no `reset`, they inherit the parent's filter context and reflect the user's current date selection. Per the evaluation ordering (step 2), the filter expression's metric references are evaluated in the pre-reset context, so they see the original date filter before the reset clears it.

### Joins

When determining the query plan for evaluating a semantic query, any of the fields needed for aggregation or filtering need to be connected through relationships. These relationships are defined in the [relationships section of the OSI spec](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md#relationships).

In many models, the relationships will be sufficient and nothing needs to be added at the field or metric.  However, there are some cases where additional refinement is needed:

- **Alternate join paths** can exist in some implementations and some models.  In these cases, there may be more than one set of relationships that can combine two fields (e.g., orders can join to users via `placed_by` or `fulfilled_by`).  
- **Including unmatched dimensions or measures** can be desired or not desired depending on what the user wants to do.  We can provide smart defaults, but there are times users will want to choose explicitly what join type they want.

These cases are addressed with the proposed properties:

- `path`: A set of relationship names that can be used for resolving the fields in this field's expression.  The order does not matter, but the join path resolution for finding any immediate fields will use only these relationships.  
- `type`: Override join type (`INNER`, `LEFT`, `RIGHT`, `FULL`)

These join properties help determine the join path, however, the actual mechanics of joining will need to follow the ones defined in the sections below to avoid traps and incorrect aggregations.

These properties are not passed down to other fields or metrics that are used.  This allows users to decompose very complicated calculations by chaining fields.

### Parameters

Configurable values that have defaults, but can be set at query time. Parameters allow metrics to be dynamic without changing the semantic model.  These are useful for cases such as:

- **Lookback periods**: "Revenue in last N days" where N is configurable  
- **Thresholds**: "High-value customers" where the threshold can be adjusted  
- **What-if analysis**: Adjust assumptions at query time

Parameters are declared in the semantic model with defaults, referenced in expressions using SQL bind parameter syntax (`:param_name`), and can be overridden when the query is executed.

### Expression Language

This specification does not take a stance on the expression language, because these properties are considered intrinsic to building a composable field model.  The actual way to express the scalar, aggregate or window expressions could be done using many different languages.

However, when building expressions, this will use an expression language that is close to ANSI SQL with some additional analytical functions folded in.

### Identifiers and Namespacing

The OSI spec currently contains three namespaces, which determine the visibility and uniqueness of each value.  Where and how a field (or metric) is defined will determine the namespace for it, which in turn determines the ways it can be addressed by other fields.

All identifiers MUST be valid names and follow ANSI SQL naming, with the size limitation of 128 characters for identifiers.  Many databases support longer identifiers, however, this number is safe for a broad number of vendors.

Regular identifiers (unquoted) should be case insensitive and resolve to upper case, so that we can have consistent matching from quoted to unquoted.  For example, an identifier id is regular, so it would match with Id or iD.  Quoting an identifier is case sensitive, so “id” would not match, but “ID” would because regular identifiers resolve to upper case.

Sometimes, we may refer to a **normalized identifier**.  This is a form the identifiers can be put in, so they can be matched easily and matches can be made with case-sensitive, exact matching.  For **normalized identifiers**:

* Regular identifiers are upper cased  
* Quoted identifiers have their quotes stripped and any escaped characters are unescaped

#### Reserved Names

ANSI disallows identifiers from being reserved names.  This standard follows that rule, but adds some additional reserved names above and beyond the ANSII ones:  
	

| \_\_GLOBAL\_\_ | Refers to the global namespace, so must not be shadowed |
| :---- | :---- |
| GRAIN | The grain property.  We may want to allow setting this inline |
| FILTER | The filter property.  We may want to allow setting this inline. Should already be reserved for SQL, because Window functions can have a similarly named field. |
| QUERY\_FILTER | The query\_filter property.  We may want to allow setting this inline. |

#### Name Spaces

Namespaces define how an identifier is looked up and determined to be unique.  The identifier rules above determine how to create a normalized name, and the namespace determines whether those normalized names resolve to the same objects.

There are three scopes which make up our namespace, with membership in each determined by where the field was defined: **Global**, **Dataset** and **Physical**.

##### Global 

Objects that are defined at the top level of the semantic model are in the Global scope.  These are from expressions without any qualifier, and can be accessed from anywhere (although other rules like grain rules still apply in how they can be used).  

In the current OSI spec, the only global scoped fields are Metrics, Parameters, Datasets and Relationships.  However, in the future there could be other sections (such as an equivalent to the fields section in a dataset).  Regardless of the heading the fields are defined in, any of those top level fields share in the same namespace, and should not be able to have the same normalized names.

**Global Metrics and Parameter fields can access other global and object fields, but NOT physical fields**.  Object fields MUST be qualified with the name of the object in order to reference the field.  E.g. store\_sales.id would reference the ID field in the STORE\_SALES object.

Global fields do not have any default settings for field properties (grain, query\_filter, filter, joins).

Relationships and Datasets need to be able to access physical fields to define keys and join fields.  These allow physical columns in case they are used for joining or uniqueness, but don’t need to be exposed directly to the user.

##### Dataset / Object

The object namespace is unique to the object the fields are defined in.  Currently, the only objects that have nested fields are Datasets.  They have a fields section to define new fields.

Fields may be defined at the dataset level.  Their identifiers MUST be unique within the dataset, but can have the same name as identifiers in other datasets, or in the global scope.

**Object fields can access logical or physical fields** within the object’s scope without requiring qualification.  The fields may also access global fields as well, which means that shadowing can occur here.  To handle these in a predictable way, names will be resolved with the following rules:

| Precedence | Field Type | Disambiguation |
| :---- | :---- | :---- |
| Highest | Physical Fields | N/A |
| Middle | Logical fields on the object | Qualifying access through the object name, will ensure getting a logical field, rather than the shadowing physical field. store\_sales.id will ensure access to the logical id field, not the physical one. |
| Lowest | Global fields or objects | Qualifying access through the \_\_GLOBAL\_\_ keyword.  This will ensure the resolution starts from the global part of the namespace. |

Fields that are created on a Dataset will default certain properties depending on whether the field is a scalar or an aggregation:

| Property | Scalar (default) | Aggregation (default) |
| :---- | :---- | :---- |
| grain | TABLE(Dataset) | None |
| filter.reset | false (inherit parent context) | false (inherit parent context) |
| filter.expression | None | None |
| join | None | None |

##### Physical

Physical fields are ones that come directly from the Dataset’s source query.  They are not directly stored in the model, but reflect what is in the actual system of record.

Physical fields are ONLY accessible from Dataset fields. 

There is no way to create Physical fields.

## Quick Reference

### Grain Modes at a Glance

| Mode | Effective Grain | Use Case |
| :---- | :---- | :---- |
| `QUERY` (default) | Query's GROUP BY | Standard metrics |
| `FIXED [dims]` | Exactly `[dims]` | Totals, benchmarks, denominators |
| `INCLUDE [dims]` | Query grain ∪ `[dims]` | Ensure finer grain before aggregation |
| `EXCLUDE [dims]` | Query grain − `[dims]` | Parent totals in hierarchies |
| `TABLE [tables]` | Grain for scalars.  Defaults to the most granular table in the join path to connect all the fields in the expression. | Scalars that cross tables define which grain they will be computed to.   |

### Filter Behaviors

| Setting | Behavior |
| :---- | :---- |
| `reset: false` (default) | Inherits parent's filter context (query WHERE + ancestor filters) |
| `reset: true` | Clears all inherited filters; acts as precomputed value |
| `reset: [field_names]` | Selectively removes clauses containing listed fields |
| `expression` | Filter always applied to this field/metric (added as AND clause) |

### Common Patterns

| Pattern | Setup |
| :---- | :---- |
| Percent of total | `FIXED []` + `reset: true` for denominator |
| Per-customer average | `INCLUDE [customer_id]` for inner, `QUERY` for outer AVG |
| Category % of parent | `EXCLUDE [child_dim]` for parent total |
| Filtered vs unfiltered | `FIXED []` + `reset: true` for unfiltered denominator |
| Replace one filter (DAX CALCULATE) | `reset: [field]` + `expression: "field = 'new_value'"` |
| Add filter without removing (DAX KEEPFILTERS) | `reset: false` + `expression: "field = 'value'"` |
| Period-over-period comparison | `reset: [date_field]` + `expression` with DATEADD on FIXED[] boundary metrics |
| Remove all table filters (DAX REMOVEFILTERS/ALL table) | `reset: [table_name.*]` — removes all clauses referencing the table |

---

## Schema Extensions

### Extended Metrics Schema

The following fields are added to the existing [Metrics schema](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md#metrics):

| Field | Type | Required | Description |
| :---- | :---- | :---- | :---- |
| `grain` | object | No | Level of detail control |
| `filter` | object | No | Filter behavior and metric-specific filter |
| `joins` | object | No | Join path disambiguation |

### Parameters Schema (Model Level)

| Field | Type | Required | Description |
| :---- | :---- | :---- | :---- |
| `name` | string | Yes | Parameter identifier |
| `type` | enum | Yes | `STRING`, `INTEGER`, `DECIMAL`, `DATE`, `TIMESTAMP`, `BOOLEAN` |
| `default` | any | No | Default value if not supplied (see below) |
| `description` | string | No | Human-readable explanation |
| `ai_context` | object | No | Context for AI assistants |

**Parameter Defaults:**

- Defaults can be **literal values** (e.g., `30`, `'2024-01-01'`, `true`)  
- Defaults can also be **scalar expressions** that don't reference fields or other parameters (e.g., `CURRENT_DATE()`, `DATEADD(month, -1, CURRENT_DATE())`)  
- Scalar expression defaults are evaluated at query time, not model definition time

---

## Expression Semantics

This section defines the precise semantics for metric evaluation.

**Core Principle**: Each metric should semantically behave as if it were computed as its own independent subquery.  Implementations are free to optimize this and each metric may be required to be broken into many sub-queries to achieve desired semantics.

### Conceptual Model

* Each metric is semantically equivalent to being its own sub-query.  
* Calculations can go from a finer grain to a coarser grain, but not the other way around.

However, this ignores the complications that come in how aggregations work across joins.  As the spec progresses, we can fill in the rules with more specificity.  However, this section discusses some of the rules for computation.

### Join Types and Aggregation Model

At a high level, each join type allows for some operations, with many-to-many (n \- n) being the most restrictive and 1-1 having basically no restrictions. To get around these, there are various techniques we can use to aggregate one side or another to create a 1-1 or break n-n joins into multiple aggregations and join steps.

One rule we have for scalars is that there needs to be a way to calculate a row value for at least one table at its original grain.  This ends up disallowing scalar operations across many-to-many joins, which we think eliminates errors.  The default is to have the grain for the scalar be the finest grain in the join path to connect all the columns.  However, the model allows for setting an explicit grain (as long as it is finer than the default).

| Join Type | Aggregations | Scalars |
| :---- | :---- | :---- |
| 1 \- 1 | Easiest type.  We can basically treat this as a single table, with no special aggregation rules needed | Computing scalars across the join is O.K. |
| 1 \- n n \- 1 | One to many joins are O.K. to aggregate (and many-to-1 can be flipped to be the same).  Anything on the “1” side can be exploded, though, so general calculations on that side are not safe, unless they only use explosion-safe aggregations or you decompose into separate queries | Computing scalars across the join is O.K. The TABLE grain defaults to the many-side table (the finest grain in the join path). An explicit TABLE grain may be set to an equal or finer table but never coarser.The joins will start with the finest grain, and join out from there. |
| n \- n | Many to many joins are generally unsafe, unless used for semi-joins (for filtering)Normally, you need some pre-aggregation to make one side not be “many” | **Computing scalars across the join is an error,** because there is no longer a 1-1 at any of the original grain levels |

### Steps for navigating joins by breaking up computation into steps

Let's take a case where we have a join between two tables: A and B.  Where:

- A has 3 columns: a\_id, a\_fact, a\_dim  
- B has 3 columns: b\_id, b\_fact, a\_id

And we have a calculation `ab_calc` which is `SUM(a_fact) / SUM(b_fact)` And we have a field `ab_scalar` which is `a_fact + b_fact.`

Given the query: `SELECT DIMENSIONS a_dim MEASURES ab_calc, SUM(ab_scalar)`

#### For a 1-1 relationship

In this case it is simple. A and B have a 1-1 relationship, so we can simply

* Join the tables  
* Calculate ab\_scalar  
* Do a SQL aggregation with the group by on a\_dim and the two calculations.

`select a.a_dim, SUM(a.a_fact) / SUM(b.b_fact), sum(a.a_fact + b.b_fact) from A join B on a.a_id = b.a_id group by 1`

#### For a 1-many relationship

In this case, the `A` table is the one side and the `B` table is the many side.  In order to avoid the explosion on the A side, it makes sense to think about `SUM(a.a_fact) and SUM(b.b_fact)` as separate metrics that are calculated, and then divided at the very end

For `ab_scalar`, the grain will be `TABLE[B]`, because it is the finest granularity of tables in the join path.  Since, we can get to this grain without any aggregations, we can simply do the join to create rows at the the table grain and aggregate to the query grain.

* Join the tables. In this operation the resulting grain is the same as for B, so it is safe for scalar operations.  
* calculate the `ab_scalar` on the result of the join  
* Aggregate `ab_scalar` on the `a_dim` dimension

**Why does this work?** Logically, this works because you can expand the A side to create a 1-1 mapping with the B side, without creating any additional rows.  Since, there are no additional rows, aggregation is safe.

For `ab_calc`, we cannot follow this path, because summing `a.a_fact` after the join would include duplicate rows because of the join explosion.  In order to handle this case, we need to aggregate both tables independently and join on grain.

* Since, the A table is where the explosion will occur, we need to aggregate this side first.  We can handle SUM(a.a\_fact) side as `a_subquery`  
  * Aggregate `SUM(a_fact)` to the grain of `a_dim` without a join  
* Table B is the many side, but does not have the dimension directly on it.  As a result, it will need to do a join with A in order to get the dimension, but use that as the grouping field.  E.g.  
  * Join the tables on the ID fields.  We need this, so that we can have `a_dim` in the same table as `b_fact`  
  * Aggregate SUM(b\_fact) to the grain of a\_dim.  Since, a\_dim comes from the 1 side, there should be no explosion.  
* Join `a_subquery` and `b_subquery` on `a_dim`, and do the scalar `/` operation

#### For a many-many relationship

In this case, neither `ab_calc` nor `ab_scalar` works for this specific example, though ab\_calc-style calculations CAN work for N:N joins when the shared dimension exists on both sides (or on a bridge table), enabling independent pre-aggregation.

**Why does `ab_scalar` not work?** We cannot calculate `ab_scalar` because the join explosion creates a result with more rows than either original table. There is no stable grain—the Cartesian product violates our rule that scalars must be computable at some original table grain. To create a row containing both `a_fact` and `b_fact`, we would need to explode both sides, meaning neither side retains its original grain.

**Why does** `ab_calc` **not work?**  
**Many to Many joins are a bit more complicated than 1-many joins.  In order for the calculation to work, it needs to be able to be broken down into different aggregations that happen before the join, so we can create a sequence of 1-many or 1-1 joins.**

**In this case, we have a problem because the dimension we are aggregating to is only on the one side, so there is not good way to get the B side to aggregate to that dimension without join explosion.**

**If this example either had dim\_a on both tables as the join keys, or had it on a join table then we could properly aggregate before joining and this would work.**

#### Cardinality Reduction Through Filtering

An important special case: a **many-to-many relationship can become 1-to-many (or 1-to-1)** when filtering constrains one side such that any columns in a unique key but not in the join columns are filtered to a single value.  This ensures that side will be the "1" side of the join.

**The Rule:** If table B has a unique/primary key of `(join_cols, filter_cols)` where:

- `join_cols` are the columns used in the relationship  
- `filter_cols` are other columns that complete the unique key

When `filter_cols` are filtered to a **single value**, then `join_cols` becomes effectively unique on the filtered result, reducing the join cardinality.

**Example: Snapshot Table**

Consider:

- `inventory_snapshots` with primary key `(product_id, warehouse_id, snapshot_date)`  
- `products` with primary key `(product_id)`  
- Relationship joins on `product_id`

| Scenario | Effective Relationship | Why |
| :---- | :---- | :---- |
| No filter on snapshots | 1-to-many | Each product has many snapshots across dates/warehouses |
| Filter: `snapshot_date = '2024-01-01'` | 1-to-1 (per warehouse) | `(product_id, warehouse_id)` is now unique in filtered result |
| Filter: `snapshot_date = '2024-01-01' AND warehouse_id = 'W1'` | 1-to-1 | `product_id` is now unique in filtered result |

With the filter applied, scalar operations become safe:

```sql
-- With snapshot_date filter: SAFE (1-to-1 after filter)
SELECT p.name, s.quantity, p.unit_cost * s.quantity AS inventory_value
FROM products p
JOIN inventory_snapshots s ON p.product_id = s.product_id
WHERE s.snapshot_date = '2024-01-01'
```

**Common Patterns Where This Applies:**

- **Snapshot/point-in-time tables**: Filter to a specific date  
- **SCD Type 2 dimensions**: Filter to `is_current = TRUE` or `effective_date <= :as_of AND end_date > :as_of`  
- **Versioned records**: Filter to `version = MAX(version)` or latest effective version  
- **Time-series lookups**: Filter to a specific timestamp

**Implementation Note:** Implementations SHOULD recognize when filters reduce join cardinality and permit scalar operations that would otherwise be disallowed. This requires analyzing whether the filter columns, combined with the join columns, form a unique key on the filtered table.

### Join-explosion safe operations

Earlier, we referred to some of the dangers of joins and how they can explode the number of values in the “1” side, which can cause incorrect aggregations.  However, there are also a set of aggregations which are explosion safe.  

As an interesting point, this list is similar to semi-additive safe operations, but are a little more restrictive.  As a rule, these are operations that work on a set of values, not on count or frequency of values:

* MIN  
* MAX  
* COUNT DISTINCT  
* ANY\_VALUE  
* ARRAY\_UNIQUE\_AGG  
* ARRAY\_UNION\_AGG

### Breaking different operation types into multiple steps

The algorithm to safely aggregate across relationships, filters and grains may need to break operations into multiple steps.  In order to do this, we need to have a clear understanding of how to compose computations safely.  Depending on the aggregation type, we may be able to aggregate as we go, or we may need to accumulate and aggregate at the end.

#### Aggregation Categories (for multi-stage computation)

| Category | Examples | Intermediate State | Strategy |
| :---- | :---- | :---- | :---- |
| **Distributive** | SUM, COUNT, MIN, MAX | Scalar | Re-aggregate directly |
| **Algebraic** | AVG, STDDEV, VARIANCE | Fixed tuple (e.g., `<sum, count>`) | Combine tuples |
| **Holistic** | MEDIAN, PERCENTILE, COUNT DISTINCT | All or distinct values | `ARRAY_AGG` or sketches |

For any of the Distributive computations, we can simply aggregate them as we go. For Algebraic, we cannot do direct computation, but we can maintain running calculations in a tuple that can be turned into the final calculation at the end. Finally, for Holistic aggregations, all we can do is accumulate the values needed for the final calculation and perform that calculation at the end.

*NOTE* If a provider does not support a safe way to aggregate across steps, and cannot calculate a metric in a single step, then it must error out of the operation, rather than provide an incorrect result.

### Order of Operations

#### Step 1: Determine Query Grain

The query grain is the set of dimensions in the query.

#### Step 2: Determine Each Metric's Effective Grain

In this step, we break out each metric.  We will look at how to calculate them all independently, leaving any consolidation to optimization done beyond the core algorithm.

Each metric needs to determine its effective grain.

| Mode | Effective Grain |
| :---- | :---- |
| `QUERY` (default) | Query grain |
| `FIXED` | Exactly the dimensions specified |
| `INCLUDE` | Query grain ∪ specified dimensions |
| `EXCLUDE` | Query grain − specified dimensions |

**Edge cases:**

- `EXCLUDE` only removes dimensions present in the query grain; any others are ignored  
- `EXCLUDE` that removes all dimensions results in `[]` (grand total), equivalent to `FIXED []`  
- `INCLUDE` with dimensions not reachable or that don't exist, result in an error

#### Step 3: Determine Each Metric's Effective Filter Context

Each metric's filter context is resolved by applying its `filter` properties to its parent's filter context:

1. Start with the parent's filter context. For top-level metrics queried directly, this is the query's WHERE clause decomposed into independent AND-separated clauses.
2. Apply the metric's `filter.reset`:
   - `false` (default): inherit all parent clauses unchanged.
   - `true`: clear all inherited clauses (empty context).
   - `[field_names]`: remove any inherited clause whose column references include a listed field (after identifier normalization).
3. If the metric has `filter.expression`, split it at top-level AND and append each piece as an independent clause.

The resulting filter context is what applies to this metric's data and what propagates to any child fields it references. Metrics with different effective filter contexts are placed in separate computation branches.

#### Step 4: Determine required intermediate LODs

Look through the filters and sub-expressions to come up with a list of the intermediate LODs needed in order to calculate the results.

Sometimes, an expression may use a field that is at a different grain than where the expression is being evaluated.

| Expression Grain | Referenced Grain | Handling |
| :---- | :---- | :---- |
| Coarser | Finer | User must wrap referenced field in an explicit aggregation (e.g., `SUM(inner_metric)`) |
| Finer | Coarser | Value is replicated for each row |
| Same | Same | Direct substitution |

**Re-aggregation rules:**

- It is possible for the user to do analytically questionable operations when going from coarser to finer metrics.  For example, taking an average of an average.  Implementations MAY warn on save or edit for these types of aggregations.  They MAY also support a strict mode to disallow them.

**NOTE** Perhaps the expression language should offer some type of `ACCUMULATE()` function that can wrap another calculation and tell the engine not to fully do the calculation, but rather to store enough intermediate state to finish it later.  Similar to how we implicitly do calculations in the aggregation section.

#### Step 5: Generate Join / Aggregation Plan

- Follow rollup order: finest grain → coarsest grain  
- Use aggregate-before-join for correctness  
- See [Join Types and Aggregation Model](https://docs.google.com/document/d/1MKNySGmEv_C6CzBZ7um9Ym3_mMvmOolpDuwPvRzQ1bo/edit#join-types-and-aggregation-model) for details

**Join safety rules:**

- **Chasm trap avoidance**: If metrics reference multiple fact tables that only connect through shared dimensions, compute each fact independently, then join on shared dimensions.  
- **Fan-out detection**: If a join would multiply rows and corrupt aggregates, compute the finer-grained side first as a subquery.  
- **Join type overrides**: If `joins.type` is specified, apply it and ensure NULL handling is safe (e.g., `COALESCE` in expressions).

#### Step 6: Execute and Compose Results

Create the final SQL query implementing the join/aggregation plan and execute it.

### Semantic Guarantees

1. **Independence**: Each metric computed as if in its own subquery  
2. **Determinism**: Same query \+ data \= same results  
3. **Grain isolation**: Metric's grain affects only that metric  
4. **Filter context propagation**: A field's filter context is determined by its parent's context plus its own `reset`/`expression` properties. Different reference paths produce independent filter contexts (subquery independence).  
5. **Composition safety**: Metrics can safely reference other metrics  
6. **Filter-grain independence**: Filter and grain are orthogonal — changing one does not imply changing the other. See below.

### Filter-Grain Independence Principle

Filter and grain control different aspects of metric evaluation:

- **Grain** controls **grouping** (SQL `GROUP BY`). It determines which rows are combined into a single result row.
- **Filter** controls **selection** (SQL `WHERE`). It determines which rows participate in the computation.

Changing one does **not** imply changing the other:

- `EXCLUDE [color]` does **not** imply `reset: [color]`. You may want to stop grouping by color while still filtering on it. Example: "total Red revenue, not broken out by color" uses `EXCLUDE [color]` with a color filter — the filter restricts to Red, the grain aggregates across all color groups.
- `reset: [field]` does **not** imply any grain change. The DAX CALCULATE pattern (`reset: [color]` + `expression: "color = 'Red'"`) replaces a filter at the same query grain — no grain override needed.
- `reset: true` **commonly co-occurs** with `FIXED` grain (e.g., `FIXED []` + `reset: true` for unfiltered grand totals), but this is a pattern, not a rule. A metric may reset filters at `QUERY` grain to compute "same grouping, different data scope" (the DAX `CALCULATE(SUM(Sales), ALL())` pattern). Implementations SHOULD warn when `reset: true` has no explicit grain, as this is usually unintentional.

This independence is consistent across all major BI tools:

| Tool | Grain and filter independent? | Notes |
| :---- | :---- | :---- |
| Tableau | Yes | `EXCLUDE [dim]` with a dim filter still applies the filter. `FIXED` always resets filters (coupled by convention, not by grain logic). |
| DAX | Yes | `CALCULATE` modifies filters without changing the evaluation context's grain. |
| ThoughtSpot | Yes | `group_aggregate` takes grain (2nd arg) and filter (3rd arg) as independent parameters. `query_filters()-{col}` removes a filter without affecting grain; `query_groups()-{dim}` removes a grain dimension without affecting filters. |
| Looker | Yes | Derived table SQL has independent `GROUP BY` and `WHERE` clauses. |

---

### Non-Decomposable Aggregations

Some aggregation functions cannot be correctly computed by simply re-aggregating scalar results. Implementations must either compute them at the target grain directly or use an appropriate accumulator when intermediate aggregation is required.

Below are some examples, but see th [Advanced: Join Correctness](https://docs.google.com/document/d/1MKNySGmEv_C6CzBZ7um9Ym3_mMvmOolpDuwPvRzQ1bo/edit?tab=t.0#heading=h.k2aep3cstwfy) section for a more precise coverage.

| Function | Scalar Decomposable? | With Accumulator? |
| :---- | :---- | :---- |
| `SUM` | ✅ Yes | N/A |
| `COUNT` | ✅ Yes | N/A |
| `MIN`, `MAX` | ✅ Yes | N/A |
| `AVG` | ⚠️ Partial | ✅ With `<sum, count>` pair |
| `STDDEV`, `VARIANCE` | ⚠️ Partial | ✅ With `<sum, sum_sq, count>` |
| `COUNT DISTINCT` | ❌ No | ✅ With set or sketch accumulator |
| `MEDIAN` | ❌ No | ✅ With sorted list or t-digest |
| `PERCENTILE` | ❌ No | ✅ With sorted list or t-digest |

**Implementation guidance:**

- Prefer single-stage aggregation at the target grain when possible.  
- When multi-stage aggregation is required, use a proper accumulator type.  
- If no correct accumulator exists and there is no way to compute the value in one step, the system MUST fail with a clear error rather than return incorrect results.

### Query Filters and Result Set

Query filters always determine which rows are returned. Metrics with `query_filters: EXCLUDE` still compute across all data, but the output rows are scoped to the query grain.

Example: Query filters to `region = 'West'`:

- Metrics with `query_filters: INCLUDE` only use West rows.  
- Metrics with `query_filters: EXCLUDE` use all regions, but results are still returned only for rows present in the query result set.

---

### Edge Cases and Validation Rules

| Condition | Handling |
| :---- | :---- |
| Circular metric references | Validation error |
| `INCLUDE` with non-existent dimension | Validation error |
| `EXCLUDE` with dimensions not in query grain | Ignored (no-op for those dimensions) |
| Re-aggregation without explicit aggregation function | Validation error |
| Non-decomposable aggregation with incompatible join | Query error |
| Window function with `FIXED` grain | Window operates within the fixed-grain result set |
| Multiple filter expressions | Combined with AND |
| Parameter not declared but referenced | Treated as SQL bind parameter (must be supplied) |
| NULL values in grain dimensions | Standard SQL: NULLs group together |

**Validation timing**:

- The specification does not mandate when validation occurs.  
- Recommended: validate at model save/load time for early error detection.  
- At runtime: only report errors if the problematic calculation is required (lazy evaluation).

---

## Grain (Level of Detail)

Controls the granularity at which a metric is calculated.

### Schema

| Field | Type | Required | Description |
| :---- | :---- | :---- | :---- |
| `mode` | enum | Yes | `QUERY`, `FIXED`, `INCLUDE`, `EXCLUDE` |
| `dimensions` | array | Conditional | Field references for non-QUERY modes |

### Mode Definitions

| Mode | Behavior | Example |
| :---- | :---- | :---- |
| `QUERY` | Use query's GROUP BY (default) | Standard metrics |
| `FIXED` | Exactly these dimensions | `FIXED []` \= grand total |
| `INCLUDE` | Add to query grain | Ensure customer-level before AVG |
| `EXCLUDE` | Remove from query grain | Category total (exclude subcategory) |
| `TABLE` | Pre-aggregated grain | Creating a scalar expression that will be used for aggregations in a later phase. |

### Examples

```
# Grand total (FIXED [])
- name: total_revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: FIXED
    dimensions: []

# Average per customer (INCLUDE)
- name: customer_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: INCLUDE
    dimensions: [customers.id]

- name: avg_customer_value
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: AVG(customer_total)

# Parent total in hierarchy (EXCLUDE)
- name: category_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: EXCLUDE
    dimensions: [products.subcategory]
```

---

## Filter

Controls how fields and metrics interact with the filter context and defines field/metric-specific filters. The filter context is a propagating set of independent clauses flowing from parent to child through field references. See the **Filter** section under **Analytical Context** for full semantics.

### Schema

| Field | Type | Required | Description |
| :---- | :---- | :---- | :---- |
| `reset` | `false` \| `true` \| `[field_names]` | No | Controls filter context inheritance. `false` (default) = inherit all; `true` = clear all; list = selectively remove clauses containing listed fields. Supports `table_name.*` wildcard to remove all clauses from a table. |
| `expression` | object | No | Filter expression added to this field/metric's context |

At least one property must be meaningful: a filter with `reset: false` and no `expression` is a no-op and should be omitted.

### Examples

```
# Metric with its own filter expression (inherits query filters)
- name: recent_revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  filter:
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: orders.order_date >= DATEADD(day, -30, CURRENT_DATE())

# Unfiltered total (for percent of total)
- name: total_unfiltered
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  filter:
    reset: true

# Replace color filter (DAX CALCULATE pattern)
- name: red_revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  filter:
    reset: [products.color]
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: products.color = 'Red'

# Period-over-period helper metrics: capture the current filter context's
# date boundaries. These inherit the parent's filter context (no reset),
# so they reflect the user's actual date selection.
- name: period_end
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: MAX(date.date)
  grain:
    mode: FIXED
    dimensions: []

- name: period_start
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: MIN(date.date)
  grain:
    mode: FIXED
    dimensions: []

# Last year's revenue: resets the date filter, then applies a shifted
# date range. Per evaluation ordering (step 2), period_start and
# period_end are evaluated in the pre-reset context — they see the
# original date filter. Then reset clears the date filter, and the
# expression re-filters to the same period one year earlier.
- name: revenue_last_year
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  filter:
    reset: [date.date]
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: "date.date >= DATEADD(year, -1, period_start) AND date.date <= DATEADD(year, -1, period_end)"
```

---

## Joins

Disambiguates join paths and overrides default join behavior.

### Schema

| Field | Type | Required | Description |
| :---- | :---- | :---- | :---- |
| `path` | array | No | Set of relationship names that can be used (order not significant) |
| `type` | enum | No | Override: `INNER`, `LEFT`, `RIGHT`, `FULL` |

### Example

```
# Given relationships: order_placed_by, order_fulfilled_by (both orders → users)
- name: orders_by_placer_region
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: COUNT(orders.order_id)
  joins:
    path: [order_placed_by]  # Disambiguate which users table
```

---

## Parameters

Configurable values that can be set at query time.

### Schema

```
parameters:
  - name: lookback_days
    type: INTEGER
    default: 30
    description: Days to look back for recent metrics
```

### Behavior

1. **Declared parameters**: Use default unless overridden at query time  
2. **Undeclared `:param`**: Treated as SQL bind parameter (must be supplied)  
3. **Reference syntax**: Standard SQL bind parameter (`:param_name`)

### Usage Example

```
parameters:
  - name: lookback_days
    type: INTEGER
    default: 30

metrics:
  - name: recent_revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.amount)
    filter:
      expression:
        dialects:
          - dialect: ANSI_SQL
            expression: "orders.order_date >= DATEADD(day, -:lookback_days, CURRENT_DATE())"
```

---

## Metric References & Composition

Metrics can reference other metrics by name. The semantic layer resolves references before evaluation.

### Resolution Rules

1. Identifiers first matched against metric names  
2. If no match, treated as `dataset.field` references  
3. Circular references are validation errors

### Composition Rules

**Grain**: Outer metric's grain takes precedence.

| Outer Grain | Inner Grain | Behavior |
| :---- | :---- | :---- |
| Coarser | Finer | Wrap inner in aggregation: `SUM(inner_metric)` |
| Finer | Coarser | Inner value replicated per row (See grain composition joins) |
| Same | Same | Direct reference(See grain composition joins) |

**Filters**: Each metric retains its own filter behavior; filters are not inherited.

### Example

```
- name: revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)

- name: total_revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: FIXED
    dimensions: []

- name: pct_of_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: revenue / NULLIF(total_revenue, 0) * 100
```

---

## Complete Example

```
semantic_model:
  - name: sales_analytics
    description: Sales analytics with analytical calculations
    
    parameters:
      - name: lookback_days
        type: INTEGER
        default: 30
    
    datasets:
      - name: orders
        source: sales.public.orders
        primary_key: [order_id]
        fields:
          - name: order_id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: order_id
          - name: customer_id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: customer_id
          - name: amount
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: amount
          - name: region
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: region

      - name: customers
        source: sales.public.customers
        primary_key: [id]
        fields:
          - name: id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: id
          - name: segment
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: segment

    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]

    metrics:
      # Base metric
      - name: revenue
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount)

      # Grand total for ratios
      - name: total_revenue
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount)
        grain:
          mode: FIXED
          dimensions: []

      # Percent of total
      - name: pct_of_total
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: revenue / NULLIF(total_revenue, 0) * 100

      # Per-customer value (INCLUDE ensures customer grain)
      - name: customer_revenue
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount)
        grain:
          mode: INCLUDE
          dimensions: [customers.id]

      # Average customer value
      - name: avg_customer_value
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: AVG(customer_revenue)
```

---

## Out of Scope & Backward Compatibility

This specification focuses on **analytical expressiveness**—the core abstractions needed to define and compose complex analytical calculations. It intentionally does not attempt to cover all metadata that might be useful for analytics.

### Items Explicitly Out of Scope

- **Display metadata**: Formatting, labels, descriptions (covered by OSI Core)  
- **Data types and validation**: Type constraints, allowed values (covered by OSI Core)  
- **Hierarchies and drill paths**: Navigation structures for BI tools  
- **Time intelligence shortcuts**: Pre-built period-over-period functions (can be expressed with parameters and filters)  
- **Row-level security**: Access control policies

### Semi-Additive Metrics

Semi-additive metrics (measures that should only be summed across certain dimensions, such as inventory snapshots or account balances) represent a boundary case for this specification:

- **Supported**: The constructs in this specification enable semi-additive use cases. For example, a snapshot balance metric can use `FIXED` grain to aggregate only across the time dimension while preserving entity granularity.  
- **Not proposed**: Safety guardrails that restrict which aggregations are valid for semi-additive metrics. Users can create metrics that aggregate incorrectly across time if they are not careful.

Future specifications may address semi-additive guardrails, but the current focus is on enabling expressiveness while leaving validation to implementation-specific tooling.

### Backward Compatibility

This specification extends the OSI Core Metadata Specification. Models that do not use the analytical context properties (`grain`, `filter`, `joins`) remain valid and behave with default semantics (query grain, include query filters, default join paths).

## Advanced: Nested LOD Calculations

When a metric with a grain specification references another metric that also has a grain specification, each is evaluated independently at its own grain.

### Core Principle

**Each metric's grain is "locked in" and computed independently.** Nesting does not change either metric's grain—they are computed separately and then composed.

### Evaluation Order

Nested LODs are evaluated **inside-out**:

1. Identify innermost LOD metric(s)  
2. Compute each at its specified grain  
3. Move outward, using inner results  
4. Continue until all resolved

### Example: Customer vs Segment Comparison

```
# Level 1: Per-customer LTV
- name: customer_ltv
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: FIXED
    dimensions: [customers.id]

# Level 2: Segment average (aggregates customer values)
- name: segment_avg_ltv
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: AVG(customer_ltv)
  grain:
    mode: FIXED
    dimensions: [customers.segment]

# Level 3: Customer vs their segment
- name: customer_vs_segment
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: customer_ltv / NULLIF(segment_avg_ltv, 0)
  grain:
    mode: FIXED
    dimensions: [customers.id]
```

**Evaluation:**

1. `customer_ltv` at `[customer_id]` → one value per customer  
2. `segment_avg_ltv` at `[segment]`, averaging customer values  
3. `customer_vs_segment` at `[customer_id]`:  
   - `customer_ltv`: same grain → direct use  
   - `segment_avg_ltv`: coarser grain → replicated per customer

### Grain Interaction Matrix

When outer references inner (both FIXED):

| Outer Grain | Inner Grain | Handling |
| :---- | :---- | :---- |
| `FIXED []` | `FIXED [customer]` | Re-aggregate all customers |
| `FIXED [segment]` | `FIXED [customer]` | Re-aggregate customers per segment |
| `FIXED [customer]` | `FIXED [segment]` | Replicate segment value per customer |
| `FIXED [customer]` | `FIXED []` | Replicate grand total per customer |

### INCLUDE and EXCLUDE in Nesting

`INCLUDE` and `EXCLUDE` are **relative** to the outer context; `FIXED` is **absolute**.

**INCLUDE Example** (Query grain \= `[region]`):

```
- name: customer_order_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: INCLUDE
    dimensions: [customers.id]
  # Effective grain: [region, customer_id]

- name: avg_customer_order
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: AVG(customer_order_total)
  # QUERY mode → grain: [region]
  # Averages customer values within each region
```

**EXCLUDE Example** (Query grain \= `[category, subcategory]`):

```
- name: category_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: EXCLUDE
    dimensions: [products.subcategory]
  # Effective grain: [category]

- name: subcategory_pct
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount) / NULLIF(category_total, 0) * 100
  # Each subcategory as % of its category
```

### Filter Context in Nested LODs

Each metric's filter context is determined by its own `reset`/`expression` properties applied to its parent's context. With `reset: true`, a metric starts with an empty context regardless of what its parent sees:

```
- name: customer_total_ltv
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: FIXED
    dimensions: [customers.id]
  filter:
    reset: true  # All-time value — ignores all inherited filters

- name: customer_recent_value
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  grain:
    mode: FIXED
    dimensions: [customers.id]
  # No filter — inherits parent's filter context (query WHERE)

- name: recent_pct_of_total
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: customer_recent_value / NULLIF(customer_total_ltv, 0)
  # With query filter "date >= 2024":
  # - customer_recent_value: inherits "date >= 2024" from query context
  # - customer_total_ltv: reset: true → sees no filters → ALL orders
```

---

## Advanced: Join Correctness & Traps

### Core Principle: Aggregate Before Join

**The cardinal rule**: Compute aggregations at their natural grain BEFORE joining to tables at different grains.

**Why this matters:**

```sql
-- WRONG: Join first, aggregate second
SELECT c.region, SUM(o.amount), COUNT(c.id)
FROM customers c JOIN orders o ON c.id = o.customer_id
GROUP BY c.region
-- Problem: Customer with 5 orders is counted 5 times!

-- CORRECT: Aggregate first, join second
WITH customer_orders AS (
  SELECT customer_id, SUM(amount) AS total
  FROM orders GROUP BY customer_id
)
SELECT c.region, SUM(co.total), COUNT(DISTINCT c.id)
FROM customers c
LEFT JOIN customer_orders co ON c.id = co.customer_id
GROUP BY c.region
```

### Rollup Order

Aggregate from finest grain to coarsest:

```
[customer, product, date] → [customer, date] → [customer] → [region] → []
(finest)                                                            (coarsest)
```

### Single-Stage vs Multi-Stage Aggregation

**Single-stage** (safe when):

- All metrics share the same effective grain  
- Joins follow many-to-one relationships (aggregating from "many" side)  
- Dimension attributes are lookups only

```sql
-- Single-stage: Orders (many) → Customers (one)
SELECT c.region, SUM(o.amount), COUNT(o.order_id)
FROM orders o JOIN customers c ON o.customer_id = c.id
GROUP BY c.region
```

**Multi-stage** (required when):

- Aggregating from BOTH sides of a relationship  
- Multiple fact tables (chasm trap)  
- Different grains requiring separate computation  
- Many-to-many joins

**How to implement multi-stage** depends on aggregation category:

| Category | Examples | Intermediate State | Strategy |
| :---- | :---- | :---- | :---- |
| **Distributive** | SUM, COUNT, MIN, MAX | Scalar | Re-aggregate directly |
| **Algebraic** | AVG, STDDEV, VARIANCE | `<sum, count>` tuple | Combine tuples, derive final |
| **Holistic** | MEDIAN, PERCENTILE, COUNT DISTINCT | All values | `ARRAY_AGG` or sketches |

### Analytical Traps

#### Chasm Trap

Two fact tables through shared dimensions:

```
Orders ←→ Products ←→ Returns
```

**Solution**: Compute each fact independently, then join:

```sql
WITH order_metrics AS (
  SELECT product_id, SUM(amount) AS revenue FROM orders GROUP BY product_id
),
return_metrics AS (
  SELECT product_id, SUM(amount) AS returns FROM returns GROUP BY product_id
)
SELECT COALESCE(o.product_id, r.product_id), o.revenue, r.returns
FROM order_metrics o
FULL OUTER JOIN return_metrics r ON o.product_id = r.product_id
```

#### Fan-Out Trap

One-to-many join before aggregation causes row duplication:

```sql
-- Customer with 3 orders appears 3 times
-- SUM works, but COUNT(customer) is wrong
```

**Solution**: Aggregate at natural grain first, then join.

### Join Type Selection

When looking at join type selection, it is important to think about a few places that are relevant to choosing consistent joins.  Having deterministic joins is critical in order to have different implementations to be able to return the same results.

In addition, this specification defines ways to override the join types in most areas so that different tools make different choices and get their correct behaviour. We expect that we will ultimately need to extend the relationships to add defaults there as well (similar to Tableau referential integrity settings).

The system uses different default join types depending on the context of the join. There are four distinct contexts where joins occur, each with its own rules:

1. **Aggregation joins** — joining tables to resolve fields needed for a metric's expression or grouping dimensions  
2. **Grain composition joins** — composing metric branches computed at different grains into a single result  
3. **Filtering joins** — semi-joins used for filter evaluation (e.g., `IN (SELECT ...)`)  
4. **Scalar joins** – joining tables to resolve pre-aggregation rows

#### Aggregation Joins (Resolving Fields)

When the planner joins tables to bring together the columns needed for a metric's expression, the default join type depends on the relationship direction:

| Scenario | Default Join Type | Reasoning |
| ----- | ----- | ----- |
| Many-side enriched with one-side (N:1) | LEFT | Preserve all many-side rows; unmatched get NULLs for one-side columns. This is the standard fact-to-dimension pattern. |
| One-to-one (1:1) | LEFT | Safe in either direction; preserve the primary side's rows. |
| Pre-aggregated fact to pre-aggregated fact (shared dim) | FULL OUTER | Neither side should lose rows — a product with orders but no returns should still appear, and vice versa. |
| Scalar operation that crosses rows | LEFT | Conceptually, we want the finest grained table to define the grain.  So we start with that and do left joins out to ensure at the end we have exactly one row for each row at the finest grain. |

**Why LEFT and not INNER?** The default is LEFT to avoid silently dropping rows. If a fact row has no matching dimension (e.g., an order with an unknown customer), an INNER join would exclude it entirely — hiding data quality issues rather than surfacing them. LEFT joins preserve all primary-side rows, producing NULLs for unresolved dimensions, which makes missing data visible in results.

**The `joins.type` override applies here.** When a metric specifies `joins: { type: INNER }`, it overrides the default join type for the aggregation joins used to resolve that metric's fields. This is useful for:

* **Entitlement filtering**: One way to filter out entries based on another table, such as an entitlement table is to ensure an `INNER` join into an entitlements table.  However, for this the more ergonomic way will likely be to add a semi-join operation like EXISTS\_IN that can be used in the expression language..  
* **Existence checks**: `INNER` join to ensure only rows with matching records in another table are counted  
* Full value totals:  `OUTER` ensures you don’t lose rows.  If you want a totals value that includes all rows, it may want to do an OUTER join, even if the denominator may use an `INNER` or `LEFT` join.

The `joins.type` property does NOT affect LOD composition joins (see below) — those are always determined by the grain relationship between branches.

#### Grain Composition Joins (Combining Branches)

This document proposes a correct way to move results from one grain to another.  These grain transitions happen in a few use cases:

* Expressions that have the grain set, which are used by other aggregations.  
* Expressions with grain set coming to the grain of the query for final results

In these cases, the target grain is always determined to the 

| Scenario | Join Type | Why |
| :---- | :---- | :---- |
| Calculation that uses a calculation from another grain | Left join on the outer calculation side. | In this case, the outer calculation is defining the target grain.  Therefore, it should not be losing rows.**Join override should  be able to override this, since, it is still a property of the join.** |
| Combining results at a shard grain (shared dimension, final results) | Outer | In this case, we have the results calculated and don’t want to lose values.  So, outer is the correct result.One important sub-case here is where the common grain is empty, in which case this is a cross join. **NOTE:  These are not overridable by the join overrides in fields, because they occur at result combinations, so no field properly directly matches** |

**Scalar composition (empty-grain branches):** When one branch has an empty grain (e.g., FIXED \[\] grand total), there are no shared dimensions to join on. Following the algorithm, this would be an outer join on the empty set of dimensions, so it reduces to a **CROSS JOIN** — the scalar value is replicated to every row of the other branch. This is correct because a grand total is a single value that applies uniformly.

#### 3\. Filtering Joins

Semi-joins used for filter evaluation are an important part of SQL and analytics.  There is no direct way to do this in the core abstractions, even though the semantics and correctness guarantees are described in earlier sections.

This document suggests adding an OSI `EXISTS_IN` function. That allows a semi-join for filtering against fields (or sets of fields).  This should allow filtering against sub-queries through fields at a defined grain, to allow for entitlement or other types of complicated filtering.  `NOT EXISTS_IN` should properly convert to an anti-semi join.

**Syntax in query filters:**

```
EXISTS_IN(outer_col, dataset.field)       # semi-join: keep matching rows
NOT EXISTS_IN(outer_col, dataset.field)   # anti-semi-join: exclude matching rows

EXISTS_IN(outer_col1, dataset.field1, 
          outer_col2, dataset.field2)     # multi-column

```

| Filter Expression | SQL Compiled Form | Effect |
| ----- | ----- | ----- |
| `EXISTS_IN(col, ds.field)` | `WHERE EXISTS (SELECT 1 FROM ds WHERE outer.col = ds.field)` | Keep matching rows; no duplication |
| `NOT EXISTS_IN(col, ds.field)` | `WHERE NOT EXISTS (SELECT 1 FROM ds WHERE outer.col = ds.field)` | Exclude matching rows |

**Why an OSI function, not SQL syntax:**

Raw SQL subqueries (`col IN (SELECT field FROM table)`) in filter expressions are **not supported**, and are instead built on the core field abstractions.

Filtering joins are distinct from aggregation joins — they never cause row duplication or fan-out, and they don't contribute columns to the result.

#### Scalar Joins

When a scalar uses values from multiple tables, we will need to do a scalar join in order to connect them.  The algorithm is described earlier in the document:

* Find the finest grain dataset (either implied or explicitly set through the grain)  
* Start with that dataset and join out, until all columns are added

The default behaviour is that the finest grain is the target grain.  This means, we would like to **maintain one row for each original row in that dataset** for aggregations (or returning rows).  As a result, **the default join type is LEFT join**, in order to achieve this behaviour.

However, the joins field property will be adhered to for scalars.  So, in the case that the author wants to define the rows based on having values for all the included fields, those can be accomplished. 

##### Nested Scalars: Outermost Join Type Wins

Unlike aggregation metrics (which are semantically independent subqueries with their own GROUP BY), TABLE-grain scalars are **column expressions computed on a shared row set**.  There is no aggregation boundary that forces them into separate computations.  This means that when one scalar references another, they share the same underlying joined row set.

The rule is: **the outermost scalar's `joins.type` controls the join type for all enrichment joins in its computation, including those needed by inner scalars it references.**

**Why this rule:**

1. **Scalars are not subqueries.**  An aggregation metric like `SUM(orders.amount)` at `FIXED [region]` genuinely requires its own GROUP BY — it must be an independent computation.  A scalar like `orders.amount + customers.credit_limit` is just a row-level column expression.  It needs a join to bring in `credit_limit`, but that join is part of building the row set, not an independent query.

2. **One join per table.**  If two scalars at the same TABLE grain both reference `customers` — one with `INNER` and one with `LEFT` — the "each uses its own" approach would join `customers` twice with different join types.  This produces two separate CTEs for the same table, which is confusing and wasteful.  The "outermost wins" approach joins `customers` once, with one join type determined by the consuming context.

3. **Matching values are identical regardless of join type.**  For equi-joins, rows that match produce the same column values under LEFT, INNER, FULL, or RIGHT.  The only difference between join types is **which rows survive** — and the outermost metric should control that, because it defines the computation being performed.

4. **Predictable mental model.**  The user thinks: "When I write a metric, `joins.type` controls the row set for MY computation.  All the scalars I reference are computed on MY row set."  This matches how you would write it in SQL: one FROM clause with joins, multiple columns in SELECT.

**What "outermost" means:**

- If a scalar is queried directly (as a measure or in an expression at query level), its own `joins.type` is the outermost — it controls the row set.
- If a scalar is referenced inside another scalar's expression, the referencing scalar's `joins.type` is the outermost.
- If neither specifies `joins.type`, the default is `LEFT`.

When a join type is chosen, that join type will be used to connect all the tables for coming up with the pre-aggregated values.  Although, this join path will include internal tables, the practical implications are:

| Join type | Functional Usage |
| :---- | :---- |
| Inner | Reduces rows on finest grain to only include ones that can contain all included values from other tables. |
| Left | Maintains the number of rows from the finest grain table |
| Outer | May increase row count to include all included values, whether or not they have a mapping on the initial grain |
| Right | Least analytically useful.  Can either increase or decrease rows, but will include the outermost included dimensions. |

#### Summary: Default Join Type by Context

| Context | Determined By | Overridable? |
| ----- | ----- | ----- |
| Aggregation joins | Relationship direction \+ `joins.type` | Yes — via `joins: { type: INNER }` on the metric |
| LOD composition joins | Grain relationship between branches | Sometimes — mathematically determined for combining results, pulling a different LOD into a finer grain follows Scalar rules. |
| Filtering joins | Filter classification (semi/anti-semi) | No — always EXISTS/NOT EXISTS |
| Scalar joins | Finest grain defines starting point, joining out from there. | Yes. |

[image1]: <data:image/png;base64,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>