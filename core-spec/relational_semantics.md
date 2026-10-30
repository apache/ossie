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

# Relational Query Interface for Ossie Models

**Specification (draft)**

Status: Draft for the Ossie Semantics Working Group.
Target: Apache Ossie (Open Semantic Interchange) semantic-model spec version `0.2.0.dev0`.
Scope: how a semantic layer exposes an Ossie model to SQL-native BI and AI tools as a set of
related SQL relations, and the semantics and correctness guarantees of querying the measures on
those relations through SQL.
Layer: this specification defines **Layer 2**, the Relational Interface, of the layered
Ossie query interface (Section 1.1). It is self-contained; Layers 1 and 3 are referenced but
specified separately.

This specification is grounded in the measure model of Hyde and Fremlin, *Measures in SQL*
(arXiv:2406.00251): a measure is a column that attaches a calculation to a relation, is
evaluated in the context of the query that references it, and expands transparently into ordinary
SQL. This document adapts that model to the multi-table, cross-tool setting of Ossie.

---

## 1. Introduction

### 1.1 Purpose

Ossie has focused on interchange: moving a semantic model's definition between tools. But
consumers also need a way to query a model and get back correct, consistent metrics, the same for
every tool that queries it.

Different consumers need different things from a query interface, so Ossie is adopting a layered
approach. This document specifies **Layer 2, the Relational Interface**: a SQL-based query
interface for BI and AI tools that generate their own SQL and want its full power. Its
counterpart, **Layer 3, the Wide Table Interface**, flattens the model into a single object that a
consumer queries without writing joins. That is simpler but more restricted, and it is proposed
separately in [apache/ossie#246](https://github.com/apache/ossie/pull/246). **Layer 1**, the
[Common Expression Language](expression_language.md),
is the portable SQL expression subset the higher layers build on. A higher ontology layer
(Layer 4) sits above these and is out of scope here.

Most BI and AI tools already use SQL as their primary query interface. They rely on its
expressions, filtering, grouping, and joining for slice-and-dice analysis and multi-table
modeling. So the natural interface for these tools is to expose an Ossie model's datasets as
relations and let them query with ordinary SQL.

Plain SQL, though, cannot guarantee correct measures. Aggregating across joined tables is
error-prone: fan-out and chasm traps silently double-count. And different tools each compute the
same metric their own way, so results are often neither correct nor consistent across tools. SQL
alone gives flexibility, but not correctness.

This specification proposes that BI and AI tools query Ossie models through standard SQL extended
with correct measure semantics ([*Measures in SQL*](https://arxiv.org/abs/2406.00251)). The engine
evaluates each measure over its own source rows, and it stays correct however the consumer
filters, groups, and joins. A tool evaluates it with a single `AGG()` aggregate and otherwise
writes the SQL it always would. Consumers get both governed, consistent measures and the
flexibility of a SQL query interface.

### 1.2 At a glance

A consumer connects to a semantic layer, discovers a model's relations, and queries them with
ordinary SQL. The one difference from querying plain tables: a measure is evaluated through the
`AGG()` aggregate:

```sql
-- illustrative
SELECT o.region,
       AGG(o.total_revenue),
       AGG(s.ticket_count),
       AGG(o.total_revenue) / AGG(s.ticket_count) AS revenue_per_ticket
FROM   model.orders o
JOIN   model.support s ON o.customer_id = s.customer_id
WHERE  o.order_date >= DATE '2025-01-01'
GROUP BY o.region
ORDER BY AGG(o.total_revenue) DESC;
```

`total_revenue` (on `orders`) and `ticket_count` (on `support`) are measures over different source
rows, identified as measures from metadata (Section 11). `region`, `customer_id`, and `order_date`
are ordinary fields. The tool joins the two tables as it would any tables and evaluates a measure
from each with `AGG()`. The engine computes each measure over its own source rows, so the
many-to-many join on `customer_id` inflates neither one (no fan-out or chasm trap, G2), while
`WHERE` and `GROUP BY` slice them like any aggregate (G1), on any field, join key or not. The same
query yields the same measure values across tools. A consuming tool writes `AGG(name)` where it
would otherwise write a raw aggregate, and generates SQL as it always has. Its obligations are in
Section 8.5.

For instance, the query might return:

```
region | total_revenue | ticket_count | revenue_per_ticket
West   |     1,200,000 |          400 |              3,000
East   |       800,000 |          250 |              3,200
```

Each order's revenue is counted once and each ticket once, though the join pairs every order
with several tickets and vice versa. A raw `SUM`/`COUNT` over the joined rows would inflate both.

### 1.3 Scope

This specification covers:

- The **data model**: relations carrying fields and measures, and the *row identity* of a
  measure's source rows (Section 3).
- The **type discipline** for measures (Section 4).
- The **semantics** of defining measures and of querying them through SQL operators:
  projection, selection, joins, grouping, subqueries, and set operators (Sections 5-7).
- The two **correctness guarantees** a conforming engine provides, stated normatively
  (Section 8), and a non-normative reference evaluation strategy that satisfies them
  (Section 9).
- The **multi-table** consistency contract and the **discovery** mechanisms by which a tool
  learns a model's tables, measures, and relationships (Sections 10-11).
- **Conformance** criteria (Section 12), and out-of-scope behaviors and open questions
  (Section 13).

It does not cover:

- Automatic join resolution: choosing which tables to join, the join types, and how filters
  propagate. At Layer 2 the tool writes its own joins; resolving them automatically is a Layer 3
  concern (Section 1.1), and the choice of joins and filters is the tool's, not something
  standardized here (Section 10.2).
- The model interchange format, the subject of other Ossie specifications.
- Physical execution, storage, indexing, or performance.

The SQL spelling shown throughout is illustrative and provisional. This specification aims to
standardize the query extension: the measure type, `AGG()`, and the measure-definition
marker. Its syntax is expected to be refined during working-group review rather than left for each
engine to reinvent.

### 1.4 Requirements language

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, MAY, and
OPTIONAL are to be interpreted as described in RFC 2119. "The engine" denotes a conforming
implementation of this interface. "A consumer" or "a tool" denotes a client that issues
queries against it.

---

## 2. Design principles

These principles motivate the normative rules and are themselves non-normative.

- **P1. A transparent relational interface.** An Ossie semantic model is exposed as a set of
  related SQL relations. Its datasets and relationships map to relations the tool sees and queries
  directly, with nothing about the multi-table structure hidden. Consumers query them with
  ordinary SQL, using its standard syntax and semantics. A relation carrying measures behaves like
  any other relation: it composes under joins, filters, projections, and subqueries, so standard
  SQL rewrites and optimizations remain sound over it (Section 8.4).
- **P2. Correct measures.** The engine owns how a measure is evaluated. A consumer references it
  by name and never reconstructs its computation, so a governed metric comes out correct and
  identical across every consumer, however the consumer filters, groups, and joins tables.
- **P3. Minimal consumer change.** Querying a measure differs from an ordinary aggregate by
  exactly one substitution: a raw aggregate expression becomes `AGG(name)`. A tool's query
  generation is otherwise unchanged. The placement of aggregates in SELECT, HAVING, and ORDER BY,
  and the use of subqueries, joins, and grouping, all carry over as written.

---

## 3. Data model

This interface exposes an Ossie semantic model as SQL relations. Each dataset in an Ossie model
becomes a relation, and the relation's columns are the dataset's fields and metrics. This section
defines that relational data model: relations of fields and measures (Section 3.1), the row
identity a measure carries (Section 3.2), and how it maps to the Ossie model (Section 3.4).

### 3.1 Relations, fields, measures

A **relation** is a multiset of rows over a fixed set of named **columns**, following standard
SQL multiset semantics. Each column is exactly one of:

- a **field**: an ordinary SQL column of some scalar type `T`; or
- a **measure**: a column of type `T measure` (Section 4) that carries an unevaluated
  aggregate calculation together with the row identity of the source rows it aggregates.

A **measure relation** is a relation with at least one measure column. A relation with no
measure columns is an ordinary relation and MUST behave exactly like a standard SQL relation
under all operators; this specification imposes no additional requirements on it.

Fields and measures share a single column namespace within a relation. Column names within
a relation MUST be unique.

### 3.2 Row identity

Every measure carries the identity of the source rows it aggregates. At the point where a
measure is defined (Section 5), each row of the measure's source has an identity. This **row
identity** is remembered as the measure propagates through a query, and it governs how the
measure is aggregated (Section 8).

Two properties define row identity:

- **Distinct source rows have distinct identities**, and rows that share an identity are the
  same source row, identical in all content the measure reads, including the inputs its aggregate
  reads. (It follows that a measure's aggregate inputs are functionally determined by its row
  identity: a candidate identity under which two genuinely different rows collide is not a valid
  identity.)
- A row **may lack an identity**. In a consuming query this happens to rows a join fabricates
  that derive from no source row (for example, the null-padded side of an outer join; Section
  8.1).

A measure's row identity is fixed at definition time and is not altered by a consuming query.

An engine may represent a row identity in different ways: an author-declared key (a dataset's
`primary_key`; Section 3.4), a synthesized id, or a physical row identifier. How it does so, and
whether any representation is exposed to consumers, are discussed in Sections 9 and 11. Whether
row identity needs a declaration syntax at all is an open question (Section 13.1). It is a
semantic notion and need not be a stored column; Section 9 gives one concrete realization.

> **Rationale (non-normative).** Row identity is the single piece of state that distinguishes a
> measure from a raw aggregate. A raw aggregate sums whatever rows reach it, so a join that
> duplicates rows silently inflates downstream aggregates. A measure remembers the identity of
> its source rows and so aggregates each of them once, no matter how upstream operations
> multiply rows.

### 3.3 Relations with measures from several sources

Distinct measures in the same relation MAY have distinct source rows. A relation formed by
joining two measure relations (Section 7.6) carries each contributing relation's measures with
their original row identities. Each measure is evaluated over its own source rows independently
(Guarantee G2).

### 3.4 Relationship to the Ossie semantic model

The relations, fields, and measures above are how this interface presents an Ossie
`semantic_model` (its `datasets`, their `metrics`, and its `relationships`) to SQL consumers, in
SQL-native names. The correspondence:

| This spec | Apache Ossie | Notes |
|---|---|---|
| relation | `dataset` | A logical table (fact or dimension) exposed as a SQL relation. |
| field | `field` | A scalar (non-measure) column; we adopt Ossie's term. Ossie's optional `dimension` role (e.g. `is_time`) is metadata this interface does not require. |
| measure | dataset-scoped `metric` (`datasets[].metrics`) | An aggregate expression over the dataset's rows (of any scalar result type), exposed as a measure on the dataset's relation. Model-scoped metrics are not exposed as measure columns (Section 10.3). |
| row identity (Section 3.2) | `primary_key` | A dataset's declared primary key is a natural source of a measure's row identity. |
| join relationship (Section 11.3) | `relationship` (`from`/`to`, `from_columns`/`to_columns`) | `from`/`to` map to source/target and the columns to the join predicate (`from` many, `to` one); Section 11.3 adds cardinality, roles, and arbitrary `ON`. |
| Layer 1 expression | `OSSIE_SQL_2026` expression (`expression.sql`, or an `OSSIE_SQL_2026` entry in `dialects`) | The portable form of an Ossie expression, in the Layer 1 language (Section 1.1). For the resolution rules, see [Expression Object](spec.md#expression-object). |

Ossie model metadata this interface does not natively surface (`ai_context`, `custom_extensions`,
and non-selected expression dialects) is addressed under discovery (Section 11) and out of scope
(Section 13.2). A conforming engine MUST preserve metric semantics: a metric evaluated via
`AGG()` is computed from the model's metric definition (its aggregate and source rows), so two
consumers issuing the same query agree on its value (Section 10.2).

---

## 4. Type discipline

### 4.1 The measure type

For each scalar type `T`, there is a distinct type `T measure`. A value of type `T measure` is
an unevaluated aggregate calculation whose evaluation yields a value of type `T`.

`T measure` is **not** a subtype of `T` and is **not** interchangeable with it. The two
central rules:

- **R1.** `T measure` is a new type, and SQL's operators and functions are not defined on it. A
  value of type `T measure` therefore MUST NOT appear where an operator or clause would have to
  interpret it: as an operand of a scalar operator or function; as a `WHERE`, join, or `HAVING`
  predicate (which must be `boolean`, not `boolean measure`); or as a `GROUP BY` key (grouping is
  not defined over `T measure`). A measure column MAY still pass through a select list, which
  accepts a value of any type, carrying its `T measure` type onward (Section 7.1).
- **R2.** The only operation that converts `T measure` to `T` is the `AGG` aggregate
  function (Section 6), which is valid only in an aggregate context.

A well-typed query MUST reject, at analysis time, any expression that violates R1 or R2. For
example (non-normative), if `total_sales` has type `int measure`:

```sql
-- rejected: '+' is not defined on (int measure, int)
SELECT total_sales + 1 FROM v;

-- rejected: grouping is not defined over int measure
SELECT total_sales FROM v GROUP BY total_sales;

-- rejected: a join condition must be boolean; int measure is not
... JOIN w ON v.total_sales = w.total_sales;
```

### 4.2 Producing a measure

A value of type `T measure` is produced only by a **measure definition** (Section 5), the
construct that marks an aggregate expression as a measure. That is also the one place an
aggregate expression may appear outside an aggregated query; everywhere else an aggregate,
including `AGG()`, is valid only in an aggregated context (R2).

### 4.3 Propagation

Under projection, selection, joins, and subqueries, a measure column retains its type
`T measure` and its row identity (Sections 3.2, 7).

---

## 5. Defining measures (illustrative syntax)

### 5.1 Base measures

A base measure attaches an aggregate expression to a relation. Illustratively, a `TO MEASURE`
marker in a projection promotes an aggregate expression to an unevaluated measure:

```sql
-- illustrative
CREATE VIEW sales_v AS
SELECT
    o_orderdate          AS order_date,   -- field
    c_region             AS region,       -- field
    COUNT(*)             TO MEASURE AS order_count,   -- measure: int measure
    SUM(o_totalprice)    TO MEASURE AS total_revenue  -- measure: decimal measure
FROM orders JOIN customers ON o_customerkey = c_customerkey;
```

Normatively:

- The operand of a measure definition MUST be a valid SQL aggregate expression (in the
  Layer 1 expression language). A non-aggregate operand MUST be rejected.
- The measure's source rows, and their row identity (Section 3.2), are captured from the
  `FROM` clause in effect at the point of definition. The definition's own fields constrain
  nothing about the row identity; it is the identity of the source rows, not of the projected
  fields.
- The result column has type `T measure`, where `T` is the aggregate expressions's output type.

### 5.2 Derived measures

A measure definition's expression is an ordinary SQL aggregate expression, so every value in it
comes from an aggregate function: `AGG()` reads an existing measure, just as `SUM` or `COUNT`
aggregates a raw field. A definition that reads one or more existing measures with `AGG()`
yields a derived measure:

```sql
-- illustrative
avg_order_value := (AGG(total_revenue) / COUNT(DISTINCT order_id)) TO MEASURE
```

`AGG(total_revenue)` reads the existing revenue measure and `COUNT(DISTINCT order_id)`
counts orders from a raw field. A derived measure delegates to the measures it references: each
is evaluated over its own source rows, and the surrounding scalar expression is applied after those
evaluations (see Section 7.2). Its inner `AGG()` calls are captured unevaluated, just as
`TO MEASURE` captures `SUM(x)`: they are evaluated at query time, in the consuming query's
context (not when the derived measure is defined), so no `AGG()` is evaluated outside an
aggregated context (R2). Derived measures MAY reference measures from more than one relation;
such a measure is a **multi-table measure** (Section 10.3).

### 5.3 Evaluation controls

An engine MAY offer controls that change which source rows a measure aggregates in each group:
including or excluding fields from the grouping a measure responds to, semi-additive selection,
and windowing (cumulative, trailing, period-over-period). These controls are extensions layered
on top of the base semantics defined here; their specification is out of scope for this
document. When present, they MUST preserve the no-duplication guarantee of Section 8.

---

## 6. Querying measures: the AGG function

A measure is evaluated through the aggregate function `AGG`:

- **Signature.** `AGG(m)` takes an argument of type `T measure` and returns a value of
  type `T`.
- **Kind.** `AGG` is an aggregate function. It MUST be treated syntactically and
  semantically as an aggregate everywhere: it MAY appear wherever a standard aggregate may
  appear (e.g. SELECT list, HAVING, ORDER BY), and it MUST NOT appear where a standard aggregate
  may not. Placing it in a SELECT list or `HAVING` makes the query aggregated
  (Section 7.2), just as any aggregate does.
- **Argument.** The argument MUST be an expression of type `T measure`. In this version, the
  only such expression is a reference to a measure column, optionally qualified by relation
  alias (`AGG(o.revenue)`). Because no operator or function is defined on `T measure` (R1),
  a scalar expression over measures, such as `AGG(a + b)`, is not well-typed. Write
  `AGG(a) + AGG(b)` instead.
  Later extensions may add other expressions of type `T measure`, such as a measure whose input
  set is modified, or an anonymous inline measure. This rule admits them without change.

`AGG(m)` evaluates `m`'s calculation over its input set: the source rows of `m` that remain
in each group after the enclosing query's joins and filters (Section 8.1).

> An engine MAY expose additional spellings of this function (for example `MEASURE()`) as
> synonyms. Synonyms are non-normative: portable queries and conformance tests use `AGG()`.

---

## 7. Operator semantics

This section specifies how **measure columns** behave under each SQL operator. The semantics
follow from the definitions of a measure column and the `AGG()` function:

- A measure column has the new type `T measure`, which SQL's operators and functions are not
  defined on. By R1 (Section 4.1), no expression of that type can be an operand of a scalar
  operator or function, nor be used where a specific type is required, e.g. a `WHERE` or join
  predicate must be `boolean` (not `boolean measure`). Grouping is not defined over
  `T measure`. A measure column passes through a select list unchanged, keeping its
  `T measure` type.
- `AGG()` is an aggregate function (Section 6). Ordinary SQL then fixes where it may appear:
  in a `SELECT` list, `HAVING`, or `ORDER BY` (and inside scalar expressions there), and not in
  `WHERE` or `GROUP BY`, just as `SUM(...)` may not.
- Once `AGG()` has evaluated a measure to a field of type `T`, the result is an ordinary
  column, with no special rules thereafter.

Every operator then behaves as in standard SQL. The only measure-specific behavior is that each
`AGG()` is evaluated over its measure's own input set (Section 8.1). That is what makes
filtering, grouping, and joining compose correctly (G1, G2). The subsections state only that
behavior and the few outright exceptions; the rest is standard SQL and is not restated.

The following table summarizes; the subsections are normative.

| Operator | Measure columns |
| :-- | :-- |
| SELECT, unaggregated (7.1) | Pass through as `T measure`; may be renamed; `*` includes them; may be (re)defined. Evaluating one via `AGG()` makes the query aggregated (7.2). |
| SELECT, aggregated (7.2) | Measures allowed only inside `AGG()`. A bare (unevaluated) measure in an aggregated select is an error. |
| WHERE (7.3) | Ordinary predicate; filters the measure's input rows before evaluation. `AGG()` only via a scalar subquery. |
| GROUP BY / GROUPING SETS / CUBE / ROLLUP (7.4) | Grouping ranges over fields; grouping is not defined over `T measure`. `GROUP BY ALL` groups by the non-measure select expressions. |
| HAVING / ORDER BY (7.5) | `AGG()` allowed, as for any aggregate. |
| JOIN (7.6) | Any join type/condition over fields. Each measure retains its own row identity and is de-duplicated before evaluation (G2). Joining on a measure is an error. |
| Subquery / CTE (7.7) | Measures propagate as `T measure` with row identity intact. |
| Set operators, DISTINCT (7.8) | Error when an input carries an unevaluated measure. |

### 7.1 SELECT (unaggregated)

In a SELECT without aggregation, a measure column is treated like any other column:

- if projected (by name, renamed, or via `*`), it passes through, keeping its `T measure`
  type and its row identity; if not projected, it is dropped;
- it MAY be defined or derived by a measure definition in the projection (Section 5);
- it MUST NOT be the operand of a scalar operator or function (R1);
- it is not evaluated to a value here: `AGG()` is the only way to evaluate a measure, and a
  `AGG()` in the select list makes the query aggregated (Section 7.2), so a genuinely
  unaggregated projection contains no `AGG()` (R2).

Non-normative examples:

```sql
SELECT date_dim AS d, total_sales AS ts FROM v;   -- ok: passes a measure through, renamed
SELECT * FROM v;                                  -- ok: fields and measures pass through
SELECT total_sales + 1 FROM v;                    -- rejected: scalar op on a measure (R1)
```

### 7.2 SELECT (aggregated)

A SELECT is aggregated (as in standard SQL) if it has a `GROUP BY` clause, `HAVING` clause, or an
aggregate expression in its select list. In an aggregated SELECT:

- An expression of type `T measure` MUST appear only as the argument of `AGG()`. A bare,
  unevaluated measure in the select list of an aggregated query MUST be rejected.
- `AGG(m)` returns a scalar of type `T`; it MAY be combined with other scalars and
  aggregates in the select list, HAVING, and ORDER BY.
- For a derived measure or an expression combining several `AGG()` calls, each `AGG()` is
  evaluated independently over its own input set and the surrounding scalar expression applied per
  output group (Section 8.1). For example, `AGG(revenue) / AGG(ticket_count)` divides two
  independently duplication-free aggregates within each group.

Non-normative examples:

```sql
SELECT region, AGG(total_revenue) FROM v GROUP BY region;   -- ok
SELECT AGG(total_sales) FROM v;               -- ok: aggregated grand total
SELECT region, MAX(order_date), AGG(total_revenue) FROM v
GROUP BY region;   -- ok: an ordinary aggregate over a field, beside a measure
SELECT region, total_revenue FROM v GROUP BY region;   -- rejected: bare measure (R1)
```

### 7.3 WHERE

`WHERE` filters the input rows before aggregation, so a measure sees only the surviving rows:
filtering on a field changes a measure just as it would a raw aggregate over the same rows
(half of Guarantee G1, Section 8.2). A scalar subquery in the predicate MAY use `AGG()`; a
predicate on a measure's value goes in `HAVING`.

Non-normative examples:

```sql
SELECT region, AGG(total_revenue) FROM v
WHERE order_date >= DATE '2025-01-01' GROUP BY region;  -- ok: WHERE filters the measure's input
SELECT region, AGG(total_revenue) FROM v
WHERE AGG(total_revenue) > 100 GROUP BY region;         -- rejected: aggregate in WHERE; use HAVING
SELECT region, AGG(total_revenue) FROM v
WHERE (SELECT AGG(gross) FROM budget) > 100 GROUP BY region;  -- ok: AGG() in a subquery
```

### 7.4 GROUP BY

- Grouping (`GROUP BY`, `GROUPING SETS`, `CUBE`, `ROLLUP`) ranges over fields; grouping is not
  defined over `T measure` (R1). The grouping sets the level at which each measure is reported,
  the other half of Guarantee G1 (Section 8.2).
- Under `GROUP BY ALL`, measures are not grouping columns: a bare (unevaluated) measure in the
  select list has nothing to group by and MUST be rejected; otherwise `ALL` groups by the
  remaining fields, as usual.

Non-normative examples:

```sql
SELECT region, AGG(total_revenue) FROM v GROUP BY region;   -- ok
SELECT region, AGG(total_revenue) FROM v GROUP BY ALL;   -- ok, groups by region
SELECT region FROM v GROUP BY total_revenue;   -- rejected: grouping not defined over a measure (R1)
```

### 7.5 HAVING and ORDER BY

Since `AGG()` is an aggregate (Section 6), `HAVING` and `ORDER BY` MAY contain `AGG()`,
and scalar expressions over it, just as they may `SUM(...)`.

Non-normative example:

```sql
SELECT region, AGG(total_revenue) FROM v GROUP BY region
HAVING AGG(total_revenue) > 100 ORDER BY AGG(total_revenue) DESC;   -- ok
```

### 7.6 JOIN

A measure relation MAY be joined to any relation using any join type (INNER, LEFT, RIGHT, FULL
OUTER, CROSS) and any join condition over fields (R1).

A join is logically applied before measures are evaluated. It affects a measure's input rows
in three ways, each handled by the evaluation semantics (Section 8.1) so the measure is still
evaluated over its own source rows:

- **Filtering.** Rows the join drops (an inner join's non-matches) are removed from the
  measure's input set, just as a `WHERE` filter would remove them.
- **Duplication.** A source row that matches several rows on the other side appears in several
  result rows, all deriving from the same source row; it enters the input set once, so the
  measure is not inflated.
- **Null-extension.** An outer join's null-padded rows derive from no source row of the
  measure, so they add nothing to its input set (they remain in the query result; Section 8.1).

The result of a join of measure relations is itself a measure relation whose measures retain
their original row identities (Section 3.3), so joins compose.

Non-normative example (many-to-many join on `region`, each measure still duplication-free):

```sql
SELECT s.region,
       t.ticket_count,
       s.total_sales,
       AGG(s.total_sales) / AGG(t.ticket_count) TO MEASURE AS sales_per_ticket
FROM tickets t JOIN sales s ON t.region = s.region;
```

### 7.7 Subqueries and CTEs

Measures MUST propagate unchanged (type and row identity preserved) through nested query structure:
subqueries (including a derived table in a `FROM` clause), scalar subqueries, CTEs, and views. A
measure defined in one relation can therefore be carried through and evaluated at the top, even
where it appears unevaluated in an intermediate select list.

Whether a top-level query may return an unevaluated measure is deliberately left undefined
by this specification (Section 13.1); intermediate propagation is fully specified here.

Non-normative example:

```sql
SELECT region, AGG(total_revenue)
FROM (SELECT region, total_revenue FROM v WHERE order_date >= DATE '2025-01-01') t
GROUP BY region;   -- total_revenue stays a measure in the subquery, evaluated at the top
```

### 7.8 Set operators and DISTINCT

`UNION` / `UNION ALL` / `INTERSECT` / `EXCEPT` / `DISTINCT` compare or append whole rows, but an
unevaluated measure has no value to compare or append. Therefore:

- A set operator or `DISTINCT` applied to an input relation that carries one or more
  unevaluated measure columns MUST be rejected.
- The same operators are valid once measures have been evaluated to scalars (via
  `AGG()` in an aggregated subquery) or projected away. For example, a `UNION ALL` of
  two subqueries that each select only fields is valid.

Non-normative examples:

```sql
(SELECT region FROM v) UNION (SELECT region FROM w); -- ok: only fields
(SELECT total_revenue FROM v) UNION (SELECT total_revenue FROM w); -- rejected: unevaluated measures
```

---

## 8. Measure evaluation and guarantees

This section gives the single normative definition of how a measure is evaluated (Section 8.1),
then states, as consequences of it, the guarantees a conforming engine provides (Sections
8.2-8.4). A conforming engine MUST provide these guarantees for every measure column in every
query. The consumer-side obligations these guarantees assume are collected in Section 8.5.

### 8.1 Evaluation semantics

A measure is evaluated only through `AGG()` in an aggregated query (Sections 6, 7.2). Its
value in each output group is defined by the set of source rows it aggregates in that group.

**Source rows.** A measure `m`'s **source rows** are the rows produced by the `FROM` clause in
effect where `m` is defined (Section 5.1). Each source row has a distinct row identity
(Section 3.2).

**Derivation.** Every row of every relation that carries `m` *derives from* at most one of `m`'s
source rows:

- Each row produced by `m`'s defining `FROM` clause derives from itself.
- Projection preserves derivation: an output row derives from the source row its input row
  derives from.
- `WHERE` preserves derivation for the rows it keeps. A row it removes contributes nothing
  downstream.
- A row produced by a join derives from the source row that the joined input row carrying `m`
  derives from. When a join matches that input row several times, every resulting row derives
  from the same source row. An outer join's null-padded rows on `m`'s side derive from no source
  row.
- Subqueries, CTEs, and views preserve derivation (Section 7.7).

These rules apply at every query level between `m`'s definition and its evaluation, including
the defining query and the evaluating query. Derivation is tracked separately for each measure
column, so in a self-join, each side's measure follows its own side's rows.

**Input set.** In an aggregated query, the **input set** of `AGG(m)` for an output group `g`
is the set of `m`'s source rows from which at least one row of `g` derives. `m`'s aggregate is
evaluated over the input set, each source row contributing once, with its own argument values.
If the input set is empty (filters removed every source row, or no row of `g` derives from a
source row, as on the null-padded side of an outer join), `m` takes the aggregate's standard
**empty-input value** in the host SQL dialect: `SUM` and `AVG` yield NULL, `COUNT` yields 0, and
so on, just as the aggregate would over an empty input in an ordinary query.

It follows that:

- Filters and joins anywhere between `m`'s definition and its evaluation restrict `m`'s input
  set (G1).
- A source row contributes to `m` at most once per group, however many rows derive from it, so
  joins never inflate `m` (G2).
- Null-padded rows never contribute to `m`.

The input set affects only the rows `m`'s aggregate sees, never the rows or groups the query
returns. Which groups appear is fixed by the query's joins, filters, and grouping, as in standard
SQL; a group whose input set is empty is kept and takes `m`'s empty-input value, rather than
vanishing from the result.

Each measure in a query is evaluated this way independently, over its own input set. A derived
measure (Section 5.2) evaluates each measure it references over that measure's own input set
for the same group. An expression combining several `AGG()` calls applies its scalar
operators to their per-group results, following the host dialect's standard scalar and NULL
semantics, including its own handling of cases such as division by zero (Section 7.2).

Section 9 gives one concrete realization of this definition; any implementation MUST produce the
same results.

### 8.2 G1: Slice-and-dice responsiveness

`AGG(m)` responds to `WHERE` and `GROUP BY` just as a standard SQL aggregate over `m`'s
definition would: a `WHERE` predicate restricts the input rows `m` sees (Section 7.3), and the
query's grouping sets the level at which `m` is reported (Section 7.4). This follows
directly from Section 8.1: filters remove source rows from `m`'s input set, and each group's input
set contains only the source rows that the group's rows derive from. It
is what makes filtering and drill-down behave predictably and identically to non-measure
aggregates.

### 8.3 G2: No duplication

`AGG(m)` aggregates `m` over its own source rows without duplication, regardless of the shape of
the enclosing query: any join type and cardinality, and any other measures or relations present.
This follows from Section 8.1: `m`'s input set is a set of source rows, so each source row
contributes to `m`'s aggregate at most once per output group, however many times a join duplicated
it, and null-padded rows from an outer join do not contribute at all. `AGG(m)` therefore does
not depend on the cardinality of any other relation or measure in the query.

G2 structurally eliminates the two classic multi-table aggregation errors:

- **Fan-out (double counting).** Joining a measure relation to a finer-grained table (one
  source row -> many joined rows) does not inflate the measure; each source row is counted
  once.
- **Chasm trap.** Joining two independent fact relations through a shared field does not produce
  cross-product inflation; each measure aggregates over its own source rows independently and the
  results are combined afterward.

### 8.4 Corollary: Closure and stable field domains

Because `AGG()` behaves as an ordinary SQL aggregate (G1), a measure relation behaves as an
ordinary relation: the set of groups a query returns (its field domain) is fixed by its
grouping fields and filters, just as with standard `GROUP BY`, and does not depend on which
measures it selects. In particular, for a measure relation `R`, the set of field values returned
by

```sql
SELECT d, AGG(m1) FROM R GROUP BY d
```

and by

```sql
SELECT d, AGG(m2) FROM R GROUP BY d
```

MUST be the same (the domain of `d`), differing only in the measure column. A conforming
engine MUST preserve this closure property.

> **Why this matters (non-normative).** SQL-native tools apply rewrites and optimizations that
> assume a relation's field domain does not shift when measures are added or removed, for
> example folding a Top-N filter subquery into the main query when both use the same grouping
> field. Those rewrites are sound against this interface because of the closure corollary, and
> are unsound against opaque multi-table interfaces whose field domains can vary with the
> measures in play. Preserving closure is therefore essential to P1 (a transparent relational
> interface).

### 8.5 Consumer obligations

The guarantees above hold when a consuming tool observes the following. Unlike G1-G2, these are
requirements on the *consumer*, not the engine.

- **Evaluate measures only through `AGG()`.** A consumer references a measure by name and lets
  the engine evaluate it (P2); it MUST NOT reconstruct a measure from the underlying columns,
  which would forgo the no-duplication guarantee.
- **Do not reaggregate an `AGG()` result on the client.** An `AGG()` value is already
  aggregated at the query's grouping; combining such values across groups on the client (for a
  coarser grouping, a drill-up, or a cached-then-refiltered view) is not valid, the same rule
  that applies to non-decomposable aggregates such as `COUNT(DISTINCT ...)` or `MEDIAN`. To
  change the grouping, re-query the engine. For example, given per-state `AGG(revenue)`, a
  tool MUST NOT sum those client-side for a national total; it re-queries grouped at the national
  level.

Discovery of measures, model boundaries, and relationships (Section 11) is the remaining
consumer-side step; a tool that ignores the measure marker still reads its fields as ordinary
columns but will not know which columns are measures (Section 11.1).

---

## 9. Reference evaluation strategy (non-normative)

This section describes one strategy that realizes the evaluation semantics of Section 8.1 (and
thereby guarantees G1 and G2). It is non-normative: an engine MAY use any strategy (cardinality
analysis, aggregate-specific algebraic identities, preserving row identity implicitly in the
query structure, etc.), provided the results equal those of this reference
strategy.

This realization makes row identity concrete as a **row-identity key**: a single key column `k`
synthesized when the measure is defined (for example from a declared key, or by row numbering).
`k` is non-null for every source row, unique per source row, and stable within a single query
execution. It is implicitly propagated through relational operators between the measure definition
and evaluation sites.

Given a query grouping by fields `D` (possibly empty) with the joins and filters applied, a
measure whose aggregate is `agg(e1, ..., en)` over argument expressions `e1..en` is evaluated,
for each grouping row, by a correlated scalar subquery that de-duplicates to identity and then
aggregates:

1. **De-duplicate to identity.** Take the joined, filtered rows of this group, discard those
   where `k` is null (rows that derive from no source row, Section 8.1), and reduce to one row per
   `k`, carrying each argument expression through with `ANY_VALUE` (well-defined because every
   `ei` is functionally determined by `k`, Section 3.2). This leaves one row per distinct source
   row of the measure in the group, removing join-introduced duplication.
2. **Aggregate.** Apply the aggregate over those de-duplicated rows.

```sql
-- evaluated per grouping row `spine` of (SELECT DISTINCT D FROM joined):
SELECT agg(e1, ..., en)
FROM ( SELECT ANY_VALUE(e1) AS e1, ..., ANY_VALUE(en) AS en
       FROM joined
       WHERE k IS NOT NULL AND joined.D IS NOT DISTINCT FROM spine.D
       GROUP BY k )
```

Correlating each group to the joined rows on `D` (matched with `IS NOT DISTINCT FROM`, so a
NULL grouping key matches its own group) fixes the subquery to one value of `D`, so the aggregate
runs directly over that group's de-duplicated source rows. A group with no surviving source row
aggregates over the empty set, and so takes the aggregate's empty-input value (Section 8.1):
`NULL` for `SUM`, `0` for `COUNT`, rather than being dropped. `COUNT(*)` carries
nothing through step 1 (rows are still reduced to one per `k`) and counts the distinct source
rows; set-based aggregates behave as expected over them, so `COUNT(DISTINCT ei)`, `AVG(ei)`, or a
multi-argument `CORR(a, b)` each consume every distinct source row once.

Each measure in the query is evaluated by its own such subquery over its own source rows, and the
query's scalar expressions over the resulting per-group scalars are applied per group (Section 7.2).

---

## 10. Multi-table models

### 10.1 Shape

A multi-table Ossie model is mapped to a set of relations (database tables, views, and measure
relations), each with its own fields and measures, together with a set of relationships
among them.
A tool discovers the relations and relationships (Section 11), then
generates queries that join them using its own SQL, reading measures via `AGG()`.

Nothing in a tool's multi-table workflow needs to change: the tool joins measure relations
just as it joins any table, and the only substitution is `AGG(name)` in place of a raw
aggregate. The engine supplies G1 and G2 across the joins.

Independent measure relations (multi-fact models) are supported directly: a query MAY join
two or more measure relations and evaluate a measure from each; by G2 each is computed over its
own source rows independently (Section 7.6).

Because the engine guarantees that each measure is evaluated without duplication, a consumer can
enrich a model freely: join a measure relation to any other table with ordinary SQL, or take a
different join path, without re-declaring the model. Every measure still evaluates over its own
source rows (G2), so a tool, or an AI agent choosing its own path, never reaches a cliff where
it must fall back to hand-written, duplication-free aggregation.

### 10.2 Consistency contract

This is the cross-tool contract, and the boundary of what the standard fixes.

- **Standardized: which rows each measure aggregates, given the same query.** Two conforming engines
  evaluating the same query (same relations, joins, join types, filters, and grouping) aggregate
  each measure over the same input set of source rows (Section 8.1; G1, G2), and return exactly the
  rows standard SQL returns for that query; measures never change which rows appear. What the
  interface does not standardize is the host dialect's own evaluation of the underlying aggregate
  and scalar expressions over those rows. Like any SQL extension, it inherits dialect differences in
  approximate aggregates, floating-point accumulation, locale- or timezone-sensitive functions, and
  the like. Two engines agree on a measure's value to the extent they agree on that underlying SQL;
  the interface adds no divergence of its own.
- **Not standardized: which query a tool generates.** Which relations to include, which join
  types to use (INNER vs. LEFT vs. FULL OUTER), and how to propagate filters are the tool's own
  analytic choices. Different analyses genuinely call for different join and filter behavior, so
  this is essential flexibility, not ambiguity, and this specification does not prescribe it.

Consequently, two tools that render the "same" user intent as different queries MAY return
different rows, each correct for the question its query actually asks, and any row present in
both carries the same measure values (per the contract above). The standard fixes the result of a
query and leaves the choice of query to the consumer.

Coming from a BI semantic layer, note the difference: here the query's `FROM` and joins are
authoritative. The engine evaluates each measure over the rows that query produces and never
adds, removes, or rewrites a join in response to which measures or fields are selected. Adding
or dropping a measure, or changing the grouping, changes only the projection or grouping, not the
joins. Regenerating the query as the selection changes, if wanted, is the consuming tool's job
(a Layer 3 concern, Section 1.1).

> An engine or model author MAY publish advisory hints (a preferred join type, a filter
> propagation direction, a declared cardinality). Hints express intent and MAY inform a tool's
> choices, but they do not change the guarantees above and are specified outside this document.

### 10.3 Home relations and Ossie metrics

Every measure in this interface is a column of a relation (Section 3.1), evaluated over source
rows with a well-defined row identity (Section 3.2). A measure whose correct evaluation depends on a
join is defined on a relation whose own definition includes that join, so its row identity
is well-defined at the point of definition. For example, a measure `SUM(qty * price)` reading
columns from both `orders` and `products` is a measure on a relation defined
`FROM orders JOIN products`, evaluated at that joined relation's row identity; the consumer
evaluates it with `AGG()` like any other. This is the general pattern: fold a measure's
required joins into its home relation's definition, rather than leaving them to the consuming
query.

Multi-relation measures fit the same pattern. A measure derived from measures on more than one
relation (say a ratio of a measure from each of two facts, Section 5.2) lives on a home relation
whose field domain is fixed, such as a conformed dimension shared by the two facts.
Defined there as `(AGG(a) / AGG(b)) TO MEASURE`, it is an ordinary measure relation that
obeys closure (Section 8.4); Appendix A.7 works this through with a conformed-dimension bridge.

A free-floating measure (attached to no relation, evaluated over whatever join path each query
happens to take) does not belong in Layer 2. Its domain and result shift from query to query. It
is a family of results rather than a single metric, and cannot carry the closure or cross-tool
consistency Layer 2 guarantees. (The multi-relation measures one might otherwise express with a
floating measure are the derived measures above, each defined on a home relation.) Such measures
could instead fit a Layer 3 wide-table interface, whose job is precisely to resolve a join path
per query (Sections 1.1, 10.2); that is outside the scope of this specification.

**Ossie metrics.** This layer exposes Ossie's dataset-scoped metrics (`datasets[].metrics`): each
is a measure on its dataset's relation (Section 3.4). Ossie's model-scoped metrics (the
model-root `metrics`) have no declared home relation and are not exposed as measure columns by
this interface.

---

## 11. Model discovery

A tool needs to discover, through standard SQL metadata channels, (a) which columns are
measures, (b) which relations belong to a model, and (c) how the relations are related. This
interface reuses existing mechanisms wherever possible so that a tool needs minimal new
capabilities.

### 11.1 Measure discovery

Measure relations MUST be discoverable as ordinary relations through standard metadata
operations (`DESCRIBE`, `information_schema`, a zero-row projection). A conforming engine MUST
expose, through these channels, which columns are measures, for example by qualifying a
measure column's reported type with a `MEASURE` marker:

```
-- illustrative: DESCRIBE model.sales
column_name       data_type
order_date        DATE
region            VARCHAR
order_count       BIGINT MEASURE
total_revenue     DECIMAL MEASURE
```

A tool that does not recognize the marker MUST still be able to read the relation's fields
as ordinary columns; it simply will not know which columns are measures.

The type-name marker keeps measure status loud: a measure announces itself in the column type
every consumer already inspects, so it is hard to mistake a measure for a plain column, unlike a
separate metadata table, which a naive consumer can silently fail to consult. Its cross-driver
robustness is the open concern (JDBC/ODBC drivers normalize, alias, or truncate type-name
strings), and whether a metadata-relation fallback (for example
`information_schema.semantic_columns`) should be required alongside it is left open
(Section 13.1).

### 11.2 Model boundary

An engine SHOULD make it easy for a tool to find all relations of one model. In increasing
order of explicitness:

- **Baseline (do nothing).** Users connect relations themselves. This MUST remain possible;
  the interface MUST NOT break tools that build models by hand.
- **Schema-native grouping (RECOMMENDED).** A tool importing a model SHOULD place all of a
  model's relations in a single schema, and consuming tools SHOULD give a schema preferential
  treatment (for example a one-click "import all tables in this schema"). This needs no new
  protocol. Its limitation is cross-schema membership.
- **Explicit model tagging (OPTIONAL).** A relation MAY declare model membership as a property
  (illustratively `MODEL = 'sales'`), and the engine MAY expose a virtual schema per model.
  This gives an enforceable boundary and handles cross-schema membership, at the cost of new
  engine and tool capability.

### 11.3 Relationship discovery

Users can always define joins in their tool (the universal baseline), but a relation MAY also
carry **relationships** that make its intended, valid join paths discoverable and reusable
across tools. A relationship is a **named, directional, informational** declaration from a
**source** relation to a **target** relation, recording:

- a **name**, unique among the source relation's relationships;
- the **target** relation;
- a **join predicate**: an equi-join on declared column pairs (`USING` columns, or `ON`
  equalities between them), which is what current Ossie metadata represents; a relationship MAY
  additionally use an arbitrary boolean `ON` expression, an extension beyond Ossie (see below);
- a **cardinality** (`ONE_TO_ONE`, `ONE_TO_MANY`, `MANY_TO_ONE`, `MANY_TO_MANY`): the expected
  business cardinality induced by the join predicate, informational only and not enforced; and
- an optional **target alias**, required only to disambiguate a self-relationship.

Relationships are directional and carry meaning: a `MANY_TO_ONE` relationship declares that the
target is, in effect, an attribute of the source. Two relations MAY be connected by more than
one relationship, each with a distinct name (for example a `flights` relation relating to
`cities` once as the `departure city` and once as the `arrival city`).

A relationship is **not a foreign key.** It requires no key or uniqueness on the target, allows
several relationships over the same fields, and is purely informational, so it expresses
analytic relationships foreign keys cannot.

**Discovery.** A relationship is surfaced through standard metadata as a table constraint of
type `RELATIONSHIP` on its **source** relation: it appears in `DESCRIBE` output and in
`information_schema.table_constraints` (`CONSTRAINT_TYPE = 'RELATIONSHIP'`), and is declared or
removed with ordinary `ADD` / `DROP CONSTRAINT` DDL. Returning relationships on the source, not
the target, keeps discovery from fanning in every fact that points at a shared dimension.

```sql
-- illustrative
ALTER VIEW model.flights
  ADD CONSTRAINT departure_city MANY_TO_ONE RELATIONSHIP TO model.cities
  ON flights.departure_city_id = cities.id;
```

An Ossie `relationship` maps onto the equi-join baseline: its `from`/`to` datasets are the
source and target and its `from_columns`/`to_columns` the joined column pairs (`from` the many
side, `to` the one), which round-trips between this interface and the Ossie model. The richer
elements above (explicit cardinality, semantic roles, and arbitrary boolean `ON` predicates)
have no home in current Ossie relationship metadata and do not round-trip without a future Ossie
schema extension (Section 13.2).

**Using a relationship.** A tool reads the relationships on a relation, selects the one that
fits the analysis, and generates the join from its predicate. The relationship fixes the
predicate, cardinality, and role; the tool still chooses the join type (INNER, LEFT, ...) and
how filters propagate (Section 10.2), and the join then behaves under Section 7.6 like any
other. ASOF and range relationships, and `RELY` optimization hints, are future extensions
(Section 13.2).

### 11.4 Descriptive metadata (non-normative)

Human- and AI-facing descriptors that have standard SQL-metadata homes (a field's or measure's
label / display name, its description / comment, and its synonyms) SHOULD be surfaced through the
engine's normal column and table metadata so tools can present them. Ossie annotations without
such a home (`ai_context` instructions and examples, and `custom_extensions`) are out of scope
for this interface (Section 13.2); an engine MAY surface them out-of-band.

---

## 12. Conformance

An implementation conforms to this specification if it satisfies all of the following:

- **C1 (Type discipline).** It enforces the measure type rules R1 and R2 (Section 4) at
  analysis time.
- **C2 (Operator semantics).** It implements the operator behaviors of Section 7 for measure
  columns, including rejecting the constructs Section 7 requires be rejected.
- **C3 (G1).** It provides slice-and-dice responsiveness (Section 8.2).
- **C4 (G2).** It provides duplication-free aggregation for every measure in every
  query (Section 8.3), realizing the evaluation semantics of Section 8.1, with results equal to
  the reference strategy of Section 9.
- **C5 (Closure).** It preserves the closure / stable-field-domain corollary (Section 8.4).
- **C6 (Measure discovery).** Measure relations are discoverable through standard metadata
  channels and measures are identifiable among their columns (Section 11.1).

Discovery mechanisms beyond C6 (model boundary and relationship discovery, Sections 11.2-11.3)
are RECOMMENDED but not required for conformance. The SQL spelling used in examples (Sections
5, 6, 11.3) is illustrative and provisional; standardizing it is a goal (Section 1.3), so
conformance is defined on the semantics an implementation realizes, not yet on a fixed syntax.
C1-C6 are engine-side requirements; a consuming tool additionally observes the obligations of
Section 8.5.

An implementation MAY additionally support Layer 1 as a portable expression language and Layer
3 as a wide-table interface; those are specified separately.

---

## 13. Open questions and out of scope

### 13.1 Open questions

These are blocking: each must be resolved before a conforming implementation can deliver the
scope of Section 1.3. They are collected here rather than restated throughout; each section that
raises one points to it.

- **Row identity declaration.** A row identity can be derived automatically (for example by
  numbering each source row), so the standard may not need a declaration syntax at all. Whether
  auto-derivation suffices, or an explicit declaration (e.g. a `PRIMARY KEY`) is needed when the
  intended identity is coarser than one source row, is open (Section 3.2).
- **Top-level unevaluated measures.** Whether a top-level query may return a column of type
  `T measure`. This is currently left to the engine, which MAY reject it or MAY offer a
  compatibility mode (for example returning nulls of the base type); intermediate propagation
  through subqueries is defined (Section 7.7). The working group should standardize one
  behavior, since it affects cross-engine interop.
- **Exposure through standard database interfaces.** How the parts of an Ossie model with no
  counterpart in a plain table (measures, relationships, and AI metadata) reach a tool
  through ordinary JDBC/ODBC connectivity. The type-name `MEASURE` marker is preferred because it
  is loud (Section 11.1), but its robustness across drivers, whether a metadata-relation fallback
  (for example `information_schema.semantic_columns`) is required alongside it, and how
  relationships (Section 11.3) and descriptive/AI metadata (Section 11.4) are surfaced the same
  way, are open.

### 13.2 Out of scope for this version

These are not required by this version and do not block the scope above; each is a candidate for
a later extension. Where an item is a runtime behavior (for example evaluation controls), a
conforming engine MAY reject it or define its own behavior but MUST NOT rely on cross-engine
agreement; whether it is a feature or syntax, it is simply out of scope here.

Deferred query and modeling features:

- **Evaluation control semantics.** The detailed semantics of evaluation controls, such as
  totals and windowing (Section 5.3), are out of scope, but may be defined by a later extension.
- **Parameterization.** Whether a model, a dataset, or a measure can take parameters (values
  supplied at query time that vary within a single query) is out of scope.
- **Relationships beyond equi- and boolean joins.** The relationship model (Section 11.3)
  covers `ON` and `USING` predicates. ASOF and range relationships, and `RELY` optimization
  hints, are future extensions; whether Ossie standardizes the relationship declaration (name,
  direction, cardinality, predicate, role) and at what conformance level is open.
- **Advisory hints.** Whether a model author's preferred join type or filter-propagation
  direction is carried as discoverable hints (Sections 10.2, 11.3); part of Ossie's broader
  hints effort.
- **Ossie annotations without a SQL-native home.** Ossie `ai_context` instructions and
  examples, `custom_extensions`, and expression dialects other than the one selected for a
  measure or field have no required representation in this interface and do not affect query
  semantics; an engine MAY surface them out-of-band (synonyms, labels, and descriptions do have
  a metadata home; Section 11.4).

Deferred interface and interop concerns:

- **Error model.** Standard error classes for the constructs this specification requires be
  rejected (Sections 4, 7), so a tool can distinguish a type violation from an unsupported
  feature from a transient failure. Currently engine-specific.
- **Interface version and capability negotiation.** How a tool discovers, at connection time,
  that an endpoint implements this interface, at what version, and with which optional
  capabilities (discovery relations, relationship metadata).
- **Conformance test suite.** A shared, portable suite that makes the conformance criteria
  (Section 12) verifiable across engines rather than self-certified; this requires pinning
  enough concrete syntax to write portable tests.
- **Adjacent concerns.** Row-level and column-level security interaction with measure
  evaluation, and consumer result-cache reuse rules, are recognized but not addressed in this
  version.

---

## Appendix A. Worked examples (non-normative)

These illustrate that ordinary tool-generated SQL, using only the `AGG()` substitution,
composes with the guarantees above. They assume a model with measure relations `orders`
(one row per order), `sales` (one row per sale), and `tickets` (one row per ticket).

### A.1 Simple aggregation

```sql
SELECT category, AGG(total_quantity)
FROM orders
GROUP BY category;
```

`total_quantity` is aggregated over orders and reported per category (G1).

### A.2 Top-N filter via self-join

A tool keeps the top 2 states by revenue by computing them in a subquery and joining back:

```sql
SELECT o.category, AGG(o.total_quantity)
FROM orders o
JOIN (
  SELECT state_id, AGG(total_revenue) AS r
  FROM orders
  GROUP BY state_id
  ORDER BY r DESC, state_id
  LIMIT 2
) t ON o.state_id = t.state_id
GROUP BY o.category;
```

The join filters rows before `AGG(o.total_quantity)` is evaluated (G1) and does not inflate
it (G2). Because the interface is standard relational SQL, the `state_id` values the
subquery ranks are exactly the `state_id` values it joins back on. The field domain is fixed by
the relation and its filters, independent of which measure is selected (closure, Section 8.4). A
wide-table interface whose per-query domains shift with the measures requested has no such
guarantee: the subquery's top two states need not match those in the outer join, so the filter
can silently keep the wrong rows. When the filtered and displayed fields coincide, closure also
lets a tool fold the subquery into the main query.

### A.3 Level-of-detail two-stage aggregation

Average, over states, of each state's total revenue:

```sql
SELECT d.category, AVG(m.state_revenue)
FROM (SELECT category, state_id FROM orders GROUP BY category, state_id) d
JOIN (SELECT state_id, AGG(total_revenue) AS state_revenue
      FROM orders GROUP BY state_id) m
  ON d.state_id IS NOT DISTINCT FROM m.state_id
GROUP BY d.category;
```

The inner `AGG(total_revenue)` is duplication-free per state (G2); the outer `AVG` is an
ordinary aggregate over the already-evaluated scalar.

### A.4 Multi-fact ratio

```sql
SELECT s.region,
       AGG(t.ticket_count),
       AGG(s.total_sales),
       AGG(s.total_sales) / AGG(t.ticket_count) AS sales_per_ticket
FROM tickets t JOIN sales s ON t.region = s.region
GROUP BY s.region;
```

The many-to-many join on `region` neither inflates `ticket_count` nor `total_sales`; each is
evaluated over its own source rows (G2), and the ratio is applied per region afterward (7.2).

### A.5 Fan-out: joining to a finer-grained table

Revenue per category, with the fact joined to its line items (one order -> many items):

```sql
SELECT o.category, AGG(o.total_revenue)
FROM orders o
JOIN order_items i ON i.order_id = o.order_id
GROUP BY o.category;
```

The join produces one row per order item, but `AGG(o.total_revenue)` aggregates each order
once (G2): each order's revenue is counted once, not once per line item.

### A.6 Join type: the tool's choice

The same measures under an inner and a left join, differing only in the join type:

```sql
-- inner: only regions with both sales and tickets
SELECT s.region, AGG(s.total_sales), AGG(t.ticket_count)
FROM sales s JOIN tickets t ON s.region = t.region
GROUP BY s.region;

-- left: every sales region; ticket_count is 0 where a region has no tickets
SELECT s.region, AGG(s.total_sales), AGG(t.ticket_count)
FROM sales s LEFT JOIN tickets t ON s.region = t.region
GROUP BY s.region;
```

The join type is the tool's analytic choice, not something the interface prescribes (Section 10.2).
Either way each measure is evaluated over its own source rows (G2). The left join keeps a region
that has sales but no tickets; there `AGG(t.ticket_count)` has no source rows and takes its
empty-input value (0 for `COUNT`, Section 8.1), while the inner join omits that region entirely. For
any region in both results the `AGG()` values are identical: the standard fixes measure values
for a given query, and the join choice fixes which rows appear.

### A.7 Multi-fact via a conformed-dimension bridge

`sales` (one row per sale) and `tickets` (one row per ticket) are independent facts sharing a
conformed `regions` dimension, keyed by `region_id`. Per Section 10.3, a cross-fact measure needs a
home relation whose definition already includes the joins to both facts. So it is defined not on the
bare `regions` dimension but on a relation built from it. Here that relation is a `region_metrics`
view:

```sql
-- illustrative: the home relation folds the joins to both facts into its own definition
CREATE VIEW region_metrics AS
SELECT r.region_id,        -- field
       r.region_name,      -- field
       (AGG(sales.total_sales) / AGG(tickets.ticket_count))
           TO MEASURE AS sales_per_ticket   -- derived measure
FROM   regions r
LEFT JOIN sales   ON sales.region_id   = r.region_id
LEFT JOIN tickets ON tickets.region_id = r.region_id;
```

`region_metrics` carrying `sales_per_ticket` is then an ordinary measure relation. A consumer
evaluates `AGG(sales_per_ticket)` and MAY join `region_metrics` to further tables with
ordinary SQL: the measure stays duplication-free (G2), its `region_id` domain is fixed (closure,
Section 8.4), and no model re-declaration is needed to bring in another table.

This is how Layer 2 covers multi-fact metrics without free-floating measures (Section 10.3):
making the bridge a relation makes explicit two things a floating measure, left to a per-query
join path, would leave to inference:

- **The join path that bridges the facts** is fixed by the home relation's definition, so a
  different bridge (a different conformed dimension, or different join types) is a different,
  separately declared measure, not a per-query guess. Two multi-fact measures that differ only in
  how the bridge is built are two distinct, individually correct measures here, rather than one
  ambiguous result.
- **A stable domain**: `sales_per_ticket` is defined for every region, with the empty-input fill
  of Section 8.1 where a fact has no rows, not a domain that shifts with which measures a query
  happens to select.

---

## Appendix B. Glossary

Apache Ossie terms (Section 3.4):

- **Apache Ossie (Open Semantic Interchange).** The vendor-neutral semantic-model standard this
  interface exposes; model spec version `0.2.0.dev0`.
- **Dataset (Ossie).** A logical table (fact or dimension) in an Ossie model; exposed here as a
  *relation*.
- **Field (Ossie).** A scalar column of a dataset; our *field* (Ossie's term, adopted).
- **Metric (Ossie).** An aggregate expression, declared on a dataset (`datasets[].metrics`) or at
  the Ossie *model* level. A dataset-scoped metric is exposed here as a *measure* on its dataset's
  relation (Section 10.3).
- **Relationship (Ossie).** A directional link between datasets (`from`/`to`,
  `from_columns`/`to_columns`); `from` is the many side, `to` the one. Surfaced via the
  relationship model of Section 11.3.

Interface terms:

- **Field.** An ordinary (non-measure) SQL column of a relation, of scalar type `T`.
- **Measure.** A column of type `T measure` carrying an unevaluated aggregate calculation and
  the row identity of its source rows.
- **Measure relation.** A relation with at least one measure column.
- **Row identity.** The identity of the source rows a measure aggregates, fixed at
  definition: distinct source rows have distinct identities, and equal-identity rows are the
  same source row. The **row-identity key** (Section 9) is one implementation of it.
- **Input set.** For a measure and an output group, the set of the measure's source rows that
  the group's rows derive from; the measure's aggregate is evaluated over it (Section 8.1).
- **`AGG(m)`.** The aggregate function that evaluates measure `m` over its input set,
  returning `T`.
- **Base / derived measure.** A measure defined directly from an aggregate expression, versus
  one defined from `AGG()` over other measures.
- **Multi-table measure.** A derived measure referencing measures from more than
  one relation.
- **G1 (slice-and-dice).** Measures respond to WHERE and GROUP BY like standard aggregates.
- **G2 (no duplication).** Measures aggregate over their own source rows without duplication
  regardless of query shape.
- **Closure.** A measure relation behaves as a relation; its field domain is independent of
  which measures are queried.
- **Fan-out / chasm trap.** The two classic duplication errors G2 eliminates.

---

## Appendix C. Source material

- J. Hyde and J. Fremlin. *Measures in SQL.* SIGMOD-Companion 2024.
  https://arxiv.org/abs/2406.00251. The measure model this specification builds on: a measure
  attaches a context-sensitive calculation to a table and expands transparently into ordinary SQL.
- Ossie proposal: [BI-Semantic Layer Interface](https://docs.google.com/document/d/1uKc3x9NlvV9O9UPdvlnCTDxsc-XWsQrxb0ylG45H-IQ/edit).
  Transparent model with correctness-guaranteed measures; the interface shape adopted here.
- Ossie proposal: [Layered Query Interface](https://docs.google.com/document/d/1FOtRNBu6UqA2yeOwUa4jt1r2v45BWQJqS5_BTk34a1c/edit).
  The Layer 1 / 2 / 3 framing of Section 1.1.

---

## Appendix D. Relationship to *Measures in SQL*

This interface builds on the measure model of Hyde and Fremlin,
[*Measures in SQL*](https://arxiv.org/abs/2406.00251). It keeps that model's core: the `T measure`
column type, the transparent expansion of a measure into ordinary SQL, and closure (a relation
with measures behaves like any other relation). The rest of this appendix records where it
differs (naming, syntax, and how a measure is evaluated), where it makes choices the paper leaves
open, and some considerations for extending the proposal later.

### Naming and syntax

The evaluation function is `AGG()`, not the paper's `AGGREGATE()`. `AGGREGATE` already names a
different function in some engines (in Databricks and DuckDB it is a function over arrays or
lists), and the evaluation function needs a name unlikely to collide with existing functions or
reserved words across popular SQL engines. Earlier drafts of this specification used `MEASURE()`,
which reads too much like the `TO MEASURE` definition marker; engines MAY keep it as a synonym
(Section 6).

A measure is defined with `expr TO MEASURE AS name`, not the paper's `expr AS MEASURE name`.
`AS MEASURE` is cleaner, but ambiguous: in standard SQL `expr AS measure` already means "give this
column the alias `measure`."

### Evaluating a measure

A measure is evaluated only through `AGG()` (R2), which corresponds to the paper's `AGGREGATE()`.
The paper defines `AGGREGATE(m)` as `m` evaluated `AT (VISIBLE)`: over the rows that satisfy the
query's grouping, `WHERE` filters, and join conditions. `AGG()` behaves the same way (Section 8.1).

The paper also lets a bare measure reference be evaluated, in a default context that ignores the
query's `WHERE` clause. This specification rejects a bare measure in an aggregated query (Section
7.2), for two reasons. First, in SQL an aggregate function is what turns a projection into an
aggregation, and implicit aggregation breaks that expectation. Second, requiring `AGG()` keeps the
conversion from `T measure` to `T` in one visible place. Evaluating a measure while ignoring the
query's filters is, in this specification's terms, an evaluation control (Section 5.3).

### Where this specification makes choices the paper leaves open

The paper defines evaluation informally and calls for a formal semantics as future work. Section
8.1 defines a measure's input set for every operator between its definition and its evaluation.
That settles cases the paper leaves open, in a way we believe matches its intent:

- **Filters and joins in intermediate queries.** A `WHERE` filter or join in a subquery or view
  that passes a measure through restricts the measure, just as one in the evaluating query does.
- **Joins in the evaluating query.** A measure's input set in each group is defined even when the
  group keys come from another relation in the join.
- **Outer joins.** Null-padded rows contribute nothing to a measure on the padded side.

### Extending the proposal

This version does not include evaluation controls (Section 5.3), and so does not adopt the paper's
`AT` operator. The functionality they enable, such as percent of total and period over period, is
important, and we strongly recommend it as a follow-up extension. An extension will need to
settle:

- **Where controls are written.** The paper applies `AT` in the consuming query. Controls could
  also be declared in a measure's definition, or in any query between the definition and the
  evaluation. An extension may allow any of these. Where controls apply at more than one level,
  they should compose so that a level closer to the consumer cannot override what a level closer
  to the definition specified. The paper's composition rule, under which outer modifiers apply
  first and inner ones last, has this property.
- **Which filters a control may lift.** The paper's `SET` can reach rows that the evaluating
  query's `WHERE` removed. An extension must say which filters can be lifted this way. Filters in
  intermediate queries, such as a view that restricts rows for security, should not be.
- **Identifying dimensions.** `SET` and `ALL` act on the terms for a given dimension. The paper
  leaves open how terms on related expressions are matched (for example `YEAR(date)` and `date`)
  and how dimensions are linked. An extension needs an answer that works over arbitrary SQL
  expressions.
