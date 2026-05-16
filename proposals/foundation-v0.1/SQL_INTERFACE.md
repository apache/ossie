# SQL Interface for OSI Semantic Views — Foundation

**Status:** Draft 1 · 2026-04-25
**Implementation status:** **specification only — no parser in
`osi_python` today.** The error codes this document defines
(`E1201`–`E1213`) are carved out in `src/osi/errors.py` with the
`RESERVED` annotation so that when a SQL parser ships it can use
stable codes without a numbering change. Callers today build
semantic queries through the Python API
(:class:`osi.planning.SemanticQuery`) or through the compliance-suite
adapter's JSON query format. Actively raised E12xx codes today are
``E1206`` / ``E1207`` / ``E1208`` / ``E1209`` / ``E1212`` — these
serve both the future SQL surface and the declared-metric shape
validator in `osi.planning.metric_shape`. See
[`../docs/ERROR_CODES.md`](../docs/ERROR_CODES.md) for each code's
current status.

**Applies to:** The Foundation defined in [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md).
**Relationship to existing work:**

- Refines §5.1 of [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md),
  which shows the `SEMANTIC_VIEW(...)` example but does not define
  conformance rules.
- Is the Foundation successor to earlier `SELECT SEMANTIC_AGG` /
  `SELECT SEMANTIC` style surfaces. The grain/filter property-block
  syntax (`{GRAIN FIXED (...)}`, `{FILTER '...'}`, `{JOINS PATH ...}`)
  is **explicitly out of scope** here — those belong to the deferred
  grain/filter layer.
- Aligns deliberately with Snowflake's `SEMANTIC_VIEW(...)` clause and
  the errata documented in
  [`../../impl/python/docs/ERRATA_ALIGNMENT.md`](../../impl/python/docs/ERRATA_ALIGNMENT.md).

---

## 1. Motivation

A Foundation OSI deployment needs a portable SQL surface so that
existing SQL tools — editors, JDBC/ODBC clients, notebooks, BI tools,
LLM agents — can read and write semantic queries without learning a new
API. SQL is the lingua franca.

Two surfaces are in common use today:

1. **A dedicated table function** — Snowflake's `SEMANTIC_VIEW(...)`,
   ThoughtSpot's `SEARCH` clause, Power BI's `EVALUATE` (DAX). Explicit
   about the semantic nature of the query. Easy to validate.
2. **A view-like surface** — `SELECT ... FROM semantic_view` treated as
   a regular SQL view. Low friction, but inherits every ambiguity of
   SQL's row-based evaluation model.

OSI's Foundation takes the first approach as **authoritative** and the
second as a **recommended but optional** convenience. The authoritative
form is called the **`SEMANTIC_VIEW` clause** in this document; the
optional convenience form is called **bare-view SQL**.

Design constraints for this spec, in priority order:

1. Every Foundation semantic query expressible in the structured form
   of §5.1 of `Proposed_OSI_Semantics.md` must be expressible as
   `SEMANTIC_VIEW` SQL, and round-trip losslessly.
2. Conformance rules are defined purely in terms of syntax + the thin
   slice's semantic rules. No grain, no filter context, no LOD. If a
   deferred feature leaks in, it's a bug in this spec.
3. A Snowflake user who already knows `SEMANTIC_VIEW(...)` can read
   OSI's clause without a manual. The differences are the errata fixes.
4. The spec is precise enough that two independent implementations
   produce identical results for the same `(model, SQL)` pair, modulo
   row ordering for unordered queries.

---

## 2. Two surfaces at a glance

### 2.1 `SEMANTIC_VIEW` clause (authoritative)

```sql
SELECT *
FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.market_segment, orders.order_year
  METRICS    orders.total_revenue, orders.order_count
  WHERE      orders.status = 'completed'
)
HAVING total_revenue > 1000
ORDER BY total_revenue DESC
LIMIT 50;
```

Direct one-to-one mapping with the structured Semantic Query clauses
(`Proposed_OSI_Semantics.md §5.1`):

