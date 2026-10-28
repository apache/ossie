<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# Wide Table Query Interface for Ossie Models

**Draft specification** · Apache Ossie `0.2.0.dev0` · **Layer 3** of the [layered query interface](spec_layers.md), built on the [Layer 2 Relational Query Interface](relational_semantics.md).

This specification defines how an engine answers queries that name dimensions, metrics, and a filter over an Ossie model.

---

## 1. Introduction

### 1.1 Purpose

Layer 2 exposes Ossie datasets as SQL relations. Consumers choose the joins and evaluate measures with `AGG()`.

Layer 3 provides a simpler interface for agents, ad-hoc queries, and BI tools without their own data model. A query names dimensions, metrics, and an optional filter; the engine chooses the joins. It lowers the request to a Layer 2 query, evaluates each metric through `AGG()`, and returns the result (Section 5).

Layer 3 queries contain neither `AGG()` calls nor measure columns. Instead, a consumer sees a **wide table** with a column for every field and metric (Section 3.1). This is a logical interface, **not** a SQL relation: the groups returned can depend on which metrics are requested (Section 6.1).

This specification defines query semantics, not transport syntax. A binding defines how queries and results are exchanged, for example through JSON (Section 3.7) or, in a future version, a SQL table function (Appendix A.2).

### 1.2 Goals

This version covers the shared-dimension case: metrics whose datasets reach common dimensions through many-to-one relationships, including star schemas and multiple fact tables sharing dimensions. Ambiguous or unsupported cases raise errors rather than rely on guesses.

Future versions can add options to queries, metrics, or relationships to express BI-specific behavior (Appendix A). Their defaults must preserve the results of queries accepted by this version.

### 1.3 Example

`orders` and `returns` both relate to `customers`. Each defines a dataset-scoped metric: `orders.total_revenue` and `returns.total_returns`, both based on `SUM(amount)`. To query both by region:

```json
{
  "dimensions": ["customers.region"],
  "metrics": ["orders.total_revenue", "returns.total_returns"]
}
```

Illustrative Layer 2 lowering:

```sql
WITH b_orders AS (
  SELECT c.region, AGG(o.total_revenue) AS total_revenue
  FROM   orders o LEFT JOIN customers c ON o.customer_id = c.customer_id
  GROUP BY c.region
), b_returns AS (
  SELECT c.region, AGG(r.total_returns) AS total_returns
  FROM   returns r LEFT JOIN customers c ON r.customer_id = c.customer_id
  GROUP BY c.region
)
SELECT COALESCE(b_orders.region, b_returns.region) AS region,
       b_orders.total_revenue,   -- empty-input value of SUM: NULL
       b_returns.total_returns   -- empty-input value of SUM: NULL
FROM   b_orders FULL JOIN b_returns
       ON b_orders.region IS NOT DISTINCT FROM b_returns.region;
```

Each branch follows a path to `customers`. `LEFT JOIN` preserves orders and returns without a matching customer; `FULL JOIN` preserves regions present in either branch. A region missing from one branch takes that branch's empty-input values (Section 5.5). Both metrics are sums, whose empty-input value is NULL, so the outer query selects the branch values as they are. For a `COUNT(*)` metric it would select `COALESCE(b_returns.total_returns, 0)`. On the data in Appendix B.1:

```text
region | total_revenue | total_returns
EAST   |           350 |            25
WEST   |           300 |          NULL
NORTH  |          NULL |            10
```

Rows have no defined order.

### 1.4 Scope

Measure evaluation belongs to [Layer 2](relational_semantics.md). The model format and expression language belong to Layer 1 ([model specification](spec.md), [expression language](expression_language.md)). This specification defines neither query syntax nor physical execution.

Expressions use `OSSIE_SQL_2026` (Layer 1). Engines MAY accept other dialects, but conformance covers only `OSSIE_SQL_2026`.

Each dimension, filter, and aggregate call reads fields from a single dataset; row-level expressions across datasets are unsupported in this version.

**Sections 3, 4, 5, 8, and 9 are normative.** JSON and SQL examples are illustrative.

### 1.5 Requirements language

MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, MAY, and OPTIONAL have the meanings in BCP 14 (RFC 2119, RFC 8174) when capitalized. **Engine** means a conforming implementation; **consumer** means a client submitting a query. “Layer 2 Section N” refers to [the Relational Query Interface](relational_semantics.md).

---

## 2. Design principles

*Non-normative.*

- **P1. Reuse `AGG()`.** Layer 2 handles fan-out and double counting; Layer 3 adds no separate measure-evaluation rules.
- **P2. Explicit defaults.** Every row that a metric aggregates contributes unless filtered; unmatched rows enter a NULL group; groups from every metric are retained.
- **P3. Reject unsupported cases.** Future versions may define cases that currently fail. Engines must not silently ignore unknown options.
- **P4. Separate semantics from syntax.** The specification defines meaning; bindings define representation.

---

## 3. The query

### 3.1 Wide-table references

The logical **wide table** contains every field and metric in a model. References take these forms:

| Column | Reference | Example |
|---|---|---|
| Field | `dataset.field` | `customers.region` |
| Dataset-scoped metric | `dataset.metric` | `orders.total_revenue` |
| Model-scoped metric | `metric` | `revenue_per_customer` |

Fields and metrics within a dataset share a namespace (core specification, Metric Scoping). References are therefore unambiguous. Identifiers compare under Layer 1 normalization rules. The lowering determines which datasets to join (Section 5).

### 3.2 Query parts

| Part | Meaning |
|---|---|
| `dimensions` | Fields used to group the result |
| `metrics` | Metrics evaluated for each group |
| `filter` | A row predicate applied before aggregation (Section 3.4) |