| SQL surface                                  | Semantic Query clause |
|:---------------------------------------------|:----------------------|
| `SEMANTIC_VIEW(<name> …)`                    | Target model          |
| `DIMENSIONS …`                               | `Dimensions`          |
| `METRICS …` (and `FACTS …`, see §5.3)        | `Measures`            |
| `WHERE` *inside* the clause                  | `Where` (pre-agg)     |
| `HAVING` *on the outer `SELECT`*             | `Having` (post-agg)   |
| `ORDER BY` *on the outer `SELECT`*           | `Order By`            |
| `LIMIT` *on the outer `SELECT`*              | `Limit`               |
| Bind parameters in the outer `SELECT`        | `Parameters`          |

### 2.2 Bare-view SQL (optional convenience)

```sql
SELECT market_segment, AGG(total_revenue) AS rev
FROM   sales_analytics
WHERE  status = 'completed'
GROUP BY market_segment
HAVING AGG(total_revenue) > 1000
ORDER BY rev DESC
LIMIT 50;
```

Recognisable as a regular `SELECT`. Lowers the barrier for BI tools and
ad-hoc queries but comes with stricter rules (§6) because the SQL
parser has to infer semantic intent from conventional SQL shape.

**OSI-conformant implementations MUST support the `SEMANTIC_VIEW`
clause. They MAY additionally support bare-view SQL.** Bare-view SQL is
a convenience and every Foundation feature must be expressible through
the clause form.

---

## 3. Grammar (`SEMANTIC_VIEW` clause)

### 3.1 BNF

```
semantic_view_query :=
    SELECT select_list
    FROM semantic_view_clause
    [ HAVING having_expr ]
    [ ORDER BY order_by_list ]
    [ LIMIT integer [ OFFSET integer ] ]

semantic_view_clause :=
    SEMANTIC_VIEW '('
        model_name
        [ DIMENSIONS dim_list ]
        [ FACTS     fact_list ]
        [ METRICS   metric_list ]
        [ WHERE     pre_agg_expr ]
    ')'
    [ AS alias_spec ]

dim_list    := dim_item    ( ',' dim_item    )*
fact_list   := fact_item   ( ',' fact_item   )*
metric_list := metric_item ( ',' metric_item )*

dim_item    := [ dataset '.' ] field_or_expr        [ [ AS ] alias ]
fact_item   := [ dataset '.' ] field_or_expr        [ [ AS ] alias ]
metric_item := [ dataset '.' ] metric_name          [ [ AS ] alias ]
             | AGG '(' metric_ref ')'               [ [ AS ] alias ]   -- see §5.1

field_or_expr := field_ref | scalar_expr_over_fields

select_list := '*' | select_item ( ',' select_item )*
select_item := alias_from_clause | scalar_expr_over_clause_output
             [ AS result_alias ]

alias_spec := identifier | identifier '(' column_alias_list ')'
```

### 3.2 Conformance statements

- Keywords (`SEMANTIC_VIEW`, `DIMENSIONS`, `FACTS`, `METRICS`, `WHERE`,
  `HAVING`, `ORDER BY`, `LIMIT`, `OFFSET`, `AGG`, `AS`) are reserved
  and case-insensitive. They are **not** valid field or metric names.
- The `AS` keyword is optional for aliases (matching Snowflake).
- `DIMENSIONS`, `FACTS`, and `METRICS` clauses are each optional, but
  **at least one of them MUST be present**. An empty `SEMANTIC_VIEW(sv)`
  is an error (`E1201` — see §8).
- The four semantic clauses `DIMENSIONS | FACTS | METRICS | WHERE` MUST
  appear in that order. This is more restrictive than Snowflake but
  matches the structured query shape and eliminates the "same clauses,
  different meaning based on order" ambiguity.
- `LIMIT`, `ORDER BY`, `HAVING`, and `OFFSET` MUST appear on the outer
  `SELECT`, not inside the clause. This deviates from some other
  dialects but matches the Foundation Semantic Query model where
  `Limit` and `Order By` are finalisation steps after the row set is
  materialised.

### 3.3 Wildcard expansion

`DIMENSIONS dataset.*`, `FACTS dataset.*`, and `METRICS dataset.*` are
supported and expand to every public field / metric declared on
`dataset`. `DIMENSIONS *` (unqualified) is **not** supported — callers
MUST pick a dataset, because wildcard dimension selection across
datasets has undefined join semantics.

Example:

```sql
SELECT * FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.*
  METRICS    customers.customer_count
);
```

---

## 4. Reference resolution (both surfaces)

### 4.1 The reference grammar

Everywhere a name may appear (in `DIMENSIONS`, `FACTS`, `METRICS`,
`WHERE`, `HAVING`, `ORDER BY`, parameters, and the outer `SELECT` list
when legal), the name takes one of three forms:

| Form                       | Example                      | Resolves against                              |
|:---------------------------|:-----------------------------|:-----------------------------------------------|
| Bare name                  | `total_revenue`              | Global scope: metrics → named filters → parameters |
| Dataset-qualified          | `orders.total_revenue`       | `orders` dataset → its fields / metrics       |
| Three-part (physical)      | `sales_analytics.orders.amount` | Reserved for future use — **E1203** in Foundation |

Bare names that collide across datasets are ambiguous — the
`Proposed_OSI_Semantics.md §4.7` rule applies: **same-named
expressions MUST be reachable via `dataset.field` qualification**.
Implementations MUST reject bare references when there is a collision
(`E1204`) and MUST accept the dataset-qualified form without
complaint. This directly fixes Snowflake errata #16 and #17.

### 4.2 Output column names and duplicate-name handling

The output of a `SEMANTIC_VIEW` clause is an ordinary row set with
named columns. Column names come from (in order of priority):

1. The explicit `AS alias` on the item.
2. A table-alias-assigned column from the outer `AS alias(col1, col2,
   ...)` clause.
3. The unqualified name of the field or metric (after
   `normalize_identifier` — upper-case unless quoted).

Implementations MUST reject queries whose output column list contains
duplicate names (`E1205`). This differs from Snowflake, which allows
duplicate unqualified names in the output and relies on positional
access. The Foundation rejects this because it makes the outer
`SELECT`'s column references ambiguous, and because downstream tools
(JDBC / pandas / SQL LLMs) cannot safely address duplicate columns.

Callers disambiguate collisions explicitly, e.g.:

```sql
SELECT * FROM SEMANTIC_VIEW(
  duplicate_names
  DIMENSIONS customers.name AS customer_name,
             orders.name    AS order_name
);
```

or via a table-alias column list:

```sql
SELECT * FROM SEMANTIC_VIEW(
  duplicate_names DIMENSIONS customers.name, orders.name
) AS t(customer_name, order_name);
```

### 4.3 Outer-query references