All parts are optional, but a query MUST include at least one dimension or metric ([`E_EMPTY_AGGREGATION_QUERY`](#8-errors)). It groups by every dimension, as with SQL `GROUP BY ALL`. With metrics and dimensions, it returns one row per resulting dimension tuple; with dimensions alone, distinct tuples; with metrics alone, one row.

Each reference MUST resolve ([`E_NAME_NOT_FOUND`](#8-errors)). Each dimension MUST name a field ([`E_METRIC_IN_DIMENSIONS`](#8-errors)), and each metric MUST name a metric ([`E_FIELD_IN_METRICS`](#8-errors)). The dataset that declares a dimension's field is the **dataset of the dimension**. Expressions over metrics (such as Layer 2's `AGG(revenue) - AGG(cost)`), row listings, sorting, limits, and predicates on metric values are not supported (Appendix A.1).

In a query with one branch (Section 5.4), a dimension that is unique and non-NULL in the home dataset, such as `orders.order_id` with `orders.total_revenue`, gives one group for each home row that the filter keeps. Other branches can add groups. For example, `customers.customer_id` with metrics of `customers` and of `orders` gives an extra NULL group for orders without a matching customer.

### 3.3 Aliases and output names

Every dimension or metric can have an identifier **alias** (Layer 1). Its **output name** is the alias, if given, or the final part of its reference: `customers.region` becomes `region`.

- Output names MUST be unique after identifier normalization ([`E_NAME_COLLISION`](#8-errors)). For example, `orders.total_amount` and `returns.total_amount` require an alias for at least one entry.
- The filter references fields, not output names.
- Bindings SHOULD label result columns with their output names, using the spelling from the query or model.
- The object form (Section 3.7) holds the alias and can accommodate future options such as relationship selection (Appendix A.1).

### 3.4 Filter

`filter` is one Layer 1 boolean expression, evaluated before aggregation.

- All field references (`dataset.field`) MUST belong to one dataset, the **dataset of the filter** ([`E_CROSS_DATASET_FILTER`](#8-errors)).
- A filter with no field references, such as `TRUE`, `FALSE`, or `1 = 0`, has no dataset. It needs no reachability check, and it is evaluated in every branch (Section 5.3).
- The filter MUST NOT contain aggregates or metric references ([`E_AGGREGATE_IN_WHERE`](#8-errors)), or window functions ([`E_WINDOW_IN_WHERE`](#8-errors)).
- The filter applies to every branch (Section 5.4). A home row (Section 5.2) matches at most one row in the dataset of the filter; if none matches, that dataset's fields are NULL. FALSE and NULL predicates remove the row.

For an order with no matching customer, `customers.region <> 'WEST'` removes the order while `customers.region IS DISTINCT FROM 'WEST'` retains it. A predicate reading both `orders.status` and `customers.segment` is rejected. Multiple filters may be added in a future version.

### 3.5 Validation

An engine MUST reject a malformed query, and a query with a part or property that it does not support ([`E_INVALID_QUERY`](#8-errors)); it MUST NOT ignore them. Section 8 lists every error and the order in which they apply.

### 3.6 Result

Result columns appear in query order: dimensions first, then metrics, labeled by their output names. Dimension columns have the types of their fields; metric columns have their metric result types.

The result contains one row for each distinct dimension tuple produced by the lowering, treating NULLs as equal. With no dimensions it contains one row. Row order is undefined.

Metric values cannot generally be re-aggregated to a different grouping. For example, summing regional `COUNT(DISTINCT orders.customer_id)` values does not give the overall customer count. The consumer must issue another query (Layer 2 Section 8.5).

### 3.7 Illustrative JSON binding

The examples use this JSON shape; an interoperable JSON binding is expected to adopt it:

- A query is an object with the parts from Section 3.2.
- A dimension is a reference string or an object with `field` and `alias`; a metric uses `metric` instead of `field`.
- `filter` is an expression string.
- `null` values and unsupported keys or properties are malformed ([`E_INVALID_QUERY`](#8-errors)).

```json
{
  "dimensions": ["customers.region"],
  "metrics": [
    {"metric": "orders.total_amount", "alias": "order_amount"},
    {"metric": "returns.total_amount", "alias": "return_amount"}
  ],
  "filter": "customers.segment = 'SMB'"
}
```

The binding identifies the model and reports the errors of Section 8.

---

## 4. Paths

The **relationship graph** contains one node per dataset and one edge per relationship. Each relationship equates its `from_columns` with its `to_columns`; `from` is the many side and `to` the one side (core specification, Relationships).

- A **step** crosses a relationship from `from` to `to`, matching at most one row.
- A **path** from dataset X to dataset Y is a sequence of steps from X to Y with no repeated dataset. The path from a dataset to itself is empty. Y is **reachable** from X if such a path exists.
- The lowering joins Y to X along the path from X to Y. If two or more paths lead from X to Y, the engine MUST raise [`E_AMBIGUOUS_PATH`](#8-errors), even if one path is shorter. For example, the sale date and the store's closing date give two paths from `store_sales` to `date_dim` (Appendix B.3).

Only many-to-one steps are supported. Layer 2's `AGG()` also handles joins in the reverse direction (Layer 2 Section 8.3), but those paths are outside this version's shared-dimension scope.

The lowering uses `LEFT JOIN` to preserve rows without matches. Engines trust declared cardinality; results are undefined when the data violates it. Self-relationships do not participate in paths.

---

## 5. Lowering

### 5.1 Semantics

The engine MUST return the rows defined by this lowering, in any order, or raise an error from Section 8. It MAY use any execution strategy. The choice of datasets, join paths and types, filters, groupings, and metric evaluation is normative; the SQL syntax shown here is illustrative.

The process is: split metrics into components (Section 5.2), check reachability (Section 5.3), evaluate components in branches by home dataset (Section 5.4), combine branches, and evaluate the metrics from their components (Section 5.5). Section 5.6 handles dimension-only queries.

### 5.2 Metric components

A **component** is an aggregate expression over fields of one **home dataset**. A row of a home dataset is a **home row**. The lowering defines each component as an internally named measure of its home dataset, and evaluates it with one `AGG()` call (Layer 2 Sections 3.4 and 5.1).

- A dataset-scoped metric `D.m` is one component with home dataset `D`, whatever its expression. The expression may combine several aggregate calls, such as `SUM(revenue) / NULLIF(COUNT(*), 0)`, and may use `COUNT(*)` with or without a `FILTER (WHERE ...)` clause. It is still one measure and one `AGG()` call.
- In a model-scoped metric, each aggregate call is a separate component. Its home dataset is the one dataset that its arguments read. Its `FILTER (WHERE ...)` clause MUST read only that dataset (Layer 1, `FILTER (WHERE ...)`), and it does not determine the home dataset. The metric's scalar expression is evaluated from the component values afterward (Section 5.5), as in Layer 1 and Layer 2 (Layer 2 Section 8.1).

The **empty-input value** of a component is its value over no rows. For example, `SUM(x)` gives NULL, `COUNT(*)` gives 0, `COALESCE(SUM(x), 0)` gives 0, and `SUM(revenue) / NULLIF(COUNT(*), 0)` gives NULL. It equals the value of `AGG()` for an empty input set (Layer 2 Section 8.1). Section 5.5 requires it.

An engine MUST reject a model-scoped metric expression that contains:

- an unaggregated field, such as `SUM(orders.amount) * customers.rate` ([`E_UNAGGREGATED_FINER_GRAIN_REFERENCE`](#8-errors));
- a window function, such as `SUM(orders.amount) OVER (ORDER BY orders.order_date)` (deferred; [`E_DEFERRED_WINDOWED_METRIC`](#8-errors));
- aggregate arguments that read several datasets, such as `SUM(order_lines.quantity * products.price)` (deferred; [`E_AMBIGUOUS_MEASURE_GRAIN`](#8-errors));
- an aggregate `FILTER` that reads a dataset other than the one its arguments read, such as `SUM(orders.amount) FILTER (WHERE customers.region = 'WEST')`, which Layer 1 does not allow ([`E_AMBIGUOUS_MEASURE_GRAIN`](#8-errors));
- an aggregate whose arguments read no dataset, such as `COUNT(*)` or `COUNT(*) FILTER (WHERE orders.status = 'completed')` ([`E_AMBIGUOUS_MEASURE_GRAIN`](#8-errors)). A dataset-scoped metric on `orders` with the expression `COUNT(*) FILTER (WHERE status = 'completed')` expresses the second count.

For example, `revenue_per_customer` = `SUM(orders.amount) / COUNT(DISTINCT customers.customer_id)` has two components: `c1` on `orders` and `c2` on `customers`. Without dimensions:

```sql
SELECT b1.c1 / b2.c2 AS revenue_per_customer
FROM       (SELECT AGG(o.c1) AS c1 FROM orders o)    b1
CROSS JOIN (SELECT AGG(c.c2) AS c2 FROM customers c) b2;
```

On the data in Appendix B.1, the value is 650 / 4 = 162.5.

### 5.3 Reachability

The **datasets of a metric** are its components' home datasets. A dataset is **reachable from a metric** only if it is reachable from *every* home dataset of that metric.

Every dimension dataset, and the filter dataset if the filter has one, MUST be reachable from every queried metric. Otherwise the engine MUST raise:

- [`E_NO_PATH`](#8-errors) if no metric reaches a dimension dataset;
- [`E_PARTIAL_DIMENSION_REACHABILITY`](#8-errors) if some, but not all, metrics reach a dimension dataset;
- [`E_UNSUPPORTED_FILTER_PATH`](#8-errors) if any metric cannot reach the filter dataset.

A filter without a dataset requires no reachability check, and it is evaluated in every branch.

Thus, a filter on `orders` cannot apply to a query also containing a metric from `returns` when those datasets meet only through a shared dimension. Unconnected metrics can still appear together without dimensions, or with a filter that has no dataset (Appendix B.4).

### 5.4 Branches

Components with the same home dataset form a **branch**. Its **join tree** is the union of the paths from that home dataset to every dimension dataset and to the filter dataset, if any.

Illustrative lowering:

```sql
SELECT <dimensions>, AGG(h.c1) AS c1, ..., AGG(h.cn) AS cn
FROM   <home dataset> h
LEFT JOIN <dataset 1> ON <relationship predicate>
...
WHERE  <filter>
GROUP BY <dimensions>
```

Each step adds a `LEFT JOIN` using the relationship's equality predicate, after the dataset it starts from. Omit `WHERE` if there is no filter; omit `GROUP BY` if there are no dimensions. A branch without dimensions produces one row.

The left joins preserve all home rows, placing unmatched rows in a NULL group. The filter is applied as a `WHERE` clause in every branch, removing rows for which it is FALSE or NULL. A branch has at most one row for each dimension tuple.

### 5.5 Combining branches and evaluating metrics

A single branch requires no stitching: its rows give the keys and the component values. With two or more branches, the keys and the component values MUST equal the result of these steps:

1. The **keys** are the distinct dimension tuples that occur in at least one branch, comparing NULLs as equal. With no dimensions, the only key is the empty tuple.
2. For each key and each branch: if the branch has a row for the key, each component of the branch takes its value from that row. Otherwise, each component MUST take its empty-input value (Section 5.2).

In all cases, the engine then evaluates each requested metric from its component values (Section 5.2), and returns the output columns of Section 3.6, with exactly one row for each key. For example, the model-scoped metric `SUM(orders.revenue) / COUNT(orders.order_id)` has two components in one branch, and its quotient is evaluated after the branch.

A branch row in a NULL group is different from a missing branch row. The first matches the key whose value is NULL and supplies that group's values. The second supplies empty-input values.

Illustrative plan for three branches, where `b_visits` evaluates `visits.visit_count` (`COUNT(*)`):

```sql
WITH keys AS (
  SELECT region FROM b_orders
  UNION SELECT region FROM b_returns
  UNION SELECT region FROM b_visits
)
SELECT k.region,
       o.total_revenue,                                 -- SUM: NULL
       r.total_returns,                                 -- SUM: NULL
       CASE WHEN v.present IS NULL THEN 0
            ELSE v.visit_count END AS visit_count       -- COUNT(*): 0
FROM   keys k
LEFT JOIN b_orders  o ON o.region IS NOT DISTINCT FROM k.region
LEFT JOIN b_returns r ON r.region IS NOT DISTINCT FROM k.region
LEFT JOIN b_visits  v ON v.region IS NOT DISTINCT FROM k.region;
```

`UNION` treats NULLs as equal. Each branch selects the constant `1 AS present`, so a NULL `present` marks a missing branch row. `COALESCE(v.visit_count, 0)` is equivalent here, because `COUNT(*)` is never NULL over rows that exist. For a component that can be NULL over rows that exist, it is not equivalent. With no dimensions, each branch has one row, and the plan is a `CROSS JOIN`.

A plan that `FULL JOIN`s branches one at a time matches each new branch against the merged key of the branches before it, such as `COALESCE(b_orders.region, b_returns.region) IS NOT DISTINCT FROM b_visits.region`. If it matches against one earlier branch only, it splits a key into two rows (Appendix B.2).

### 5.6 Dimension-only queries

Without metrics, rows come from an **anchor**: a dimension's dataset that can reach every other dimension dataset. The filter does not affect anchor selection.

The engine MUST raise [`E_NO_ANCHOR`](#8-errors) if none qualifies, or [`E_AMBIGUOUS_PATH`](#8-errors) if multiple datasets qualify. The filter dataset, if the filter has one, MUST be reachable from the anchor ([`E_UNSUPPORTED_FILTER_PATH`](#8-errors)).

The query becomes a single branch with that anchor and no metric components:

```sql
SELECT <dimensions>
FROM   <anchor>
LEFT JOIN ...   -- paths from the anchor
WHERE  <filter>
GROUP BY <dimensions>;
```

---

## 6. Guarantees

These properties follow from the lowering; they introduce no additional requirements.

| Property | Basis |
|---|---|
| **S1. Preserve home rows.** Every home row contributes to one group (NULL if unmatched), unless filtered out. | `LEFT JOIN` from the home dataset (Section 5.4) |
| **S2. Avoid double counting.** A home row is counted at most once per group. | `AGG()` (Layer 2 Section 8.3) |
| **S3. Preserve metric groups.** Every group's dimension tuple survives the branch combination, exactly once. | Section 5.5 |
| **S4. Avoid unsafe re-aggregation.** Components are evaluated at the requested grouping. | `AGG()` |
| **S5. No silently wrong answer.** An engine returns the defined result or an error. | Section 5.1 |
| **S6. Filters cannot add result rows.** Filters do not change branch or anchor selection. | `WHERE` in each branch |

S6 depends on the absence of sorting, limits, and metric-value predicates (Appendix A.1).

### 6.1 The wide table is not a SQL relation

In Layer 2, adding or removing measures does not change a query's groups (closure; Layer 2 Section 8.4). In Layer 3, each metric supplies groups from its home dataset. On the data in Appendix B.1, querying `customers.region` with `orders.total_revenue` yields EAST and WEST; with `returns.total_returns`, EAST and NORTH; with no metrics, EAST, WEST, and NORTH.

Consequently, Layer 3 exposes neither measure columns nor `AGG()` in its queries. Tools that depend on relational closure—for example, to fold a Top-N subquery into another query—should use Layer 2. Clients that only want answers can call Layer 3 as it is. To include every dimension value in Layer 3, a consumer can add a metric from the dimension dataset, such as `customers.customer_count`.

---

## 7. Using Layer 2 directly

*Non-normative.* A consumer can write a Layer 2 query for cases outside Layer 3's scope, often starting from a similar Layer 3 lowering. `AGG()` retains Layer 2's measure semantics across joins.

- **One-to-many paths:** `movies.total_gross` by `actors.height`, where `appearances` relates to `movies` and to `actors`, fails with [`E_NO_PATH`](#8-errors) in Layer 3. A Layer 2 query joins through `appearances`, and `AGG()` counts each movie once per height.
- **Metric-specific filters:** adding `WHERE o.status = 'completed'` to only `b_orders` in Section 1.3 restricts revenue but not returns.
- **Cross-dataset filters:** for example, `o.status = 'completed' AND c.segment = 'SMB'`.
- **All dimension values:** starting from `customers` and joining `orders` and `returns` preserves all regions. Plain SQL risks a chasm trap; `AGG()` prevents duplicate counting.
- **Other SQL operations:** `ORDER BY`, `LIMIT`, `HAVING`, `INNER JOIN`, explicit selection among ambiguous paths, and row listings.

---

## 8. Errors

An engine reports each error with its code. If a query meets the conditions of several rows, the engine MUST raise the code of the first row whose condition holds.

| # | Code | Condition | Rule |
|---|---|---|---|
| 1 | `E_INVALID_QUERY` | The query is malformed, or has a part or property that the engine does not support. | Sections 3.5, 3.7 |
| 2 | `E_EMPTY_AGGREGATION_QUERY` | The query has neither dimensions nor metrics. | Section 3.2 |
| 3 | `E_NAME_NOT_FOUND` | A reference does not resolve. | Section 3.2 |
| 4 | `E_METRIC_IN_DIMENSIONS` | A `dimensions` entry names a metric. | Section 3.2 |
| 5 | `E_FIELD_IN_METRICS` | A `metrics` entry names a field. | Section 3.2 |
| 6 | `E_AGGREGATE_IN_WHERE` | The filter contains an aggregate or a metric reference. | Section 3.4 |
| 7 | `E_WINDOW_IN_WHERE` | The filter contains a window function. | Section 3.4 |
| 8 | `E_CROSS_DATASET_FILTER` | The filter reads fields of more than one dataset. | Section 3.4 |
| 9 | `E_NAME_COLLISION` | Two entries have the same output name. | Section 3.3 |
| 10 | `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` | A model-scoped metric has a field reference outside every aggregate. | Section 5.2 |
| 11 | `E_DEFERRED_WINDOWED_METRIC` | A model-scoped metric contains a window function. Deferred. | Section 5.2 |
| 12 | `E_AMBIGUOUS_MEASURE_GRAIN` | An aggregate in a model-scoped metric has arguments that read several datasets (deferred), has a `FILTER` that reads a dataset other than the one its arguments read, or has arguments that read no dataset. | Section 5.2 |
| 13 | `E_NO_PATH` | No metric of the query reaches the dataset of a dimension. | Section 5.3 |
| 14 | `E_PARTIAL_DIMENSION_REACHABILITY` | Some, but not all, metrics of the query reach the dataset of a dimension. | Section 5.3 |
| 15 | `E_NO_ANCHOR` | A query without metrics has no anchor. | Section 5.6 |
| 16 | `E_UNSUPPORTED_FILTER_PATH` | The filter has a dataset, and a metric, or the anchor, does not reach it. | Sections 5.3, 5.6 |
| 17 | `E_AMBIGUOUS_PATH` | Two or more paths lead between datasets that the lowering joins, or two or more datasets qualify as the anchor. | Sections 4, 5.6 |

An error with code 13, 14, or 16 MUST identify the dimension or the filter, and a metric (or the anchor) that does not reach its dataset. Diagnostics SHOULD identify the relevant query parts, metrics, datasets, and relationships, and suggest a remedy when one exists (such as a Layer 2 query).

---

## 9. Conformance

Conformance is semantic: each query must produce the specified rows or error. Bindings define the representation of queries, results, and errors, and interoperability between their implementations.

An engine conforms if it satisfies Sections 3, 4, 5, and 8 through every binding it offers. It need not generate SQL text, but SHOULD expose its lowering, for example through `EXPLAIN`.

---

## Appendix A. Future extensions (non-normative)

Extensions should preserve this version's behavior by default. Controls over paths, joins, and filters belong in Layer 3; controls over measure evaluation belong in Layer 2 so SQL consumers can use them as well.

### A.1 Layer 3 options

- **Per-metric overrides:** metric-specific filters, exemptions from query filters, path selection, and join types. These support BI behavior such as measures that ignore a slicer and cases currently rejected by [`E_UNSUPPORTED_FILTER_PATH`](#8-errors).
- **Explicit relationship selection:** a query-selected path or named relationship role for dimensions and filters, including sale date versus store closing date. Dataset aliases distinguish roles.
- **One-to-many paths:** finer-grained dimensions (`movies.total_gross` by `actors.height`), bridge datasets, and existence filters (such as orders with a matching `line_items.sku`). `AGG()` handles the measure evaluation. Existing path choices remain the default.
- **Multiple filters:** several filters, each on its own dataset. A SQL `WHERE` clause applies its conditions together to every row. Layer 3 could reason about each filter independently, for example by applying a filter only to the metrics that reach its dataset.
- **Cross-dataset expressions:** in dimensions, filters, or aggregate arguments, such as `SUM(order_lines.quantity * products.price)`.
- **Sorting and limits:** sorting alone does not affect lowering, but limits can change which dimension tuples appear. A metric sort with a limit behaves like a predicate on metric values. Any limit also breaks S6: filtering out the highest-revenue region may bring another region into a Top-1 result. NULL order, ties, and string collation also need definition.
- **Metric-value predicates (`HAVING`):** these can also break S6. Filtering may reduce a region's revenue from 300 to 100, causing it to satisfy `revenue < 200`.
- **Grouping sets and row listings:** `ROLLUP`, `CUBE`, and `GROUPING SETS`; branch stitching would include `GROUPING()` in the key to distinguish subtotals from NULL groups.
- **Additional join types:** for example, `INNER JOIN` when referential integrity is declared.
- **Anchored metric queries:** choosing a dataset whose dimension values determine all groups, restoring closure (Section 6.1).
- **More expressions:** scalar dimension expressions, aggregate metric expressions, expressions over metrics, and result-level window functions.

### A.2 SQL table function

A future binding could expose Layer 3 as a SQL table function accepting dimensions, metrics, and a filter. Its result would be an ordinary relation without measure columns, allowing surrounding SQL to filter, sort, or limit those rows.

---

## Appendix B. Worked examples (non-normative)

Relationships point from the dataset containing the reference to the dataset containing the key: `orders_to_customers` has `from: orders`, `to: customers`. All metrics below are dataset-scoped.

### B.1 Shared dimensions

| customers.customer_id | region |
|---|---|
| C1 | EAST |
| C2 | EAST |
| C3 | WEST |
| C4 | NORTH |

| orders.order_id | customer_id | amount |
|---|---|---|
| O1 | C1 | 100 |
| O2 | C1 | 200 |
| O3 | C2 | 50 |
| O4 | C3 | 300 |

| returns.return_id | customer_id | amount |
|---|---|---|
| R1 | C1 | 25 |
| R2 | C4 | 10 |

The query in Section 1.3 yields EAST 350 and WEST 300 from `orders`; EAST 25 and NORTH 10 from `returns`. If `total_returns` were `COUNT(*)`, WEST's value would be its empty-input value 0, and the lowering would select `COALESCE(b_returns.total_returns, 0)`.

Filtering on `customers.region <> 'EAST'` retains WEST and NORTH, both present before filtering (S6).

### B.2 Three branches

`visits` also relates to `customers`. `visits.visit_count` is `COUNT(*)`.

| visits.visit_id | customer_id |
|---|---|
| V1 | C4 |
| V2 | C4 |
| V3 | C3 |

`{"dimensions": ["customers.region"], "metrics": ["orders.total_revenue", "returns.total_returns", "visits.visit_count"]}` has three branches. `orders` gives EAST and WEST, `returns` gives EAST and NORTH, and `visits` gives WEST and NORTH:

```text
region | total_revenue | total_returns | visit_count
EAST   |           350 |            25 |           0
WEST   |           300 |          NULL |           1
NORTH  |          NULL |            10 |           2
```

EAST has no visits, so `visit_count` takes the empty-input value of `COUNT(*)`, 0. NORTH appears once. A plan that joins `b_visits` on `b_orders.region` alone does not match the NORTH row, whose `b_orders.region` is NULL. It returns two NORTH rows: one with `total_returns` 10, and one with `visit_count` 2.

### B.3 Path cases

- **Shared dimensions:** `store_sales` and `store_returns` both relate to `item` and `date_dim`. Querying their respective metrics by `item.brand` and `date_dim.year` succeeds with two branches.
- **Ambiguous paths:** `store_sales` also relates to `store`, which relates to `date_dim` through its closing date. Both `store_sales.total_sales` by `date_dim.year` and a filter on `date_dim.year = 2024` raise [`E_AMBIGUOUS_PATH`](#8-errors).
- **No path:** `customers.customer_count` by `item.brand` raises [`E_NO_PATH`](#8-errors) when `store_sales` relates to `customers`: `customers` cannot reach `store_sales` or `item`.
- **No anchor:** `item.brand` and `store.state` with no metric raise [`E_NO_ANCHOR`](#8-errors); neither dataset reaches the other. Adding `store_sales.total_sales` makes the query valid.

### B.4 Unconnected datasets

`tickets` has no relationship to `orders` or `customers`. Without dimensions, `orders.total_revenue` and `tickets.ticket_count` return one row with both totals. Adding `customers.region` raises [`E_PARTIAL_DIMENSION_REACHABILITY`](#8-errors), naming `tickets.ticket_count`. Adding the filter `FALSE` instead is valid: the filter has no dataset, and the result is one row with each metric's empty-input value.

### B.5 Filter cases

Using the data in Appendix B.1:

- `{"metrics": ["orders.total_revenue"], "filter": "customers.region = 'EAST'"}` returns 350.
- `{"dimensions": ["customers.region"], "metrics": ["orders.total_revenue"], "filter": "orders.amount > 100"}` returns EAST 200 and WEST 300. Adding `returns.total_returns` raises [`E_UNSUPPORTED_FILTER_PATH`](#8-errors), naming that metric.
- `{"dimensions": ["customers.region"], "filter": "orders.amount > 100"}` raises [`E_UNSUPPORTED_FILTER_PATH`](#8-errors): the anchor `customers` cannot reach `orders`.
- `{"dimensions": ["orders.order_id", "customers.region"], "filter": "orders.amount > 100"}` uses anchor `orders` and returns (`O2`, EAST) and (`O4`, WEST).
- `{"dimensions": ["customers.region"], "metrics": ["orders.total_revenue"], "filter": "FALSE"}` returns no rows. Without the dimension, it returns one row whose value is NULL, the empty-input value of `SUM`.