`HAVING`, `ORDER BY`, and any `SELECT`-list scalar expression on the
outer query reference the clause's **output column names** — i.e. the
aliases assigned in §4.2. Dataset-qualified references (`orders.foo`)
are **not visible** outside the clause. This matches Snowflake (errata
#10) and is a direct consequence of treating the clause as a
table-valued expression.

```sql
SELECT market_segment, total_revenue
FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.market_segment
  METRICS    orders.total_revenue
)
WHERE  market_segment = 'BUILDING'     -- ✓ uses output alias
ORDER BY total_revenue DESC;           -- ✓ uses output alias
```

### 4.4 Identifier casing

Identical to `Proposed_OSI_Semantics.md §4.7`: unquoted identifiers
fold to upper case; quoted identifiers are preserved verbatim. No
implementation-defined folding.

---

## 5. Metrics, facts, and ad-hoc aggregation

### 5.1 Three ways to produce a measure

| Form                                             | What it means                                          | Valid in `SEMANTIC_VIEW` clause? | Valid in bare-view? |
|:-------------------------------------------------|:-------------------------------------------------------|:---------------------------------:|:-------------------:|
| `METRICS orders.total_revenue`                   | Reference a pre-declared metric.                       | ✓                                 | via `AGG(...)` only |
| `METRICS orders.total_revenue AS rev`            | Same, aliased.                                         | ✓                                 | via `AGG(...)` only |
| `METRICS AGG(orders.total_revenue)`              | Explicit "aggregate this declared metric".             | ✓                                 | ✓                   |
| `METRICS SUM(orders.amount)`                     | **Ad-hoc** — aggregate applied to a fact.              | ✓ (see §5.2)                      | ✓ (as `SUM(amount)`)|
| `FACTS orders.amount`                            | Raw fact; no aggregation. See §5.3.                    | ✓                                 | — (see §6)          |

Inside the `METRICS` clause, a bare reference to a declared metric
(`orders.total_revenue`) and its `AGG(...)`-wrapped form are
**semantically identical**. Both request the metric's declared
aggregation, evaluated at the query's dimensional grain. Implementations
MAY normalise one form to the other internally.

### 5.2 Ad-hoc aggregates

Ad-hoc aggregates are `agg_function(expression_over_facts)` applied
inline to one or more facts. The `agg_function` MUST be a
REQUIRED aggregate from [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md):
`SUM`, `COUNT`, `COUNT(DISTINCT …)`, `COUNT(*)`, `MIN`, `MAX`, `AVG`.

**`COUNT(*)` is supported.** This is the Foundation fix for Snowflake
errata #4. `COUNT(*)` in the `METRICS` clause means "count rows of the
smallest dataset referenced by the query's other inputs, after the
query's `WHERE` is applied, at the query's dimensional grain." If the
dataset is ambiguous, `COUNT(*)` MUST be dataset-qualified:
`COUNT(orders.*)`.

Ad-hoc aggregates MUST NOT reference pre-declared metrics; aggregation
of a metric uses `AGG(...)`, not `SUM(metric)`. Writing `SUM(revenue)`
where `revenue` is a declared metric raises `E1206`.

### 5.3 `FACTS`

`FACTS dataset.field` returns the raw, unaggregated value of a fact.
The row cardinality of the output equals the dataset's row cardinality
after `WHERE` is applied. This matches Snowflake.

**Constraint (inherited from Snowflake errata #8):** A single
`SEMANTIC_VIEW(...)` call that uses `FACTS` MUST NOT also use `METRICS`.
Facts return unaggregated rows; metrics return aggregated rows; the
two cardinalities cannot co-exist in one result. Violations raise
`E1207`. `DIMENSIONS` and `FACTS` may be combined freely.

### 5.4 Cardinality of a dimension-only query

`SELECT * FROM SEMANTIC_VIEW(sv DIMENSIONS customers.market_segment)`
returns the **distinct** values of the dimension tuple. This resolves
Snowflake errata #2 and #3 in favour of the more user-friendly
behaviour: a dimension-only query is treated as "list the distinct
dimension values for which at least one related row exists at the
model's declared grain." This matches the `Proposed_OSI_Semantics.md
§5.2` rule that result cardinality is always `DISTINCT(Dimensions)`.

Bare-view SQL follows the same rule: `SELECT market_segment FROM sv`
returns distinct segments, not per-row values. Implementations that
wish to preserve per-row access MUST require an explicit `FACTS` clause
(via the clause form).

---

## 6. Bare-view SQL (optional surface)

### 6.1 Supported shape

```
SELECT [ DISTINCT ] select_item ( ',' select_item )*
FROM   <semantic_view_name> [ AS alias ]
[ WHERE  pre_agg_expr     ]
[ GROUP BY expr_list      ]
[ HAVING post_agg_expr    ]
[ ORDER BY order_by_list  ]
[ LIMIT integer [ OFFSET integer ] ]
```

**Supported:**

- `GROUP BY` explicitly lists the dimensions. The Foundation does NOT
  infer dimensions from un-aggregated projections (this is the
  `osi_impl` Variant C behaviour we found fragile) — an explicit
  `GROUP BY` is required whenever any measure appears in the projection.
- Select items may be dimensions, `AGG(metric)` references, or ad-hoc
  aggregates `SUM(fact) / COUNT(*) / …`.
- The outer `WHERE` is the query's `Where`. The outer `HAVING` is the
  query's `Having`.
- `SELECT DISTINCT` is accepted and has the same meaning as a
  dimensions-only clause query (§5.4); implementations MAY normalise
  one into the other.

**Rejected (`E1208`):**

- `SELECT *` (same reason as Snowflake errata #6 — `*` would conflate
  dimensions, facts, and metrics). Implementations SHOULD suggest an
  explicit `DIMENSIONS dataset.*` clause in the error message.
- Any `FROM` extension: `LATERAL`, `JOIN`, `UNNEST`, `MATCH_RECOGNIZE`,
  `PIVOT`, `UNPIVOT`.
- Raw window function syntax (`SUM(x) OVER (...)`). Window metrics are
  declared on the model and accessed via `AGG(metric_name)`. This
  matches Snowflake errata #22.
- `QUALIFY`, `CONNECT BY`, recursive CTEs.
- Sub-queries on the outer `SELECT`'s `WHERE` or `HAVING`.

### 6.2 `COUNT(*)` in bare view

`SELECT COUNT(*) FROM sv` is valid and equivalent to
`SEMANTIC_VIEW(sv METRICS COUNT(*))`. It counts rows of the canonical
grain dataset (the dataset that owns every dimension in the query, or
the unique root if there are no dimensions; if ambiguous,
`COUNT(dataset.*)` is required).

### 6.3 Single-aggregate grain in bare view

`SELECT AGG(order_count), COUNT(market_segment) FROM sv` (two aggregates
at the same grain, from different datasets) is **rejected** in the thin
slice (`E1209`). This closes Snowflake errata #1: in standard SQL, all
aggregates in a single `SELECT` share the same rowset, and the thin
slice refuses to silently break that invariant. Callers who want
cross-dataset aggregates in one query MUST use the clause form with an
explicit `DIMENSIONS` list that disambiguates the join grain, or run
two queries.

### 6.4 Table aliases

`FROM sv AS t` is accepted. Column references may use the alias
(`t.market_segment`) everywhere the unqualified name is accepted. The
alias does NOT introduce a way to disambiguate cross-dataset same-named
columns (that is what `dataset.field` in the clause form is for);
`t.name` remains ambiguous if both `customers.name` and `orders.name`
exist.

---

## 7. Evaluation order and the filter pipeline

Because the Foundation has no grain or filter-context overrides, the
evaluation pipeline is the canonical rewrite of
`Proposed_OSI_Semantics.md §5.2`, instantiated for SQL:

```
Step 1  Resolve model references (§4)
Step 2  Resolve join path across all referenced datasets (§6 of spec)
Step 3  Apply WHERE / clause-internal WHERE  ← pre-aggregation
Step 4  Aggregate at (DIMENSIONS) grain
Step 5  Apply HAVING                         ← post-aggregation
Step 6  Apply ORDER BY, LIMIT, OFFSET        ← finalisation
Step 7  Emit result set
```

### 7.1 No post-window WHEN in Foundation

Window metrics are not part of the Foundation, which means Snowflake
errata #18, #19, #23, #24, and #25 — all related to window + filter
ordering — **do not arise**. The spec is silent on them.

When window support lands as a deferred extension, this document will
grow a §7.2 defining the precise ordering. Until then, any syntax that
would produce a window metric (e.g. `AGG(metric)` where `metric` is
declared with `OVER (...)`) raises `E1210` with a message pointing to
the deferred `OSI_Proposal_Window_Metrics.md` (to be added later).

### 7.2 `HAVING` sees post-aggregation values

`HAVING` is post-aggregation. It references output column names
(§4.3). It MAY reference a measure that is not in the `SELECT` list (in
which case the implementation includes it internally and drops it
before emitting results). It MUST NOT reference facts or raw field
values — for row-level filtering, use `WHERE`.

---

## 8. Error taxonomy

All SQL-interface errors are in the `E12xx` range. They are raised
during parsing or reference resolution, before planning.

| Code   | Condition                                                             |
|:-------|:----------------------------------------------------------------------|
| `E1201` | `SEMANTIC_VIEW(sv)` has no `DIMENSIONS`, `FACTS`, or `METRICS` clause |
| `E1202` | Clause order is wrong (e.g. `METRICS` before `DIMENSIONS`)            |
| `E1203` | Three-part reference in Foundation (`schema.sv.col` or similar)       |
| `E1204` | Ambiguous bare reference where same-named entities collide            |
| `E1205` | Duplicate output column names not disambiguated                       |
| `E1206` | Pre-declared metric used inside a raw aggregate (e.g. `SUM(metric)`)  |
| `E1207` | `FACTS` and `METRICS` in the same clause                              |
| `E1208` | Unsupported SQL construct in bare view (`SELECT *`, `LATERAL`, …)     |
| `E1209` | Multi-dataset aggregates at implied cross-grain in bare view          |
| `E1210` | Window-function metric referenced (deferred feature)                  |
| `E1211` | Inner `LIMIT` / `ORDER BY` / `HAVING` in `SEMANTIC_VIEW(...)` clause  |
| `E1212` | `COUNT(*)` is ambiguous across datasets — qualify with `dataset.*`    |
| `E1213` | Bare name resolves to a parameter but is used as a dimension/measure  |

Error messages MUST cite the offending SQL token(s) with line and
column. Implementations MUST NOT return wrong SQL on any of these
conditions — fast, loud failure is required (Invariant I-1 in
`ARCHITECTURE.md`).

---

## 9. Worked examples

### 9.1 Minimal metric query (clause form)

```sql
SELECT * FROM SEMANTIC_VIEW(
  tpch_analysis
  DIMENSIONS customers.market_segment
  METRICS    orders.order_average_value
)
ORDER BY market_segment;
```

Round-trips to:

```yaml
query:
  dimensions: [customers.market_segment]
  measures:   [orders.order_average_value]
  order_by:   [{field: market_segment, direction: ASC}]
```

### 9.2 Same query, bare view

```sql
SELECT market_segment, AGG(order_average_value) AS avg_value
FROM   tpch_analysis
GROUP BY market_segment
ORDER BY market_segment;
```

Round-trips to:

```yaml
query:
  dimensions: [customers.market_segment]
  measures:
    - metric: orders.order_average_value
      output_name: avg_value
  order_by: [{field: market_segment, direction: ASC}]
```

The planner is identical for both forms after resolution.

### 9.3 `EXISTS_IN` semi-join (Foundation-legal)

```sql
SELECT * FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.market_segment
  METRICS    customers.customer_count
  WHERE      EXISTS_IN(orders, orders.status = 'returned')
);
```

`EXISTS_IN(orders, …)` is a Foundation semi-join (see
`Proposed_OSI_Semantics.md §6.3`). It is syntactically a function call
and fits inside the clause's `WHERE` without any further SQL surface
changes.

### 9.4 `COUNT(*)` (was broken in Snowflake)

```sql
SELECT * FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.market_segment
  METRICS    COUNT(orders.*) AS order_count
);
```

Unambiguous because `orders.*` picks the dataset. If the dataset can
be inferred from `DIMENSIONS`, callers MAY write `COUNT(*)`.

### 9.5 Top-N via outer `ORDER BY` + `LIMIT`

```sql
SELECT * FROM SEMANTIC_VIEW(
  sales_analytics
  DIMENSIONS customers.customer_name
  METRICS    orders.total_revenue
)
ORDER BY total_revenue DESC
LIMIT 10;
```

### 9.6 Rejected: `FACTS` + `METRICS` together

```sql
SELECT * FROM SEMANTIC_VIEW(
  sales_analytics
  FACTS   orders.amount
  METRICS orders.total_revenue
);
-- E1207: FACTS and METRICS cannot be combined in one SEMANTIC_VIEW clause
```

### 9.7 Rejected: raw window syntax in bare view

```sql
SELECT market_segment,
       SUM(orders.amount) OVER (PARTITION BY market_segment) AS win
FROM   sales_analytics;
-- E1210: window functions are a deferred feature; see specs/deferred/
```

---

## 10. Design choices and their rationale

### 10.1 Why promote the clause form to authoritative?

Because it is unambiguous. Every clause has exactly one semantic role,
and the clause boundary makes it trivial to reject out-of-scope
constructs (window functions, arbitrary `FROM` extensions). Bare-view
SQL is strictly a convenience and inherits every nuance from standard
SQL — the errata catalogue for Snowflake's bare-view surface is 25
items long, and many of those errata are latent ambiguities of SQL
itself.

### 10.2 Why tighten clause ordering beyond Snowflake?

Snowflake accepts clauses in any order. We do not, because:

1. Two queries that differ only in clause ordering should not look
   syntactically distinct in examples, documentation, or golden tests.
2. Parsers are simpler; error messages are better.
3. Every user who has ever written SQL expects a deterministic ordering
   (`SELECT`, `FROM`, `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`). The
   semantic-clause ordering here mirrors that intuition.

### 10.3 Why forbid duplicate unqualified column names?

Because same-named columns are a trap (Snowflake errata #16, #17).
Rejecting them forces authors to alias, which in turn forces the model
to be referenceable from bare-view SQL. This is a one-line annotation
cost in the source and eliminates an entire class of runtime-surprise
bug.

### 10.4 Why keep `FACTS` despite it disagreeing with `METRICS`?

Because some analytical questions genuinely want the raw rows — e.g.
"show me the first five orders that triggered the anomaly detector."
Forcing callers to define a metric for every such question is onerous.
The constraint that `FACTS` and `METRICS` cannot co-exist in one call
keeps the semantics crisp — a `FACTS`-using query is a row query; a
`METRICS`-using query is an aggregate query. Mixing them would require
row-level join semantics that are out of the Foundation.

### 10.5 Why support bare-view SQL at all?

Because BI tools (Tableau, Looker, Power BI, Superset, Metabase) and
most SQL LLMs generate `SELECT … FROM view GROUP BY …` by default,
often without any ability to emit a `SEMANTIC_VIEW(...)` clause. Making
bare-view SQL optional keeps conformance simple while lowering adoption
friction for implementations that want broader tool compatibility.

### 10.6 Alignment with Snowflake

We align on: keyword names (`SEMANTIC_VIEW`, `DIMENSIONS`, `FACTS`,
`METRICS`, `AGG`), the `dataset.field` reference convention, clause-vs-
outer-query split for `WHERE` / `HAVING`, and outer-query alias scoping
for `HAVING` / `ORDER BY`.

We intentionally diverge on: required clause ordering (§3.2), rejection
of duplicate output column names (§4.2), rejection of cross-dataset
ad-hoc aggregates in bare view (§6.3), support for `COUNT(*)` (§5.2),
and a Foundation-only rejection of window-metric syntax (§7.1). Every
divergence is tracked in [`docs/ERRATA_ALIGNMENT.md`](../docs/ERRATA_ALIGNMENT.md)
with its corresponding erratum number.

---

## 11. Open questions (for future revisions)

- **Window metrics.** Will be added in a dedicated deferred proposal.
  The SQL-interface question is whether window metrics are addressed
  via `AGG(metric_name)` (Snowflake's choice), via a distinct keyword
  like `WINDOW_METRIC`, or via a per-reference syntactic marker.
  Tracking: `specs/deferred/OSI_Proposal_Window_Metrics.md` (not yet
  written).
- **Parameter binding.** Named parameters (`:region`, `$region`, `?`)
  differ across SQL dialects. The Foundation uses positional `?` for
  portability, but Snowflake-style `:name` syntax is a recognised
  alternative. A future revision MUST pick one canonical form and a
  translation rule.
- **`SHOW SEMANTIC DIMENSIONS FOR METRIC`.** Snowflake offers an
  introspection command. We consider this out of the SQL-interface spec
  and in-scope for a future "semantic catalogue" spec.
- **Dialect translation.** This spec defines the surface syntax the
  compiler's parser accepts. Output SQL (the translated query fed to
  the backend) is handled in the codegen layer with SQLGlot; the two
  are decoupled.

---

## 12. Appendix — quick errata-to-spec map

| Snowflake ERRATA # | Foundation disposition                                  |
|:-------------------|:---------------------------------------------------------|
| #1 per-agg granularity | **Forbidden** in bare view (`E1209`); unambiguous in clause form |
| #2 dim-only dedup divergence | **Resolved** — both forms apply `DISTINCT(Dimensions)` |
| #3 dim-only per-expression granularity | **Resolved** — same as #2 |
| #4 `COUNT(*)` not supported | **Fixed** — `COUNT(*)` is REQUIRED (§5.2) |
| #5 `QUALIFY` rejected | Inherited (out of scope in Foundation) |
| #6 `SELECT *` fails | **Rejected**, with a better error (`E1208`) |
| #7 LEFT OUTER JOIN orphans | Inherited from join semantics (§6 of main spec) |
| #8 `FACTS` + `METRICS` error | **Kept** — encoded as `E1207` (§5.3) |
| #9 inner `LIMIT` syntax error | **Kept** — encoded as `E1211` (§3.2) |
| #10 outer `WHERE` uses aliases | **Kept** — formalised in §4.3 |
| #11 ad-hoc vs pre-defined | Equivalent (§5.1) |
| #12 decomposability | Inherited |
| #13 `COUNT(DISTINCT)` granularity | Inherited |
| #14 `EXPLAIN` works | Out of scope for this spec |
| #15 metric aliasing | Supported (§4.2) |
| #16 same-named expressions | **Fixed** — `E1204` + §4.1, §4.2 |
| #17 same-named metrics | **Fixed** — same as #16 |
| #18–#25 window-related | **Deferred** — window metrics not in Foundation |

All Foundation errata handling is re-stated in
[`docs/ERRATA_ALIGNMENT.md`](../docs/ERRATA_ALIGNMENT.md) with the
corresponding test identifiers.
