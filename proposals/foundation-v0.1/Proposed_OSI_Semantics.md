# Proposed OSI Semantics — Foundation

**Status:** Draft Proposal
**Spec version:** `0.1`
**Author:** will.pugh@snowflake.com
**Date:** 2026-04-25

A semantic model MAY declare the spec version it conforms to via a top-level `osi_version: "0.1"` key. A model that omits the key is interpreted under the latest version the engine supports; engines MAY emit a diagnostic in that case. Future revisions of this Foundation increment the minor version (`0.2`, `0.3`, …) and remain additively compatible — a model written against `0.1` MUST continue to validate and produce identical results under any later `0.x` version.
**Related specs:**
- [OSI Core Metadata Spec](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md)

- [SQL_EXPRESSION_SUBSET](https://docs.google.com/document/d/1Jn98kHWsnbvo1MycQBAnWirGTwRmw-DWaen7coNCYWA/edit?usp=sharing)

---

## 1. Motivation and Scope

This proposal defines an initial set of semantics for OSI that allows us to create an initial core base that we can use to build later abstractions on top of. It is consistent with the core abstractions, but holds off on APIs around grain and filter control. Standard SQL window functions are in scope (§6.10); only the genuinely non-portable extensions to windows — parameterized frame bounds, `GROUPS` frame mode, ordered-set aggregates with `WITHIN GROUP`, and windowed-metric composition — are deferred. These deferred features are powerful, and many BI tools have them, but with different design decisions for how they should work.

This will focus on:
1. **Core OSI semantics** — datasets, fields, relationships, metrics, and a minimal query model, without grain overrides or filter-context propagation.
2. **Well-defined join semantics** — how relationships are declared, how cardinality is inferred, how the planner picks join types per context, and what safety rules prevent silent incorrectness.
3. **A fixed SQL subset** — the expression language allowed inside metric, field, and filter expressions, so that portable implementations can be written and two tools can agree on what a metric means.

Anything that falls outside these is explicitly deferred. §10 lists deferred features and the companion proposal that addresses it.

## 2. Design Principles

1. **Portable first.** Every construct in this proposal must be representable in ANSI SQL:2003.
2. **Safe by default.** Where SQL would silently produce wrong results (fan-out, chasm trap), OSI either disallows the operation or substitutes a safe rewrite. No query should compute a wrong answer because the author did not add a guard.
3. **Additive extension path.** Every feature deferred to §10 is additive: adopting it does not require reworking anything in this initial base. A model that uses only this base remains valid under the full spec.
4. **Declare intent, not execution.** Model authors describe *what* their data means (cardinality, referential integrity, relationship). The planner decides *how* to execute (join type, CTE structure, rollup order).
5. **Trust-but-don't-validate.** OSI trusts declared primary keys, unique keys, and referential integrity. Data-quality enforcement is upstream (ETL, dbt tests, etc.), not a semantic-layer concern.

## 3. What is In / What is Out

The Foundation surface is deliberately narrow, several important features are pushed out, in order to focus on the foundational semantics first.

| In (Foundation v0.1) | Out (deferred to §10) |
|:---|:---|
| Datasets, fields, metrics, relationships (§4) | Aggregate-bodied fields and dataset-namespaced metrics (§4.3, §4.5; `E_AGGREGATE_IN_FIELD`, `E_DEFERRED_KEY_REJECTED`) |
| Aggregation and Scalar query shapes — `Dimensions`, `Measures`, `Fields`, `Where`, `Having`, `Order By`, `Limit` (§5.1) | Per-metric / per-dataset / per-model filter context (filter inheritance, named filters, `CALCULATE`-style overrides) |
| Equijoin relationships, single-column or composite (§4.4) | Rich join semantics — non-equijoin, ASOF, range (§10) |
| Cardinality **inferred** from declared primary / unique keys (§6.4) | Cardinality and referential integrity **declared** on relationships (`referential_integrity`, `from_all_rows_match`, `to_all_rows_match`) |
| Aggregate-before-join safety (§6.7) | Grain-based operations — explicit `FIXED` / `INCLUDE` / `EXCLUDE` / `TABLE` overrides (LOD equivalents) |
| Chasm-trap safety (independent fact computation + join on shared dims) (§6.7, §6.8.2) | Nested aggregation in metric expressions (`AVG(AVG(...))`, `AVG(COUNT(...))`; `E_NESTED_AGGREGATION_DEFERRED`) |
| Fan-out safety (§6.7, §6.10.3, §5.1.2) | Variables / parameters in queries or models |
| M:N traversal with a safe-result guarantee — bridge de-duplication (§6.8.1) and stitching dimensions (§6.8.2); every aggregate category accepted bare, no fan-out, no chasm | Path disambiguation (per-metric `using_relationships`, per-metric `joins.type` overrides) — ambiguous paths are surfaced as `E_AMBIGUOUS_PATH`, not silently picked |
| SQL expression subset — core scalar and aggregation functions, plus `COUNT(DISTINCT)` over a bridge (§7, [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md)) | Hierarchies (rollup paths, parent-child models) |
| Standard SQL window functions — ranking, navigation, aggregate-windows; `ROWS` / `RANGE` frame modes; integer-literal frame bounds (§6.10) | Non-portable window features — `GROUPS` frame, parameterised frame bounds, `WITHIN GROUP` ordered-set aggregates, windowed-metric composition |

A complete deferred-features registry, with one row per future proposal, is in §10.


---

## 4. Semantic Model

### 4.1 Top-Level Structure

The top-level structure consists of the following keys — `name`, `description`, `dialect`, `ai_context`, `datasets`, `relationships`, `metrics`, and `custom_extensions`. The Foundation introduces no additional top-level keys; the field-level shape for each section is defined in the subsections below.

### 4.2 Datasets

A dataset is a logical table backed by a physical SQL source.

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique within the model |
| `source` | string | Yes | `database.schema.table` or a SQL subquery |
| `primary_key` | array | No | Columns that uniquely identify rows |
| `unique_keys` | array of arrays | No | Additional unique constraints |
| `description` | string | No | |
| `ai_context` | string / object | No | |
| `fields` | array | No | See §4.3 |
| `custom_extensions`  | array | No | vendor specific attributes |

**Primary and unique keys are semantic assertions about row uniqueness.** They feed cardinality inference for relationships (§6.4). Models that omit them remain valid: the engine MUST still produce correct, safe results, but it has to assume worst-case cardinality (`N : N`) and may emit more conservative SQL — possibly less efficient, never less correct.

Implementations MAY decide to require `primary_key` declarations in order to have a well-defined table grain. In that case, they MUST reject models that omit primary keys with `E_PRIMARY_KEY_REQUIRED`.

Engines SHOULD trust the keys defined in the dataset, and do not have to validate uniqueness beyond what is specified in the model.

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    primary_key: [order_id]
    unique_keys:
      - [order_number]
    fields: [ ... ]

  - name: customers
    source: sales.public.customers
    primary_key: [id]
    fields: [ ... ]
```

#### 4.2.1 Grain

Grain is defined as the set of fields that uniquely define a row.  

For a Dataset, we will use the term **`Table Grain`** (also: **home grain**) to denote the grain the dataset naturally lives at — its primary key (or any declared unique key). If a Dataset does not have a key, there is no way to uniquely identify a row.

> *Note on terminology.* Some BI traditions use the phrase "natural grain" for the same per-dataset concept. The Foundation deliberately avoids that wording because a deferred proposal uses `natural_grain` as a reserved model-level declaration that sets the home grain for the entire model. To prevent confusion this spec uses "home grain" or "table grain" exclusively for the per-dataset concept.

Grain for an aggregated query is the set of dimension fields in the query (or subquery).

For example, in the datasets defined above the grain of `orders` would be `order_id` and/or `order_number`.

For aggregations queries (or sub-queries) the grain will be the dimensions (group by ) fields.  For example, the grain of

```sql
select order_location, count(order_id) 
from orders 
group by 1
```

is `order_location`, because after the aggregation we know that `order_location` uniquely defines a row.

### 4.3 Fields

Fields are named expressions on a dataset. A field's expression is either a scalar (row-level) expression, a window function evaluated at the home grain, or a boolean form of either. **Aggregate expressions in fields are not part of the Foundation** — every aggregate is a metric (§4.5), and metrics live at the top-level `metrics:` section only. The shape of the expression — not a declared tag — determines how a reference to the field is routed in a query.  Expansion of metrics onto fields will be handled in follow-up proposals.

> **Uses existing [`OSI Core Metadata.md`](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md) §Fields, with behaviour extension.** The core metadata spec says field expressions are *"scalar SQL expressions (no aggregations)"*. The Foundation upholds that rule with one extension: a field's `expression` MAY also include a standard SQL window function evaluated at the home grain (§6.10). Aggregate-bodied fields, including aggregates over the home dataset's own columns, are deferred to §10 along with all other dataset-namespaced metric forms — write the aggregate at the top-level `metrics:` section instead. A field expression that contains any aggregate function MUST raise `E_AGGREGATE_IN_FIELD`.

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique within the dataset |
| `expression` | string \| object | Yes | A non-aggregate SQL expression in the OSI expression subset (§7). The schema for `expression` (string form and structured per-dialect form) is defined normatively in [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md) §"Per-Dialect Expression Form"; the bare-string form is the default. |
| `description` | string | No | |
| `ai_context` | string / object | No | Synonyms, examples |
| `access_modifier` | enum | No | `public` (default) or `private` (hide from query) |

**Expressions** are tied to a dialect and to the home grain of the dataset they are attached to. They evaluate to one scalar value per home-dataset row (or one window-frame result per row).

Cross-dataset references inside a field's expression are governed by the grain rules in §4.3.1.

#### 4.3.1 Cross-dataset references in field expressions

This subsection governs what a field's expression may reference across relationships. The rule is restrictive on purpose: any aggregation packaged as a *field* (whether cross-grain or same-grain) would carry an implicit "this resolves at the home grain" pin, and the semantics of such a construct under a query-level `Where` clause is exactly the choice the §10 grain proposal is meant to settle explicitly. The Foundation defers all field-level aggregation rather than picking a default that §10 might overturn; aggregations are expressed as model-scoped metrics (§4.5).

**Routing by cardinality.** When a field's expression references columns or fields from a related dataset, the routing depends on the cardinality of the relationship path from the field's home dataset to the referenced dataset:

- **Same or lower granularity** (referenced dataset is reachable via `N : 1` / `1 : 1` edges from the home dataset): the reference is allowed directly and is enriched onto each row of the home dataset along the unambiguous path. Path-disambiguation is deferred (§10), so engines MUST raise `E_AMBIGUOUS_PATH` when more than one path exists.

- **Higher granularity** (referenced dataset is reachable via `1 : N` edges): a non-aggregate reference at higher grain raises `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` (D-024). An aggregate-wrapped reference (the cross-grain aggregation form) is rejected by the broader "no aggregates in field expressions" rule above (`E_AGGREGATE_IN_FIELD`). Express the aggregation as a model-scoped metric (§4.5) — `metrics: [{name: lifetime_value, expression: SUM(orders.amount)}]` — and consume it via an aggregation query.

- **Across an `N : N` edge** in a non-aggregating context: rejected (`E3012_MN_NO_SAFE_REWRITE`, §6.8). Cross-grain aggregation across an `N : N` edge is also handled by the rule above — packaged as a metric and governed by §6.8 the same way it is for any top-level metric.

**Window functions in field expressions.** A field's `expression` MAY include a standard SQL window function (e.g., `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date NULLS LAST)`). The window evaluates at the home dataset's grain; the window's `PARTITION BY` and `ORDER BY` expressions MUST be resolvable at that grain (row-level fields of the home dataset, or `N : 1` enrichments along an unambiguous path). The Foundation's full window-function contract is in §6.10. (A window function is not an aggregate function in the spec sense — it does not raise `E_AGGREGATE_IN_FIELD`.)

**Worked example — aggregation expressed as a model-scoped metric, not a field.** The "sum of each customer's orders" pattern goes under top-level `metrics:`, not on the dataset:

```yaml
datasets:
  - name: customers
    primary_key: [id]
    fields:
      - { name: id,     expression: id }
      - { name: region, expression: region }

  - name: orders
    primary_key: [order_id]
    fields:
      - { name: customer_id, expression: customer_id }
      - { name: amount,      expression: amount }

relationships:
  - { name: orders_to_customer, from: orders, to: customers,
      from_columns: [customer_id], to_columns: [id] }   # N:1

# All metrics — including same-grain aggregates over a single dataset — live in
# the top-level `metrics:` section. Field expressions are non-aggregate.
metrics:
  - name: lifetime_value
    expression: SUM(orders.amount)   # cross-grain — resolves at the query's grain (§4.5)
  - name: total_orders_amount
    expression: SUM(orders.amount)   # could also be the same-grain "totals" measure;
                                     # the dataset prefix is unnecessary because metrics
                                     # are referenced by bare name
```

The query `Dimensions: [customers.region]; Measures: [lifetime_value]` returns one row per region with the sum of order amounts — the standard cross-grain pattern.

If a `SUM(orders.amount)` (or any other aggregate, including the same-grain `SUM(amount)` on the orders dataset itself) were placed under `fields:`, the engine MUST raise `E_AGGREGATE_IN_FIELD`. Boolean cross-grain expressions such as `COUNT(orders.order_id) > 0` are also expressed as metrics and consumed in `Having`; the field-level form is deferred to §10's grain-aware functions or to the deferred §6.8 semi-join filter form.

```yaml
fields:
  - name: order_id
    expression: order_id

  - name: order_date
    expression: o_orderdate
    data_type: DATE                    # temporal semantics come from the SQL type

  - name: discounted_price
    expression: extended_price * (1 - discount)

  - name: full_name
    expression: first_name || ' ' || last_name

  - name: is_completed
    expression: status = 'completed'   # boolean scalar → usable in Where

  - name: order_rank
    expression: ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date NULLS LAST)
                                       # window function at home grain → usable in Where, Fields, etc.
```

The field type is determined by **resolved expression shape**, not by any declared tag. Concretely:

- A **row-level scalar** is a non-aggregate expression over the home dataset's columns or `N : 1` / `1 : 1` enrichments. It is usable in `Dimensions`, `Where`, `Order By`, and `Fields`.
- A **query-grain aggregate** is an aggregate that resolves at the query's grain — either a top-level aggregate in `Measures` (e.g., the `SUM` in `Measures: [SUM(orders.amount)]` for a query grouped by region) or a reference to a model-scoped metric (`Measures: [total_revenue]`). It is usable in `Measures`, `Having`, and `Order By` (when referenced through a measure). Aggregates do not live in field expressions.
- A **boolean row-level scalar** is usable in `Where`; a **boolean query-grain aggregate** is usable in `Having`.

The engine MUST classify each expression by its resolved shape; the user does not need to declare the kind explicitly.

> **Forward note.** The deferred grain proposal (§10) generalises this with explicit grain operations and grain-aware functions, including a field-level form for aggregation that today must be expressed as a model-scoped metric (§4.3.1).

### 4.4 Relationships

Relationships declare how datasets join. The Foundation supports **equijoin relationships only** (single-column or composite). Non-equijoin and temporal joins are deferred to the Non-Equijoin and ASOF proposals (§10).

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique within the model |
| `from` | string | Yes | The many-side dataset (FK side) |
| `to` | string | Yes | The one-side dataset (PK/UK side) |
| `from_columns` | array | Yes | FK columns |
| `to_columns` | array | Yes | PK or UK columns on the `to` side |
| `description` | string | No | |
| `ai_context` | string / object | No | |

Column arrays must have the same length and positionally correspond.

```yaml
relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]

  - name: line_items_to_orders
    from: line_items
    to: orders
    from_columns: [order_id]
    to_columns: [order_id]

  - name: order_lines_to_products
    from: order_lines
    to: products
    from_columns: [product_id, variant_id]
    to_columns: [id, variant_id]
```

Referential-integrity declarations on relationships (e.g., "every `from` row has a matching `to` row") are deferred to a companion proposal (§10). The Foundation always uses the safe defaults of §6.6 — `LEFT` for enrichment, `FULL OUTER` for multi-fact composition — and never silently drops rows because RI was assumed.

### 4.5 Metrics

Metrics are aggregate expressions. **All metrics are model-scoped — defined at the top level in the `metrics:` section and referenced by bare name.** The Foundation has exactly one place a metric can live and exactly one syntax for referencing it. Every aggregate expression is a metric; field expressions never contain aggregates (§4.3).

> **Deferred — dataset-namespaced aggregations.** Adding aggregations at the dataset level is deferred.  This includes: (a) aggregate-bodied fields in a dataset's `fields:` list (e.g., `orders.fields: [{name: total_revenue, expression: SUM(amount)}]`), and (b) per-dataset `metrics:` blocks (e.g., `customers.metrics: [...]`). 

| Field | Type | Required | Description |
|:---|:---|:---|:---|
| `name` | string | Yes | Unique in the global metric namespace |
| `expression` | string \| object | Yes | An aggregate SQL expression in the OSI expression subset (§7). Schema (string form and structured per-dialect form) defined in [`OSI core metadata spec`](https://github.com/open-semantic-interchange/OSI/blob/main/core-spec/spec.md) §"Metrics" and [`SQL expression subset`](https://docs.google.com/document/d/1Jn98kHWsnbvo1MycQBAnWirGTwRmw-DWaen7coNCYWA/edit?usp=sharing). |
| `description` | string | No | |
| `ai_context` | string / object | No | |
| `access_modifier` | enum | No | `public` (default) or `private` |

**Foundation rules for metric expressions.** A metric's expression MUST be one of:

1. **A single aggregation** that resolves at the query's grain (§6.2). The aggregate operates over scalars from any dataset reachable through the relationship graph — same-grain (`SUM(orders.amount)` when consumed at the orders grain), `N : 1` / `1 : 1`-reachable, or `1 : N`-reachable (cross-grain single-step). Examples: `total_revenue = SUM(orders.amount)`, `total_orders = SUM(orders.amount)` (consumed at customer grain), `avg_order = AVG(orders.amount)` (cross-grain via the relationship graph).

   **Cross-grain single-step semantics.** When the aggregate references a higher-grain dataset via a `1 : N` edge, the single-step interpretation is **standard SQL semantics**: the engine joins the higher-grain rows through the relationship path and aggregates them at the query's grain. Each higher-grain row contributes once per output group, so Semantic 2 holds (§6.1). This is the same shape Looker, Tableau, and dbt-semantic-layer produce for cross-grain measures.

   **`M : N` cross-grain references.** Cross-grain aggregates over an `N : N` edge are accepted for every aggregate category (distributive, algebraic, holistic). The contract is set-theoretic: the aggregate's input is the set of unique `(measure-home-row, group-key)` associations reachable through the relationship path; its output is the result of applying the aggregate to that set, once per group. Engines MAY implement this contract by any plan that produces equivalent results; the reference construction is in §6.8.1 (D-026, D-027). For `AVG(movies.gross)` grouped by `actors.height` over `actors ↔ appearances ↔ movies` (the §6.8.1 fixture), the required answer is `170 → AVG(100, 200) = 150`, `180 → AVG(50) = 50`. This is the heavy-side-weighted single-step analogue of the `1 : N` rule above.  The alternative "per-home-row-first" interpretation (e.g., per-actor-first averaging, which would yield `125` for height `170`) is the *nested* form `AVG(AVG(movies.gross))` and is **deferred** to §10's grain-aware-functions proposal (see below).

2. **An arithmetic combination** of already-defined metrics, scalar literals, and aggregated scalar expressions: `m1 / NULLIF(m2, 0) * 100`, `revenue - cost`. Each operand resolves independently at the query's grain per §6.2; the arithmetic is applied after the operand aggregates have resolved. Bare row-level fields finer than the query's grain MUST be inside an aggregate; an unwrapped finer-grain reference MUST raise `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` (D-024). Bare fields at coarser grain than the query's grain (constants, header fields on an `N : 1`-reachable dataset, etc.) attach via §4.3 and are usable directly.

3. **A metric reference** (`total_revenue`) — an alias or rename of an existing metric, resolved at the query's grain per §6.2.

4. **A window-function expression** that resolves at the query's grain — see §6.10 below.

> **Deferred — nested aggregation.** Expressions of the form `OUTER(INNER(<expr>))` where `INNER` is an aggregate (e.g., `AVG(COUNT(orders.oid))`, `AVG(AVG(orders.amount))`) MUST raise `E_NESTED_AGGREGATION_DEFERRED`. The per-home-row-first interpretation that nested aggregation expresses requires an implicit grain pin on the inner aggregate, and the rules for choosing that pin (which dataset, how `Dimensions` participates) are deferred to §10's grain-aware-functions proposal. Practical implications:
>
> - For **distributive** aggregates (`SUM`, `COUNT`, `MIN`, `MAX`), the single-step form gives identical numbers to the nested form, so there is no expressive loss — write `SUM(orders.amount)` instead of `SUM(SUM(orders.amount))`.
> - For **non-distributive** aggregates (`AVG`, `STDDEV`, `VARIANCE`, `MEDIAN`, `PERCENTILE`) over a `1 : N` edge, only the heavy-side-weighted single-step interpretation is available today (`AVG(orders.amount)` is the average over every order, not the average of per-customer averages). The unweighted "average of per-home-row averages" interpretation waits for §10.
> - For **non-distributive** aggregates over an `N : N` edge, the bare single-step form is **accepted** under the bridge-de-duplication construction of §6.8.1 (the analogue of the heavy-side-weighted `1 : N` rule above). Only the per-home-row-first interpretation — written as the nested form `AVG(AVG(…))` — waits for §10. `AVG(movies.gross) by actors.height` ⇒ accepted (bridge-dedup AVG); `AVG(AVG(movies.gross)) by actors.height` ⇒ `E_NESTED_AGGREGATION_DEFERRED`.

**Filter and grain context.** Metric-references-metric (forms 2 and 3) introduce **no per-metric grain override** and **no per-metric filter override**. Every referenced operand resolves at the query's grain per §6.2 and is filtered by the query's `Where` clause like any other aggregate in the projection — that is standard SQL behaviour, not a Foundation extension. Per-metric grain controls (Tableau `FIXED` / `INCLUDE` / `EXCLUDE`) and per-metric filter overrides (Tableau `FIXED` filters, Power BI `CALCULATE`, dbt-semantic-layer metric filters) — which let an individual metric ignore or replace the surrounding query context — are deferred to §10.

**Window functions in metric expressions.** A metric's `expression` MAY contain a standard SQL window function (e.g., `running_total = SUM(amount) OVER (ORDER BY order_date NULLS LAST)`). The window evaluates at the query's grain over the post-`GROUP BY` row set. The Foundation's full window-function contract is in §6.10. One limitation: a metric expression that itself **references** another metric whose expression contains a window function is deferred (§10, §6.10.5) — direct use of a windowed metric in `Measures` works today, but composing windowed metrics through forms (2) and (3) does not.

These rules produce numerical results equivalent to Looker, Tableau, and dbt-semantic-layer's cross-grain handling for 1:N reaches with single-step aggregation. Snowflake Semantic Views require explicit nested form for cross-grain aggregates; with nested aggregation deferred in the Foundation, only the single-step form is available — see §12.A for the divergence note.

```yaml
datasets:
  - name: orders
    fields:
      - { name: order_id,    expression: order_id }
      - { name: customer_id, expression: customer_id }
      - { name: amount,      expression: amount }
      - { name: discount,    expression: discount }
    # No `metrics:` block on a dataset — that form is deferred (see §4.5).

# All metrics live here, referenced by bare name.
metrics:
  # Same-grain aggregate over a single dataset.
  - name: total_revenue
    expression: SUM(orders.amount)

  - name: order_count
    expression: COUNT(orders.order_id)

  - name: distinct_customers
    expression: COUNT(DISTINCT orders.customer_id)

  # Derived metric — arithmetic of other metrics.
  - name: avg_order_value
    expression: total_revenue / NULLIF(order_count, 0)

  # Multi-column aggregate over a single dataset's own columns.
  - name: net_revenue
    expression: SUM(orders.amount * (1 - orders.discount))

  # Cross-grain single-step (form 1) — standard SQL semantics.
  # Each order row contributes once per output group; SUM is distributive.
  # When consumed at customer.region grain, this is the per-region revenue total.
  - name: customer_revenue
    expression: SUM(orders.amount)

  # Cross-grain single-step AVG — also accepted; standard SQL semantics.
  # Grouped by region, this is the average over every order whose customer
  # is in that region — a heavy-customer-weighted average. The unweighted
  # "average of per-customer averages" form is deferred (see §4.5 deferral above).
  - name: avg_order_amount_across_customers
    expression: AVG(orders.amount)
```

### 4.6 Namespacing and Identifiers

This section is normative for the Foundation surface. The identifier *grammar* (case-folding, normalised-identifier definition, the `Field` / `FieldExpr` shape, the global / dataset / physical scope model, and the in-dataset precedence table) lives in [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md) §"Namespacing and Identifier Resolution"; this section adds the Foundation-specific pieces that document doesn't — and shouldn't — cover: a definitive position on quoted-identifier case-sensitivity, the Foundation's reserved-name set, the error codes, and the reference rules at every site a name appears in a semantic query. Where the two documents overlap they MUST agree; where they differ this section is normative for the Foundation.

#### 4.6.1 Identifier Form

The grammar lives in [OSI SQL Expression Subset](https://docs.google.com/document/d/1Jn98kHWsnbvo1MycQBAnWirGTwRmw-DWaen7coNCYWA/edit?usp=sharing) §"Namespacing and Identifier Resolution": ANSI SQL identifiers, ≤128 characters, regular (unquoted) identifiers are case-insensitive and fold to upper-case, quoted identifiers preserve their inner text verbatim, and the **normalised identifier** is the canonical form used for matching (regulars upper-cased; quoted have quotes stripped and escapes unescaped). Two identifiers are equal iff their normalised forms are byte-equal.

The Foundation pins one point on which OSI SQL Expression Subset hedges: **quoted identifiers are case-sensitive relative to regular identifiers**. So `"id"` does not match the regular identifier `id` (the regular form normalises to `ID` and the quoted form preserves `id`), but `"ID"` does. The conformance suite (§11.1) asserts this position.

#### 4.6.2 Reserved Names

The Foundation reserves the following names. Engines MUST reject user-defined identifiers that collide with them:

| Reserved | Meaning |
|:---|:---|
| `GRAIN` | Reserved for the deferred grain extension (§10). |
| `FILTER` | Reserved for the deferred filter-context extension (§10); also reserved by SQL window-function syntax. |
| `QUERY_FILTER` | Reserved for the deferred filter-context extension (§10). |

ANSI SQL reserved words are also reserved at the identifier level — engines MAY accept them only when quoted.

#### 4.6.3 Scopes

The three-scope model (Global / Dataset / Physical) and the in-dataset precedence table (physical → logical-on-same-dataset → global) are defined in [OSI SQL Expression Subset](https://docs.google.com/document/d/1Jn98kHWsnbvo1MycQBAnWirGTwRmw-DWaen7coNCYWA/edit?usp=sharing) §"Name Spaces". The Foundation adds the following:

**Global scope — Foundation-specific membership and rules.** In the Foundation, the only global names are **datasets**, **relationships**, and **model-scoped metrics**. All global names share one namespace: a dataset and a relationship cannot have the same normalised name; a metric and a relationship cannot have the same normalised name; etc. Engines MUST reject duplicate global names with `E_NAME_COLLISION`. A dataset-scoped field MAY share a normalised name with a global name — they live in different scopes; the in-dataset precedence table decides which one a bare reference picks up.

**Reach restrictions.** Global metric expressions can reference any other global name and any dataset-scoped name (qualified as `dataset.field`), but cannot reach physical columns directly — physical columns are reachable only from inside the dataset that owns them. Relationships MAY reference physical columns on either endpoint (per the OSI SQL Expression Subset).

**Practical consequence of the precedence table.** A dataset that declares both a physical column `id` and a logical field `id` resolves bare `id` to the physical column; the qualified form `<dataset>.id` reaches the logical field.

#### 4.6.4 Reference Syntax

Three rules cover every reference site:

1. **Inside a query** (`Dimensions`, `Measures`, `Where`, `Having`, `Order By`, `Fields`): metrics are referenced by **bare name** from the global metric namespace. Dataset-scoped fields MUST be referenced as `dataset.field`.

   Example: in `Measures: [total_revenue]`, the engine resolves `total_revenue` to the model-scoped metric of that name. If no metric `total_revenue` exists, the engine raises `E_NAME_NOT_FOUND`. In `Dimensions: [orders.region]`, `orders.region` resolves to the field `region` on dataset `orders`.

   The Foundation has no `dataset.metric_name` form (dataset-namespaced metrics are deferred per §4.5). Every metric lives in one global namespace, so name collisions across the model surface as ordinary `E_NAME_COLLISION` at validation time, not as scope-resolution ambiguities at query time.

2. **Inside a dataset field expression**: bare names follow the precedence table in the OSI SQL Expression Subset §"Name Spaces" (physical → logical → global). A dataset-local logical field that shadows a global name is reachable via the `dataset.field` form.

3. **Inside a model-scoped metric expression**: names MUST be `dataset.field`-qualified for any reference to a dataset-scoped field (model-scoped metrics live in the global scope and have no implicit home dataset). Bare references to other global names (e.g., another model-scoped metric) are allowed.

---

## 5. Query Model

### 5.1 Semantic Query Clauses

A semantic query is one of two distinct shapes. Every Foundation query MUST be classifiable as exactly one of them. The shape is determined by which projection clause the query uses.

| Shape | Projection clauses | Result grain | Maps to standard SQL… |
|:---|:---|:---|:---|
| **Aggregation query** | `Dimensions` and/or `Measures` | One row per distinct tuple of `Dimensions`. Empty `Dimensions` ⇒ exactly one row (the empty grain). | …with a `GROUP BY` (or no `GROUP BY` and only aggregates in the SELECT list). |
| **Scalar query** | `Fields` | Table grain — one row per row in the (joined) source row set. No aggregation. | …with no `GROUP BY` and no aggregates in the SELECT list. |

Mixing `Fields` with `Dimensions` or `Measures` in the same query is rejected (`E_MIXED_QUERY_SHAPE`). The two shapes have different correctness rules and different SQL contracts; the engine must know which one it is compiling before it plans.

##### Common clause semantics

The following rules apply to both query shapes. Per-shape clauses below extend them.

**`Where` and `Having` predicate lists.** Both clauses accept either a single predicate expression or a list of predicate expressions. A list MUST be interpreted as the conjunction (`AND`) of its entries. `Where: [P1, P2]` is identical in meaning to `Where: "P1 AND P2"`. Engines MAY support either surface form; conformant emitted SQL is the `AND`-joined form. There is no Foundation-level `OR` shortcut between list entries — `OR` must be written inside an expression.

**`Order By` NULL placement.** Every `Order By` entry — both the outer query's `Order By` and any `ORDER BY` inside an `OVER (...)` window clause — has a defined NULL placement. If the entry does not specify `NULLS FIRST` or `NULLS LAST` explicitly, the Foundation default is **`ASC ⇒ NULLS LAST`, `DESC ⇒ NULLS FIRST`** — i.e., NULL is treated as a *high-end* value that lands at whichever end of the sort the maximum lands at. The compiled SQL MUST guarantee this *resolved row order* on every supported dialect; engines achieve this by emitting the explicit `NULLS …` clause whenever the dialect's native default would otherwise produce a different order. (When the resolved clause already matches the dialect's native default — e.g. `DESC NULLS FIRST` on Snowflake, `ASC NULLS LAST` on DuckDB — the explicit clause MAY be elided from the compiled SQL, since elision and explicit emission produce identical row orders on that dialect. The byte-identical-output guarantee in D-014 is per `(model, query, dialect)` — *within* a dialect the compiled SQL is deterministic; *across* dialects the row order is identical even when the literal SQL text differs by an elidable `NULLS …` token.) The Foundation chooses the high-end-NULL convention because (a) it matches SQL:2003's "NULLs compare-greater than non-NULLs" default, and the out-of-the-box defaults of Snowflake, PostgreSQL, and Oracle; (b) it preserves the **symmetry property** that flipping `ASC ↔ DESC` also flips the NULL placement, which is the behaviour every common BI mental model assumes (e.g. "top 10 by revenue → flip to bottom 10" should bring the NULL-revenue rows to the top, since they *are* the worst values by any reasonable interpretation). A user who wants every NULL pinned to a specific end regardless of direction MUST write the explicit `NULLS FIRST` / `NULLS LAST` clause; the compiled SQL then carries that explicit clause on every dialect (it cannot be elided because it overrides the resolved default).

**`Order By` entry shape.** An `Order By` entry references either (a) a name that is in scope for the query's projection — a dimension name, a measure name (for an aggregation query), or a field name (for a scalar query) — or (b) any expression that would have been valid in the projection. Positional references (`ORDER BY 1`) are not part of the Foundation surface. Engines that compile to SQL MAY use positional references in emitted SQL if they preserve determinism.

**`Limit` without `Order By`.** A `Limit` without an `Order By` MUST be accepted and compiled — same as standard SQL. The resulting row set is engine-defined (which rows are kept is up to the engine and underlying storage), but the emitted SQL itself MUST be deterministic per D-014 — the same `(model, query, dialect)` produces byte-identical SQL on every compilation. Engines MAY emit a diagnostic for users who appear to want determinism, but are NOT required to. Users who need a stable row set MUST supply an `Order By` whose tuple is unique within the result.

**Result-column naming.** There is no cross-vendor convention for naming the result column produced by a `dataset.field` reference (Snowflake renders the full path expression; Databricks renders `parent.field`; Postgres/BigQuery render the leaf name only). The Foundation therefore does **not** mandate a particular result-column-naming scheme — engines MAY emit the leaf name (`region` for `orders.region`), the qualified name, the full path, or a vendor-specific form, as long as it is deterministic for the same `(model, query, dialect)`. Metrics, which are referenced by bare name, do not have this ambiguity (the result column is the metric's name unless the user supplies an explicit alias). Users who need a stable, portable result-column name for a `dataset.field` reference MUST use an explicit alias (e.g., `Dimensions: [orders.region AS order_region]`). A future SQL-interface proposal (§9) is expected to settle this.

#### 5.1.1 Aggregation Query

| Clause | Purpose | Required |
|:---|:---|:---|
| **Dimensions** | Fields used for grouping. Become the query's `GROUP BY`. | No |
| **Measures** | Metrics, ad-hoc aggregations, or window expressions to compute. Window functions (§6.10) evaluate over the post-`GROUP BY` row set. | No |
| **Where** | Pre-aggregation filter; predicates with no aggregate and no window (§6.3, §6.10.1). | No |
| **Having** | Post-aggregation filter; predicates whose top-level boolean references at least one aggregate or window (§6.3). | No |
| **Order By** | List of `{field-or-measure-or-window-expression, direction}` pairs. | No |
| **Limit** | Row limit on the result set. | No |

At least one of `Dimensions` or `Measures` MUST be non-empty.

**Result grain:** the distinct tuple of `Dimensions`. Empty `Dimensions` collapse to the **empty grain** — exactly one row containing the fully-aggregated measures.

**Example semantic query:**

```yaml
query:
  dimensions: [customers.market_segment, orders.order_year]
  measures:   [total_revenue, order_count]
  where:      "orders.status = 'completed' AND customers.region = 'WEST'"
  having:     "total_revenue > 1000"
  order_by:   [{field: total_revenue, direction: DESC}]
  limit:      50
```

**Standard SQL it corresponds to (any aggregation SELECT with a `GROUP BY` is an aggregation query):**

```sql
SELECT customers.market_segment,
       orders.order_year,
       SUM(orders.amount) AS total_revenue,
       COUNT(orders.order_id) AS order_count
FROM   orders
JOIN   customers ON orders.customer_id = customers.id
WHERE  orders.status = 'completed' AND customers.region = 'WEST'
GROUP BY customers.market_segment, orders.order_year
HAVING SUM(orders.amount) > 1000
ORDER BY total_revenue DESC
LIMIT 50
```

A vendor-specific SQL surface (a `SEMANTIC_VIEW(...)` clause, a `FROM <semantic_view>` syntax, a SQL-runner over the model, etc.) MAY render the same semantic query differently. The Foundation does not mandate a particular SQL surface; §12 catalogs how existing vendor surfaces compare.

#### 5.1.2 Scalar Query

A scalar query asks for **table-grain rows** — one row per row in the home dataset, with no aggregation step at the query level.

| Clause | Purpose | Required |
|:---|:---|:---|
| **Fields** | The columns to project. Each entry MUST be a scalar at the **home dataset's grain** — either a row-level field on the home dataset, an enrichment along an `N : 1` path (§4.3), or a window function evaluated at the home grain (§6.10). | Yes |
| **Where** | Row-level filter; predicates with no aggregate and no window (§6.3, §6.10.1). Compiles to SQL `WHERE`. | No |
| **Order By** | List of `{field-or-window-expression, direction}` pairs. | No |
| **Limit** | Row limit on the result set. | No |

**Constraints:**

- `Fields` MUST contain at least one entry.
- No `Measures`, no `Dimensions`, no `Having`. Group-level predicates have no meaning at table grain.
- A **bare metric reference** in `Fields` (e.g., `revenue`) is rejected with `E_AGGREGATE_IN_SCALAR_QUERY`. A metric is an aggregation, not a per-home-row scalar; the user likely wants an aggregation query (§5.1.1). The error message MUST suggest converting to an aggregation query.
- A field whose expression contains **any aggregate** (same-grain over the home dataset's own columns or cross-grain via a `1 : N` reach) is rejected at *model-validation* time with `E_AGGREGATE_IN_FIELD` (§4.3). All aggregates are model-scoped metrics (§4.5) and consumed via an aggregation query (§5.1.1); the field-level form is deferred to §10.
- Cross-dataset references in `Fields` follow §4.3: same/lower-grain references attach directly along an unambiguous `N : 1` / `1 : 1` path. Higher-grain references are not allowed in `Fields` at all (an unwrapped finer-grain reference raises `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`, D-024; an aggregate-wrapped reference inside a field expression raises `E_AGGREGATE_IN_FIELD`, §4.3).

**Result grain:** the **home dataset's table grain** — the row set produced by taking the home dataset's rows and enriching along `N : 1` paths needed to resolve `Fields`. The home dataset is the dataset whose row-level (non-aggregated) fields drive the projection; if `Fields` references several datasets at the same grain (linked by `1 : 1` edges), the engine treats them as one logical home. The result is one row per surviving home-dataset row, after `Where` and `Limit` are applied.

**Multiple incompatible homes.** If `Fields` supplies row-level (non-aggregated) references from two or more datasets that are **not** linked by declared `1 : 1` edges — for example `Fields: [orders.amount, returns.amount, customers.region]`, with `orders` and `returns` as independent facts on the many-side of `customers` — there is no single home grain at which the scalar shape can produce one row per home-dataset row without replicating the other root's rows. The engine MUST raise `E_FAN_OUT_IN_SCALAR_QUERY` (D-023, extended). The diagnostic MUST identify the conflicting home datasets and suggest either converting to an aggregation query or choosing a single fact as the home. (A semi-join filter form is deferred — see §6.8.) This matches the row-level-projection semantics of every major BI tool: Tableau, Power BI matrices, and Looker explores all anchor row-level views on a single base table; OSI follows the same convention.

**Example semantic query — pure row-level projection:**

```yaml
query:
  fields:   [orders.order_id, orders.amount, customers.market_segment]
  where:    "orders.status = 'completed'"
  order_by: [{field: orders.amount, direction: DESC}]
  limit:    100
```

**Standard SQL it corresponds to (any non-aggregating SELECT is a scalar query):**

```sql
SELECT orders.order_id,
       orders.amount,
       customers.market_segment
FROM   orders
LEFT JOIN customers ON orders.customer_id = customers.id
WHERE  orders.status = 'completed'
ORDER BY orders.amount DESC
LIMIT 100
```

**Cross-grain aggregation: use an aggregation query, not a scalar query.** A pattern such as "list each customer's region and lifetime value" is *not* a scalar query in the Foundation. Define the aggregation as a metric (§4.5 form 1) and consume it via an aggregation query (§5.1.1):

```yaml
# Model
metrics:
  - name: lifetime_value
    expression: SUM(orders.amount)   # higher-grain reference inside a metric — allowed

# Query (aggregation query, not scalar)
query:
  dimensions: [customers.region, customers.id]
  measures:   [lifetime_value]
  order_by:   [{field: lifetime_value, direction: DESC}]
  limit:      20
```

This returns one row per `(region, customer)` pair with the customer's lifetime value. Aggregate-bodied fields (any aggregate in a field expression, including the cross-grain pattern packaged as a field on `customers`) are deferred to §10's grain-aware-functions proposal — see §4.3.

The mapping rule for SQL surfaces: **a SQL `SELECT` with `GROUP BY` (or with query-level aggregates in the projection) is an aggregation query; a SQL `SELECT` with neither is a scalar query.** A user porting an existing SQL query to a semantic query keeps the same shape on each side.

Implementations MAY provide any surface syntax — JSON, SQL subclause, programmatic builder — as long as the semantic clauses above are expressible.

There is currently **no authoritative SQL surface** for OSI; this is an area where a portable surface is still emerging. Multiple vendors offer SQL surfaces over their semantic layers, and a common convention is to map the presence of `GROUP BY` (or aggregates in the projection) to the aggregation-query shape and the absence of both to the scalar-query shape — which is exactly the rule above. A Foundation-compliant SQL surface that follows this convention will keep user intent stable across implementations.

## 6. Semantics

This is the heart of the Foundation. The goal is that two OSI-compliant engines running the same query on the same data always get the same result.

### 6.1 User-Visible Semantics

When a query spans multiple datasets, OSI's engine chooses the join shape for you using a small set of safety-first defaults. The rules you can rely on are the five **user-visible semantics** below. Every concrete rule in the rest of §6 — cardinality inference, default join types, trap avoidance, M:N resolution, path resolution — exists to make these five guarantees hold.

#### Semantic 1 — No fact row is silently dropped

If you query a fact and pull in dimension columns, **every fact row is represented in the result**. After grouping, fact rows whose dimension key doesn't match anything are aggregated into a `NULL` bucket on those dimensions — they do not silently disappear from totals.

A broken foreign key surfaces as a `NULL` bucket in the result, never as silently-missing rows. To opt into the alternative ("only orders with a known customer"), add `WHERE <dim_field> IS NOT NULL`.

Opt-in `INNER`-promotion via declared referential integrity is deferred to a later proposal (§10).

#### Semantic 2 — No row is double-counted by fan-out

**No row of dataset `A` contributes more than once to a measure defined on `A`**, regardless of what tables you join in for grouping or filtering. A customer with 5 orders is counted once in `COUNT(customers.id)`, not five times — even if the query also groups by an order-level dimension.

**A row of dataset `A` MAY contribute to more than one group** when the relationship between `A` and the grouping dimension is many-to-many (e.g., a bridge table). In that case, the engine MUST either:

- Ensure that each row of `A` contributes to each group at most once (the bridge / stitch resolutions of §6.8), or
- Fail the query with an error rather than silently inflating the totals.

This behaviour means that summing per-group totals over an M:N edge MAY give a number different from a total computed directly over the base table — that is fan-out *by design*, not double-counting.

#### Semantic 3 — In multi-fact queries, no fact loses its groups

When you put measures from two different facts in the same query (e.g., `revenue` from `orders` and `returns` from `returns`, both grouped by `customer.region`), **the result contains every group that appears in either fact**. A region with revenue but no returns appears with `returns = NULL` (or `0` if the metric coalesces). A region with returns but no orders also appears.

Pulling a second fact into your query never *removes* groups that were in your first fact's answer.

#### Semantic 4 — No unsafe re-aggregations

Aggregations fall into three main categories: distributive, algebraic, and holistic.

- **Distributive** functions like `SUM` can be re-aggregated with the same function (`SUM` of partial `SUM`s) and the distributive property guarantees the correct result.
- **Algebraic** functions like `AVG` can be broken into multi-step aggregations, but the intermediate step needs a different operation than the final one. For `AVG`, the engine tracks `SUM` and `COUNT` separately so the final division uses the right total. Taking the `AVG` of an `AVG` directly is unsafe — it overweights smaller populations.
- **Holistic** functions like percentile and `COUNT(DISTINCT)` need the entire population to compute the final answer and cannot be decomposed safely.

Implementations MUST NOT silently re-aggregate using an unsafe path. When the chosen plan forces multi-stage decomposition that the aggregate cannot survive — typically a holistic or unsupported-algebraic aggregate over a §6.7 chasm pre-aggregation or a §6.8.2 stitch — the engine MUST raise `E_UNSAFE_REAGGREGATION` and identify the aggregate and the grains involved. The §6.8.1 bridge plan does not force decomposition (it is a single-pass aggregate over the de-duplicated row set) and so is not in scope for this rule; every aggregate category resolves through it per D-027.

#### Semantic 5 — No silently wrong answer

If the engine cannot find a safe way to compute your query, it raises a typed error with a code, not a plausible-but-wrong number. Different engines may handle some M:N edge cases differently — one engine's safe rewrite is another's typed error — but neither is allowed to produce silently-inflated output.

The error codes you'll hit in practice:

| When | Error code |
|:---|:---|
| Two facts joined through an `N : N` relationship with no safe rewrite at the query's grain | `E3012_MN_NO_SAFE_REWRITE` |
| Two unrelated facts referenced together with no shared dimension | `E3013_NO_STITCHING_DIMENSION` |
| Multiple equally-valid join paths exist | `E_AMBIGUOUS_PATH` |
| No relationship path connects the referenced datasets | `E_NO_PATH` |
| The chosen plan forces multi-stage decomposition the aggregate cannot survive (holistic over chasm pre-agg or stitch) | `E_UNSAFE_REAGGREGATION` |
| A scalar-query join path replicates home-dataset rows | `E_FAN_OUT_IN_SCALAR_QUERY` |
| A row-level reference to a field at a grain finer than the consuming home grain | `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` |

### 6.2 Evaluation Semantics

The evaluation depends on the query shape (§5.1) — whether it aggregates or not. The concepts below — starting grain, final grain, and the dataset set — are common to both shapes.

#### Starting Grain

The starting grain defines the pre-aggregation row set that the operation begins from. For an aggregation query, **each metric resolves its own starting grain independently** from the datasets its expression touches and the join path the engine resolves. Two metrics in the same query MAY have different starting grains; the engine combines them under the §6.1 semantics and the M:N rules in §6.8.

For a given metric, there may be multiple starting grains when shared dimensions or many-to-many joins are present. In most cases the engine can aggregate in a way that satisfies the §6.1 semantics — for example, by pre-aggregating fan-out-prone joins (§6.7) or by walking through a bridge dataset (§6.8.1). When it cannot, it MUST fail with a typed error rather than producing a silently-wrong number. The relevant codes, narrowest-first:

| Code | Condition |
|:---|:---|
| `E3012_MN_NO_SAFE_REWRITE` | The composition crosses an `N : N` edge with no available bridge or shared-dimension stitch (§6.8). (Semi-join filter form deferred — see §6.8 note.) |
| `E3013_NO_STITCHING_DIMENSION` | Two unrelated facts referenced in the same measure with no shared dimension and no relationship path. |
| `E_UNSAFE_REAGGREGATION` | The chosen plan **forces a multi-stage decomposition** that the aggregate cannot survive. The two Foundation shapes that force decomposition are §6.7 chasm pre-aggregation (multiple incompatible 1:N reaches that must be aggregated independently before being merged) and §6.8.2 stitch (independent per-fact aggregation under a shared dimension followed by a FULL OUTER merge, then re-aggregation at the query grain). A holistic aggregate (`MEDIAN`, `PERCENTILE_CONT`) cannot be decomposed across these stages; an algebraic aggregate (`AVG`, `STDDEV`) survives them only if the engine implements its multi-stage decomposition. The §6.8.1 bridge plan does **not** force decomposition — it is a single-pass aggregate over the de-duplicated `(measure-home-row, group-key)` set, so every aggregate category resolves there per D-027. The diagnostic MUST name the aggregate and the grains involved, and SHOULD suggest the safe rewrite (pre-aggregate at the home grain, switch to a distributive aggregate, or restate at a coarser grain). See §7 and `SQL_EXPRESSION_SUBSET.md` for the distributive / algebraic / holistic categories. |
| `E_AMBIGUOUS_MEASURE_GRAIN` | Catch-all: a single measure has multiple incompatible starting grains and none of the more-specific codes above applies. Foundation engines SHOULD reach for the more-specific codes first; this code is reserved for shapes the spec does not yet enumerate. The diagnostic MUST list the starting grains the engine identified. |

To find the starting grain for a metric, the engine:

1. **Finds all datasets** touched by any dimension, the measure being calculated, `Where` predicate, or `Having` predicate.
2. **Resolves a join path** — a connected sub-graph of the declared relationships that spans those datasets. If multiple paths exist, see §6.9.
3. **Follows the `1`-side of joins to find the finest-grain dataset** in the path. If multiple incomparable finest-grain datasets exist (shared dim, M:N), each is treated as an independent starting point and the engine MUST combine them via §6.7 (pre-aggregation), §6.8.1 (bridge), or §6.8.2 (stitch), failing with one of the codes above if no safe combination exists.

If a finer-grained referenced field is **not wrapped in an aggregate** (i.e., the user is projecting a row-level value at a grain finer than the consuming home grain), the query MUST fail with `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`. The remedy is to wrap the reference in an aggregate (`SUM`, `COUNT`, etc.) or pull the value from a coarser-grain related dataset where it is already at the home grain.

> **Out of scope: model-level `natural_grain` declaration.** A future proposal will define an optional top-level `natural_grain:` key that pins one dataset as the implicit anchor for every query against the model. The Foundation does NOT adopt that feature yet. The behaviour described above (each metric resolves its own starting grain) is what the Foundation guarantees.

#### Final Grain

The final grain of a query is what defines a unique row in the result.

In an aggregation query, the final grain is the grain implied by the dimensions. If there are no dimensions, the final grain is the empty set — a total aggregation to a single row.

In a scalar query, the final grain MUST be the same as the starting grain (there is no aggregation step between them). If that is not possible — because the join path the query forces through includes an `N : N` edge or any other join that replicates the home dataset's rows — the query MUST fail with `E_FAN_OUT_IN_SCALAR_QUERY`. (The aggregation-query shape handles the same condition non-fatally by pre-aggregating on the many-side per §6.7; only the scalar shape lacks an aggregation step in which to absorb the fan-out, so the same condition is fatal there.)

#### Determining Datasets Involved In a Query

The datasets involved in a query are:

1. Every dataset directly referenced by `Dimensions`, `Measures`, `Where`, `Having`, `Order By`, or `Fields`, **plus**
2. Every **intermediate dataset** required to resolve a connecting join path between the directly-referenced datasets via the relationships graph (§6.9).

A query for `Dimensions: [region.name]; Measures: [SUM(orders.amount)]` over a model with `orders → customers → region` directly references `orders` and `region` but implicitly involves `customers` because it lies on the unique join path. Dataset (2) is just as much "involved in the query" as dataset (1) — `Where` predicates apply to it, cardinality safety checks apply to it, and the planner is free to read its rows.

This has a user-visible consequence for dimension-only queries: a query with `Dimensions: [customers.region]` and no measures reads `customers` only and returns every region present in `customers`, including regions with no orders, no returns, and no activity in any other fact. The Foundation does NOT silently restrict dimension domains to "values used by some fact"; if a model wants that behaviour it must rely on the (deferred) `natural_grain` proposal referenced above.

#### Normative Evaluation Algorithm

This subsection describes the **semantic evaluation procedure** the Foundation requires. It is normative on observable behaviour (which rows appear, which error codes fire, in what order checks are applied) and intentionally silent on physical implementation (CTE structure, join order, materialization strategy). Two engines that produce different SQL but the same row sets and error codes for the same `(model, query)` are both compliant.

The algorithm is written as if every clause is fully populated; engines short-circuit unused clauses.

**A. Aggregation query (`Dimensions` and/or `Measures`).**

1. **Classify the query shape.** If both `Fields` and (`Dimensions` ∪ `Measures`) are non-empty, raise `E_MIXED_QUERY_SHAPE` and stop. If `Dimensions` and `Measures` are both empty, raise `E_EMPTY_AGGREGATION_QUERY` (an aggregation query must have at least one of them).
2. **Resolve identifiers.** For every name in `Dimensions`, `Measures`, `Where`, `Having`, `Order By`, apply the resolution rules of §4.6. Names that fail to resolve raise `E_NAME_NOT_FOUND`. Duplicate global names raise `E_NAME_COLLISION`. Reserved-name collisions raise `E_DEFERRED_KEY_REJECTED`.
3. **Classify expression shapes** per §4.3 / D-005. For each predicate in `Where` / `Having`, compute its *resolved shape* (row-level scalar, query-grain aggregate, or boolean form of each) and verify that it is in the legal set for its clause:
   - `Where` accepts row-level scalars; a window function in `Where` raises `E_WINDOW_IN_WHERE`; a query-grain aggregate in `Where` raises `E_AGGREGATE_IN_WHERE`.
   - `Having` accepts query-grain aggregates and grouping-column references from `Dimensions` (§6.3); a pure row-level predicate in `Having` raises `E_NON_AGGREGATE_IN_HAVING`.
   - A boolean expression that mixes terms at different resolved levels (e.g. `amount > 100 AND SUM(amount) > 1000`) raises `E_MIXED_PREDICATE_LEVEL`.
4. **Identify the final grain.** The final grain is the distinct tuple of `Dimensions`; empty `Dimensions` ⇒ the empty grain (one row).
5. **Identify the dataset-set.** Collect every dataset referenced by `Dimensions`, by any measure in `Measures`, by `Where`, by `Having`, and by `Order By`. Add any intermediate datasets required to connect them via declared relationships (§6.9).
6. **For each measure, independently:**
   1. Collect the measure's *measure dataset-set* — every dataset referenced by the measure's expression, plus the dimension datasets needed to project the result at the final grain, plus the `Where`-referenced datasets (a `Where` predicate is always pre-aggregation and affects every measure).
   2. Resolve a join path through the relationships graph that spans the measure dataset-set (§6.9). Multiple equally-valid paths ⇒ `E_AMBIGUOUS_PATH`. No path ⇒ `E_NO_PATH`. The path MUST be acyclic.
   3. Determine the measure's *starting grain*: walk the path along the `1`-side of every `N : 1` / `1 : 1` edge and find the finest-grain dataset (or datasets) reachable. If the path crosses an `N : N` edge, identify the M:N resolution required (§6.8: bridge, stitch, or filter).
   4. Verify Semantic 2 / D-026: each row of the measure's home dataset MUST contribute to each final-grain group at most once. When this is violated by a naive flat join, emit a §6.7 pre-aggregation, a §6.8.1 bridge resolution, or a §6.8.2 stitch — whichever the model supports.
   5. Verify decomposability (Semantic 4 / D-022): the aggregate's category (distributive / algebraic / holistic, per `SQL_EXPRESSION_SUBSET.md`) MUST be compatible with the chosen plan. A holistic aggregate cannot run over pre-aggregated home-grain rows; if the plan requires it, raise `E_UNSAFE_REAGGREGATION` identifying the aggregate and the grains involved. (Note: a single-step holistic aggregate over a plain `1 : N` edge is not caught — it is one SQL aggregate over the joined rows, well-defined per D-020. The §6.8.1 bridge plan is also not caught — it is a single-pass aggregate over the de-duplicated `(measure-home-row, group-key)` row set, well-defined per D-027 for every aggregate category. The error applies only to plans that genuinely force decomposition — typically §6.7 chasm pre-aggregation or §6.8.2 stitch.)
   6. If none of the §6.7 / §6.8 strategies satisfies Semantic 2 at the measure's starting grain:
      - M:N traversal with no safe rewrite ⇒ `E3012_MN_NO_SAFE_REWRITE`.
      - Disconnected facts referenced together ⇒ `E3013_NO_STITCHING_DIMENSION`.
      - Multiple incompatible starting grains that none of the above fits ⇒ `E_AMBIGUOUS_MEASURE_GRAIN` (D-025).
7. **Compose measures at the final grain.**
   - **Single-measure queries.** No composition step. The result is exactly the row set produced for the one measure in step 6 — typically one row per distinct dimension tuple reachable by the measure's join path, plus a `NULL`-key row for any unmatched fact rows on the many-side (§6.6 row 1, LEFT default). A dimension value that exists in the dim domain but is *not* reached by any surviving fact row does **not** appear. This matches raw SQL `... FROM fact LEFT JOIN dim ... GROUP BY dim.X`.
   - **Multi-measure queries (two or more measures).** Stitch the independently-resolved measure row sets via `FULL OUTER` on the shared dimensions (§6.6 row 3 / D-004; Semantic 3 — neither side loses groups). This is the only shape that correctly composes two independently-aggregated facts: an `INNER` or single-direction `LEFT` would silently drop groups present only in one branch. The mental contrast: single-measure queries follow the fact's natural join shape (Plan A); multi-measure stitch is the *only* shape that explicitly preserves both sides — and it has to, because there is no other way to merge two independently-aggregated facts.
   - When `Dimensions` is empty, the stitch degenerates to `CROSS JOIN` of scalar grand totals (§6.6, §6.8.2 worked example).
8. **Apply `Where` pre-aggregation** to every measure's row set (this happened logically before step 6, but is observable here as "every measure's input is post-`Where`").
9. **Aggregate to the final grain** per measure. Apply `Having` post-aggregation.
10. **Evaluate windows** in `Measures`, `Order By`, and `Having` over the post-`Having` row set, with NULL placement defaulted per §5.1 / D-029. Windows whose home dataset would be fanned out by the plan raise `E_WINDOW_OVER_FANOUT_REWRITE` (D-030) unless the engine materialised the home grain before applying the window.
11. **Apply `Order By`** with the resolved NULL placement; then **`Limit`**.

**B. Scalar query (`Fields`).**

1. **Classify the query shape.** Mixed-shape: `E_MIXED_QUERY_SHAPE`. Empty `Fields`: `E_EMPTY_SCALAR_QUERY`.
2. **Resolve identifiers** (same as A.2).
3. **Reject query-grain aggregates.** A bare metric reference inside `Fields` (a model-scoped metric — the only kind in the Foundation) raises `E_AGGREGATE_IN_SCALAR_QUERY`. A `Fields` entry that is itself a query-grain aggregate (e.g., `SUM(orders.amount)` written inline) raises the same code. A `Fields` entry that references a field whose expression contains any aggregate is rejected at model-validation time with `E_AGGREGATE_IN_FIELD` (§4.3).
4. **Identify the home dataset(s).** Collect every dataset whose row-level (non-aggregated) fields drive any `Fields` entry. Two cases:
   - **Single home grain.** All such datasets are linked by declared `1 : 1` edges. The engine treats them as one logical home; the home grain is their common PK (any one of them suffices).
   - **Multiple incompatible homes** — two or more datasets that are not `1 : 1`-linked supply row-level fields. The scalar shape has no aggregation step that can absorb the resulting replication. Raise `E_FAN_OUT_IN_SCALAR_QUERY` (D-023, extended) and stop. The diagnostic MUST identify the conflicting home datasets and suggest converting to an aggregation query (any join of two unrelated facts at row level requires an aggregation step on at least one side). A future proposal will add a semi-join filter form (deferred — see §6.8).
5. **Resolve enrichments.** For every `Fields` entry that is not a row-level field of the home dataset, resolve it as either:
   - An `N : 1` / `1 : 1` enrichment along a unique path (§6.9) — the value is attached to each home row.
   - A window expression evaluated at the home grain (§6.10).

   Cross-dataset references across an `N : N` edge that the engine cannot reduce to one of the two forms above raise `E_FAN_OUT_IN_SCALAR_QUERY`. Multiple paths ⇒ `E_AMBIGUOUS_PATH`. No path ⇒ `E_NO_PATH`. Aggregate-bodied fields (any aggregate in a field expression) are rejected at model-validation time per §4.3.
6. **Verify the final grain equals the starting grain.** For a scalar query, no aggregation step exists between them, so any plan that would replicate home-dataset rows is fatal — `E_FAN_OUT_IN_SCALAR_QUERY` (D-023). The aggregation-query shape handles the same condition non-fatally per A.6.4.
7. **Apply `Where` row-level filter** (post-enrichment, pre-windowing in terms of standard-SQL ordering; window functions run after `Where`).
8. **Evaluate any home-grain windows** with NULL placement defaulted per §5.1 / D-029.
9. **Apply `Order By`** with resolved NULL placement; then **`Limit`**.

**Notes for both shapes.**

- Steps 2–6 are pre-execution checks. An engine MAY surface any qualifying error from this set; it SHOULD prefer more-specific codes (e.g., `E3012` over `E_AMBIGUOUS_MEASURE_GRAIN`).
- The algorithm prescribes a **logical** order; engines are free to interleave the steps in their physical plans (e.g., apply `Where` early, push joins down) as long as the observable result is the same.
- For determinism (D-014), the same `(model, query, dialect)` MUST compile to byte-identical SQL on every run.

### 6.3 Having vs Where

The Foundation follows standard SQL semantics: the `Where` clause is applied pre-aggregation, and the `Having` clause is applied post-aggregation over the final-grain rows.

For this initial foundational semantics, the difference between `Where` and `Having` is straightforward because there is no developed concept of multi-step aggregations. Later proposals add a more refined understanding of grain, which enables multi-step calculations that may require a filter to affect something in the middle. For this document, `Having` is defined as occurring **over the post-`GROUP BY` row set**: predicates in `Having` MAY reference (a) any aggregate that resolves at the query's grain and (b) any grouping column from `Dimensions` (this is standard SQL — `HAVING region = 'EAST'` is legal, even though grouping columns could also have been filtered in `Where`). A predicate in `Having` that contains only row-level references with no aggregate and is not a grouping-column reference raises `E_NON_AGGREGATE_IN_HAVING`. The full predicate-shape routing matrix is in D-005 / step A.3 of the §6.2 algorithm.

Window functions follow standard SQL ordering — they execute over the post-`Where`, post-`GROUP BY`, post-`Having` row set, before `Order By` and `Limit`. They MAY appear in `Measures`, `Fields`, `Order By`, and `Having`. They MUST NOT appear in `Where` (SQL forbids this — windows run after `Where`). See §6.10 for the Foundation's full window-function contract.

### 6.4 Cardinality Inference

For each declared equijoin relationship, cardinality is inferred structurally from the dataset primary and unique keys.

```
get_cardinality(rel):
  to_unique   = rel.to_columns   matches rel.to_dataset.primary_key
                                  OR any entry in rel.to_dataset.unique_keys
  from_unique = rel.from_columns matches rel.from_dataset.primary_key
                                  OR any entry in rel.from_dataset.unique_keys

  left  = "1" if from_unique else "N"
  right = "1" if to_unique   else "N"
  return (left, right)
```

| Case | Inferred | Typical shape |
|:---|:---|:---|
| `to` side columns match PK/UK, `from` does not | `N : 1` | fact → dimension |
| Both sides match PK/UK | `1 : 1` | dimension ↔ dimension |
| Neither side matches PK/UK | `N : N` | bridge / missing-key model |

**`N : N` is the conservative inference** when keys are missing. The Foundation guarantees correct results across an `N : N` edge under the rules of §6.8 — the engine either finds a safe rewrite or errors out. Models with no declared keys are still well-formed, but more edges will be conservatively inferred as `N : N`, restricting the queries that resolve and steering the engine toward heavier SQL shapes. Declaring PKs / UKs makes a wider class of queries answerable and tends to produce simpler emitted SQL.

### 6.5 Join Contexts

Joins appear in four contexts, each with distinct rules:

| Context | Purpose | Notes |
|:---|:---|:---|
| **Aggregation join** | Bring columns together for a metric's aggregation or for grouping dimensions. | Default type per §6.6. |
| **Filtering join** | Semi-join / anti-semi-join for filter evaluation. | Never causes row duplication. |
| **Multi-fact composition join** | Combine results from separately computed fact tables on shared dimensions (chasm-trap resolution). | Default: `FULL OUTER` on shared dims; scalar grand-totals degenerate to `CROSS JOIN`. |
| **Fan-out-safe pre-aggregation** | Aggregate the many-side first so a subsequent join doesn't fan out. | Emitted automatically. |

### 6.6 Default Join Types

| Scenario | Default | Reasoning |
|:---|:---|:---|
| Many-side enriched with one-side (`N : 1`) — single-measure aggregation query | `LEFT` from fact → dim | Preserve all fact rows; unmatched dims become NULL. A dim value with no matching fact rows does **not** appear in the result. This matches raw SQL `SELECT dim.X, SUM(fact.Y) FROM fact LEFT JOIN dim ... GROUP BY dim.X`. Exposes data-quality issues (unmatched fact rows) instead of silently hiding rows. |
| `1 : 1` | `LEFT` **from the starting-grain side outward** | Safe either way for cardinality; the direction matters only for orphan visibility. The "starting-grain side" is the dataset whose grain is finer-or-equal to the query's grain along the path being resolved (typically the fact in an aggregation query, or the home dataset of a scalar query). Orphan rows on the starting-grain side surface (Semantic 1); orphan rows on the far side are filtered out. This matches the `N:1` rule above when the relationship happens to be `1:1`. |
| Composition across two separately-aggregated measures from **incompatible fact roots** (multi-measure aggregation query) | `FULL OUTER` on the shared dimensions | Neither side should lose groups. This is the **only** join shape that correctly merges two independently-aggregated facts — an `INNER` or single-direction `LEFT` would silently drop groups present only in one branch. The dim values appearing in the result come from the *union* of both branches' join paths, not from the dim domain alone. (For multi-measure queries whose measures share a fact root through `N:1` ancestors, the FULL OUTER stitch is mathematically equivalent to a single-path aggregation; engines MAY emit either plan.) |
| Composition onto an empty shared-grain set (scalar grand total) | `CROSS JOIN` of **per-fact 1-row scalars** | Each fact branch is independently aggregated to a single scalar row first (one row, the empty grain); the `CROSS JOIN` then produces exactly one row per branch combination — i.e., one row total. The Foundation requires that each branch be reduced to a 1-row scalar **before** the `CROSS JOIN`; an engine MUST NOT `CROSS JOIN` two non-scalar row sets, which would produce a Cartesian product. |

**Why `LEFT`, not `INNER`?** An `INNER JOIN` would silently drop facts whose dimension key is unresolved — this is a correctness failure with no error message. `LEFT` surfaces the problem (NULL dimension values in the result). The Foundation has no Foundation-level mechanism to opt into `INNER`; opt-in mechanisms (declared referential integrity, per-metric overrides) are deferred to §10.

**Why `LEFT` (fact → dim), not `LEFT` (dim → fact)?** For a single-measure aggregation query, the natural anchor is the fact: every fact row should contribute to exactly one group, and unmatched fact rows should be visible (bucketed to a `NULL`-key group). Anchoring on the dim instead would suppress orphan facts (silently wrong, violates Semantic 1) and would inflate the result with dim values that have no fact data (silently right but inconsistent with raw SQL — and the user did not ask to see "all our regions", they asked for "revenue by region"). If the user wants "all dim values whether or not the fact has data", they make the query multi-measure (e.g. add `COUNT(dim.id) AS dim_population` as a second measure) — which triggers the FULL OUTER stitch in row 3, and every dim value appears via the dim-population branch.

### 6.7 Avoiding Traps

The Foundation declares the §6.1 semantics that ensure no traps are encoded in analytical queries. Implementation, however, is engine-dependent. There are a handful of safe ways to combine joins and aggregations; providers are free to implement whichever they choose, as long as they preserve the §6.1 semantics.

Different implementations may support different edge cases differently. As a result, there are a handful of cases that implementations MAY choose to reject:

- Facts that have no declared unique or primary key.
- Many-to-many joins that would assign a row to multiple groups.

### 6.8 M:N Resolution

`N : N` relationships are valid model citizens, however, some engines may not support all ways of querying over them.  In these cases, it is acceptable for them to return MN_AGGREGATION_REJECTED.  Hoever, any engines that do support N : N relationships, MUST adhere to the semantics listed below.

**Semantic guarantee.** When a query traverses an `N : N` relationship, the engine MUST produce results that are mathematically equivalent to one of the safe rewrites below — i.e., results in which no row is double-counted because of fan-out, and no measure is silently inflated by a chasm. If no safe rewrite exists at the query's grain, the engine MUST raise a code-tagged error rather than emit potentially-wrong SQL. 


**Equivalent safe rewrites.** Any of the following plan shapes produces correct results; the engine may use any of them, or any combination, as long as the result agrees with at least one.

| # | Rewrite | Idea | Reference plan shape |
|:---:|:---|:---|:---|
| 1 | **Bridge** (§6.8.1) | A bridge dataset with `N : 1` edges to both endpoints lets the planner traverse the M:N. | Two `enrich` hops via the bridge + a de-duplication step at the (fact, group-key) level to enforce Semantic 2 (each row of the measure's home dataset contributes to each group at most once). |
| 2 | **Stitch** (§6.8.2) | Both endpoints reach a common set of dimensions; the planner aggregates each side at the shared grain and joins. | Independent `aggregate` per endpoint at the shared grain, then `merge` (FULL OUTER on the shared dims). |

> **Deferred — semi-join filter form.** A third resolution mode —
> using a semi-join expression like `EXISTS_IN` purely in a `Where`
> predicate — is intentionally **deferred** to a future proposal that
> covers semi-join semantics in full (NULL-safety, NOT-form,
> correlated/uncorrelated shapes). Until that proposal lands, M:N
> resolution in the Foundation is limited to **Bridge** and **Stitch**.
> If a model genuinely needs a cross-fact filter today, the author
> must either add a bridge dataset, or express the filter via an
> aggregation step that produces the filter set explicitly.

**Error contract.** When no safe rewrite produces a correct answer at the query's grain, the engine MUST fail with one of:

| Code | Condition | Required guidance in the error |
|:---|:---|:---|
| `E3012_MN_NO_SAFE_REWRITE` | An `N : N` traversal in a measure has no semantically-equivalent safe rewrite given the current model and query grain. | Suggest adding a bridge dataset or a shared dimension. (A semi-join filter form is deferred — see the note above.) |
| `E3013_NO_STITCHING_DIMENSION` | Two unrelated facts (different roots, no path) are referenced together with no dimension shared by both. | Note that the result would otherwise be a Cartesian product; suggest adding a shared dimension. |

`E3011_MN_AGGREGATION_REJECTED` is reserved for the **engine-capability opt-out** described in the *Semantic guarantee* above: an engine that elects not to support M:N traversal at all MUST raise it for *every* M:N query and MUST NOT emit SQL. It is not a per-query verdict — engines that DO support M:N use `E3012` / `E3013` for the cases where a particular query has no safe rewrite.

#### 6.8.1 Bridge Datasets (reference rewrite)

A **bridge dataset** is any dataset with declared `N : 1` relationships to two or more other datasets. Bridges are not a special node type — they are recognizable from cardinality alone. No keyword is required; the engine discovers the bridge structurally:

```yaml
datasets:
  - name: order_lines
    source: sales.public.order_lines
    primary_key: [order_id, line_id]
```

**Worked example.** A classic actor↔movie M:N modelled through `appearances`:

```yaml
datasets:
  - { name: actors,      primary_key: [actor_id] }
  - { name: movies,      primary_key: [movie_id] }
  - { name: appearances, primary_key: [actor_id, movie_id] }   # bridge

relationships:
  - { name: app_to_actor, from: appearances, to: actors, from_columns: [actor_id], to_columns: [actor_id] }   # N:1
  - { name: app_to_movie, from: appearances, to: movies, from_columns: [movie_id], to_columns: [movie_id] }   # N:1
```

Tiny dataset to make the bridge resolution concrete:

`actors`:

| actor_id | name  | height |
|:---:|:---|---:|
| A1 | Alice | 170 |
| A2 | Bob   | 170 |
| A3 | Carol | 180 |

`movies`:

| movie_id | title  | gross |
|:---:|:---|---:|
| M1 | Action | 100 |
| M2 | Drama  | 200 |
| M3 | Comedy |  50 |

`appearances` (bridge):

| actor_id | movie_id |
|:---:|:---:|
| A1 | M1 |
| A1 | M2 |
| A2 | M1 |
| A3 | M3 |

**Query**: `Measures: [SUM(movies.gross)]`, `Dimensions: [actors.height]`.

Per Semantic 2 (§6.1), each row of `movies` (the measure's home dataset) MUST contribute at most once to any given output group. A movie like M1, whose cast includes two actors at the same height, must contribute to that height *once*, not twice. The bridge plan walks the bridge to materialize the unique `(movie, height)` associations, then aggregates.

*Step 1 — enrich `appearances` along both `N : 1` edges (`→ movies` for `gross`, `→ actors` for `height`):*

| actor_id | movie_id | height | gross |
|:---:|:---:|---:|---:|
| A1 | M1 | 170 | 100 |
| A1 | M2 | 170 | 200 |
| A2 | M1 | 170 | 100 |
| A3 | M3 | 180 |  50 |

*Step 2 — de-duplicate to one row per (`movie_id`, `height`) pair. This is the bridge-resolution step that enforces Semantic 2: each fact contributes to each group at most once.*

| movie_id | height | gross |
|:---:|---:|---:|
| M1 | 170 | 100 |   ← M1 had two appearances at height 170 (Alice, Bob); kept once
| M2 | 170 | 200 |
| M3 | 180 |  50 |

*Step 3 — aggregate to query grain (`SUM(gross)` per `height`):*

| height | SUM(movies.gross) |
|---:|---:|
| 170 | 300 |
| 180 |  50 |

A naive flat join `actors ⋈ appearances ⋈ movies` with `GROUP BY actors.height` produces `(170 → 400, 180 → 50)` because M1's 100 is counted once per appearance. The bridge plan above produces `(170 → 300, 180 → 50)` because M1 is counted once per `(movie, height)` association. The bridge-plan answer is the one Semantic 2 mandates and the one Looker symmetric aggregates, Tableau Multi-Fact relationships, and Power BI bridge-table best practice all produce on the same data.

Note that summing the per-height totals (`300 + 50 = 350`) MAY differ from summing the source table directly (`100 + 200 + 50 = 350`, equal here only because no movie has actors at multiple heights). Semantic 2 explicitly permits this divergence: a movie whose cast spans two heights would contribute to both height groups, so the per-group totals can sum to more than the base-table total.

**Non-distributive aggregates** (`AVG`, `MEDIAN`, and other holistic forms) across an M:N edge are accepted under the same contract as the distributive case above: the aggregate's input is the unique `(measure-home-row, group-key)` row set, and the aggregate is evaluated once over that set per group. Because the contract enumerates a single input row set and applies the aggregate once, there is no algebraic-decomposition concern — that concern arises only when a plan is *forced* to decompose the aggregate across multiple stages, which happens for chasm pre-aggregation (§6.7) and for stitch (§6.8.2) but not here. Engines MAY satisfy the contract by any plan that produces equivalent results; the steps shown above are a reference construction, not the only legal one. For the fixture above: `AVG(movies.gross)` grouped by `actors.height` ⇒ `170 → AVG(100, 200) = 150`, `180 → AVG(50) = 50`. This is the heavy-side-weighted single-step analogue of the `1 : N` rule in §4.5.

A *different* interpretation — "per-actor-first" averaging, where the engine first computes each actor's personal average gross, then averages those per-actor averages within the height group — yields different numbers (`170 → AVG(150, 100) = 125`, `180 → 50`). That interpretation is reachable only through *nested aggregation* (`AVG(AVG(movies.gross))`), which carries an implicit grain pin on the inner aggregate. The nested form is **deferred to §10's grain-aware-functions proposal** and currently raises `E_NESTED_AGGREGATION_DEFERRED` (§4.5). Users who want the per-home-row-first reading wait for §10; the bare form continues to give the bridge-dedup answer.

#### 6.8.2 Stitching Dimensions (reference rewrite)

A **stitching dimension** is a dimension reachable from both endpoints of a query through `N : 1` paths. When the query references measures from two facts that share such a dimension (and no measure on the `N : N` edge itself), the safe rewrite is to compute each fact independently at its home grain, `merge` them on the finest shared key, then aggregate to the query grain.

This is the same pattern §6.7 already applies for the **chasm trap** — the only difference is that the two facts are now linked by an explicit `N : N` relationship rather than two separately-declared paths through a shared dim. The result contract (not the SQL) is what §6.8.2 fixes: every group of either fact appears in the output (Semantic 3); a fact that has no rows in a given group contributes `NULL` (or `0` if the metric `COALESCE`s).

An engine that picks this rewrite MUST raise `E3013_NO_STITCHING_DIMENSION` when the query's dimension set is empty *and* the two endpoints share no path — silently producing a Cartesian product would be wrong.

**Worked example.** `orders` and `returns` both connect to a `customers` dimension. The semantic query is:

```yaml
Dimensions:
  - customers.region
Measures:
  - SUM(orders.amount)  AS total_revenue
  - SUM(returns.amount) AS total_returns
```

Tiny dataset (note: every region has at least one fact present, but not every customer appears in both facts — this is what makes Semantic 3 visible):

`customers`:

| customer_id | region |
|:---:|:---|
| C1 | EAST |
| C2 | EAST |
| C3 | WEST |
| C4 | NORTH |

`orders`:

| order_id | customer_id | amount |
|:---:|:---:|---:|
| O1 | C1 | 100 |
| O2 | C1 | 200 |
| O3 | C2 |  50 |
| O4 | C3 | 300 |

`returns`:

| return_id | customer_id | amount |
|:---:|:---:|---:|
| R1 | C1 | 25 |
| R2 | C4 | 10 |

Note that C4 has only a return (no orders), C3 has only an order (no return), and only one region (EAST) has both facts.

The engine computes each fact independently at the home grain of its source, merges them, then aggregates to the query grain:

*Step 1a — aggregate `orders` to customer grain:*

| customer_id | revenue |
|:---:|---:|
| C1 | 300 |
| C2 |  50 |
| C3 | 300 |

*Step 1b — aggregate `returns` to customer grain:*

| customer_id | returns_ |
|:---:|---:|
| C1 | 25 |
| C4 | 10 |

*Step 2 — FULL OUTER merge on `customer_id`:*

| customer_id | revenue | returns_ |
|:---:|---:|---:|
| C1 | 300 |   25 |
| C2 |  50 | NULL |
| C3 | 300 | NULL |
| C4 | NULL |  10 |

*Step 3 — enrich with `customers.region` (`N : 1`):*

| customer_id | region | revenue | returns_ |
|:---:|:---|---:|---:|
| C1 | EAST  | 300 |   25 |
| C2 | EAST  |  50 | NULL |
| C3 | WEST  | 300 | NULL |
| C4 | NORTH | NULL |  10 |

*Step 4 — aggregate to query grain (`SUM` per region):*

| region | total_revenue | total_returns |
|:---|---:|---:|
| EAST  | 350 |   25 |
| WEST  | 300 | NULL |
| NORTH | NULL |  10 |

**Semantic 3 is visible in the last table:** WEST has revenue but no returns (returns column is `NULL`, not omitted); NORTH has returns but no revenue (revenue column is `NULL`, region row still present). Neither fact loses its groups. This follows standard SQL — `SUM` over an empty (or all-NULL) input row set is `NULL` (§6.11). If the user wants `0` instead of `NULL`, that is a per-metric authoring decision (`COALESCE(SUM(...), 0)`), not a join-semantics concern.

Already handled by the chasm-trap planner today (§6.7); §6.8.2 just names the equivalence so two engines can agree on the result without prescribing the rewrite.

### 6.9 Path Resolution and Ambiguity

When a query spans multiple datasets, the engine finds a path through the relationships graph. The rules:

1. **Unique path.** If exactly one path of `N : 1` / `1 : 1` edges connects the referenced datasets, the engine uses it.
2. **Multiple paths.** If two or more paths exist (e.g., `orders` can reach `users` via `placed_by` or `fulfilled_by`), the engine MUST raise `E_AMBIGUOUS_PATH`. The Foundation provides no in-model mechanism to pick between them — path-disambiguation hints (`using_relationships`) are deferred to a later proposal (§10).
3. **No path.** If the referenced datasets are not connected, the engine MUST raise `E_NO_PATH`.
4. **Path must be acyclic within a query.** A relationship graph may contain cycles globally, but a single query MUST select an acyclic sub-path.

Per-metric join-type overrides (`joins.type: INNER | LEFT | FULL`) are also deferred to a later proposal — Foundation engines use only the §6.6 defaults.

### 6.10 Window Functions

Standard SQL window functions are part of the Foundation. The expression-level catalog — ranking (`ROW_NUMBER`, `RANK`, `DENSE_RANK`, `NTILE`, `PERCENT_RANK`, `CUME_DIST`), navigation (`LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE`), and aggregate-windows (any required aggregate combined with `OVER (...)`) — is defined normatively in [`SQL expression subset`](https://docs.google.com/document/d/1Jn98kHWsnbvo1MycQBAnWirGTwRmw-DWaen7coNCYWA/edit?usp=sharing) §"Window Functions". This section adds the **semantic contract** the Foundation requires on top of that catalog so that two engines compute the same answer for the same window.

#### 6.10.1 Where windows MAY appear

| Clause / expression site | Window allowed? | Evaluated at |
|:---|:---:|:---|
| `Measures` entry, or a metric `expression` referenced by `Measures` | Yes | The query's grain (post-`GROUP BY`) |
| `Fields` entry (scalar query), or a field `expression` projected by `Fields` | Yes | The home dataset's grain |
| `Order By` | Yes | The post-`Having` row set |
| `Having` | Yes (rare) | The post-`GROUP BY` row set |
| `Where` | **No** — SQL prohibits | — |
| Inside an aggregate (`SUM(SUM(x) OVER (PARTITION BY ...))`) | Yes — the inner window runs at the inner aggregate's grain; the outer aggregate runs at the query grain | — |
| Inside another window (`RANK() OVER (... ORDER BY SUM(x) OVER (...))`) | **No** — illegal in SQL | — |

References to a window function in `Where` MUST raise `E_WINDOW_IN_WHERE` with a suggestion to use `Having` or to wrap the calculation as a filterable field.

#### 6.10.2 Determinism contract

Windows are the a source of cross-engine non-determinism in BI SQL. The Foundation pins down three sources of that non-determinism — one by centralising it in §5.1, two by adding window-specific rules here:

1. **NULL ordering inside `OVER (... ORDER BY ...)` follows the §5.1 "Common clause semantics" default.** Any `ORDER BY <expr>` inside `OVER (...)` is governed by the same rule as the outer `Order By`: if `NULLS FIRST` / `NULLS LAST` is not explicit, the resolved placement is **`NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC`** (the SQL:2003 "NULLs are high-end" convention), and engines MUST guarantee that resolved row order. 

2. **`LAST_VALUE` default-frame warning.** OSI will follow SQL's default behaviour with its default window frame (`RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`).  This causes `LAST_VALUE(x) OVER (PARTITION BY g ORDER BY t)` return the current row, not the partition's last value. This is an often-reported mistake people can make, however, we think staying consistent with SQL is more valuable that a different default.

3. **Tie-breaking in order-dependent windows.** For functions whose result depends on row order (`ROW_NUMBER`, `RANK`, `DENSE_RANK`, `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE`, `NTILE`), engines MAY emit a diagnostic warning when the `ORDER BY` cannot tie-break to a stable ordering (i.e., the order columns do not include a unique key of the partition). This is a MAY, not a SHOULD — many real BI queries accept rare-tie non-determinism. For order-independent windows (`PERCENT_RANK`, `CUME_DIST`, partition-only aggregate windows like `SUM(x) OVER (PARTITION BY region)`) this rule does not apply.

#### 6.10.3 Fan-out and Semantic 2

Semantic 2 (no row of dataset `A` contributes more than once to a measure on `A`) extends to windows: a window function whose home dataset is `A` MUST run over the pre-fan-out row set of `A`, just like an aggregate would. Concretely, a metric `SUM(orders.amount) OVER (PARTITION BY orders.region)` joined to a fan-out child of `orders` MUST NOT see the duplicated rows in the window calculation; the engine MUST pre-aggregate or otherwise materialize the home-grain rows before applying the window. Engines that cannot satisfy this MUST raise `E_WINDOW_OVER_FANOUT_REWRITE` rather than emit potentially-doubled running totals.

#### 6.10.4 Grain interaction

A field whose expression contains a window function evaluates at the home dataset's grain — exactly like any other field expression. The window's `PARTITION BY` and `ORDER BY` expressions MUST be resolvable at that home grain (typically: row-level fields of the home dataset, or `N : 1` enrichments along a unique path).

When such a windowed field is referenced from an aggregation query that groups the home dataset to a coarser grain, the windowed value is a row-level scalar at the home grain — finer than the query's grain. Per D-024, a **bare** reference to that field in `Measures` MUST raise `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`; the user MUST wrap the reference in an aggregate (`MAX(orders.order_rank)`, `COUNT(*) WHERE orders.order_rank = 1`, etc.) or use it in `Where` as the canonical filter. Aggregating a ranking function across a coarser grain rarely produces meaningful analytics, but is well-defined when the user spells out the outer aggregate. The far more common BI pattern (filter by windowed field in `Where`, then aggregate) works correctly under the pre-fan-out rule above. The canonical "first order per customer" pattern is:

```yaml
# field on orders
- name: order_rank
  expression: ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date NULLS LAST)

# query
Dimensions: [customers.region]
Measures:   [COUNT(*) AS first_orders]
Where:      orders.order_rank = 1
```

This works because `orders.order_rank` is evaluated at the orders home grain (before any aggregation to region), the `Where` filters at that grain, and the `COUNT(*)` then aggregates the filtered rows to region.

#### 6.10.5 Windowed metrics in composition

A metric whose `expression` contains a window function (e.g., `running_total = SUM(amount) OVER (ORDER BY order_date NULLS LAST)`) is well-defined when used directly in a query: the window runs over the post-`Where`, post-`GROUP BY` row set at the query's grain.

What is **deferred**: referencing a windowed metric *from another metric's expression*. The composition rules in §4.5 forms (2) and (3) say metric-references-metric resolves at the query's grain with no grain or filter propagation; that rule has subtle interactions with the inner window's `PARTITION BY` / `ORDER BY` that need their own proposal (§10). Engines MUST raise `E_WINDOWED_METRIC_COMPOSITION` when a metric expression references a windowed metric.

The rule applies to **any** reference to a windowed metric from another metric's body — including syntactically no-op transformations such as `running_total + 0`, `CAST(running_total AS BIGINT)`, or `running_total * 1`. Engines MUST detect references at the metric-AST level (not by inspecting whether the surrounding expression is "real arithmetic"). Direct use of the windowed metric in `Measures` is the only Foundation-supported consumption shape today.

#### 6.10.6 Frame modes

Two frame modes are required: `ROWS` and `RANGE`. The third standard-SQL mode, `GROUPS`, is deferred to a later proposal. Frame bounds MUST be literal integers (or `UNBOUNDED` / `CURRENT ROW`); parameterized frame bounds (`:lookback_frame PRECEDING`) are deferred to §10.

### 6.11 Empty-input and NULL-input aggregate behaviour

The Foundation follows **standard SQL** for aggregates over empty and NULL-containing input row sets.

#### 6.11.1 Empty-input behaviour

| Aggregate | Empty-set result |
|:---|:---|
| `COUNT(*)`, `COUNT(x)`, `COUNT(DISTINCT x)` | `0` |
| `SUM(x)`, `AVG(x)`, `MIN(x)`, `MAX(x)`, `MEDIAN(x)`, `PERCENTILE_CONT(...)`, `STDDEV(x)`, `VARIANCE(x)` | `NULL` |

`COUNT` returns `0` because it counts rows; an empty input has zero rows. Every other aggregate returns `NULL` because there is no defined result when there are no contributing values. This is what every major SQL dialect does natively (Snowflake, Postgres, BigQuery, Databricks, SQL Server, MySQL), and what every BI tool's source query produces unless the modeller explicitly overrides it (e.g. by wrapping `SUM` in `COALESCE(..., 0)`).

#### 6.11.2 NULL-input behaviour (normative)

When the input row set is non-empty but some rows have `NULL` in the aggregated column, the Foundation follows ANSI SQL:

| Aggregate | NULL handling |
|:---|:---|
| `COUNT(*)` | Counts all rows, including those with `NULL` in any column. |
| `COUNT(x)`, `COUNT(DISTINCT x)` | Ignores rows where `x IS NULL`. `COUNT(DISTINCT x)` additionally collapses duplicate non-NULL values to one. If every row's `x` is `NULL`, the result is `0`. |
| `SUM(x)`, `AVG(x)`, `MIN(x)`, `MAX(x)`, `MEDIAN(x)`, `STDDEV(x)`, `VARIANCE(x)`, `PERCENTILE_CONT(...)` | Ignores rows where `x IS NULL`. If every row's `x` is `NULL` (i.e. the non-NULL input subset is empty), the result is `NULL` — the same as §6.11.1's empty-input rule. |

This has one non-obvious consequence: `AVG(x)` over a set with one `5` and 99 `NULL`s is `5`, not `0.05`. Users who want NULLs treated as zero MUST write `AVG(COALESCE(x, 0))`.

#### 6.11.3 `COUNT(DISTINCT)` over `N : N` is safe

`COUNT(DISTINCT x)` is technically holistic (it cannot be safely re-aggregated by re-applying `COUNT(DISTINCT)` to partial counts), but it has an idempotence property under join-induced row duplication: duplicating a row that already contributes its `x` value to the distinct set does not change the result. The §6.8.1 bridge plan materialises distinct `(home, group-key)` associations precisely to enforce Semantic 2 — the same de-duplication that `COUNT(DISTINCT x)` performs naturally.


#### 6.11.4 Composition

`revenue / cost` where `cost` is zero rows returns `NULL / NULL = NULL` — not a division-by-zero error. Authors who want a sentinel value should write `revenue / NULLIF(SUM(cost.amount), 0)` (still `NULL` on empty `cost`) or `COALESCE(SUM(revenue.amount), 0) / NULLIF(SUM(cost.amount), 0)` to surface a `0` numerator. These are author-level concerns, not Foundation-level rewrites.

#### 6.11.5 Multi-fact stitch

When the chasm/stitch plan produces a group present in fact A but not in fact B (Semantic 3, §6.8.2), fact B's measure cell is whatever standard SQL would produce — `NULL` for `SUM`, `0` for `COUNT`. Models that prefer `0` for missing-fact cells should declare the metric as `COALESCE(SUM(amount), 0)` (this is a per-metric author decision, not a Foundation default).

---

## 7. SQL Expression Subset

The SQL expression subset is defined in [SQL_EXPRESSION_SUBSET.md](SQL_EXPRESSION_SUBSET.md).  Implementations may support many different dialects, but MUST support the OSI_SQL_2026 dialect at a minimum.

---

## 8. Worked Examples

### 8.1 Single-Table Revenue

```yaml
semantic_model:
  - name: sales_analytics
    datasets:
      - name: orders
        source: sales.public.orders
        primary_key: [order_id]
        fields:
          - name: order_id
            expression: order_id
          - name: customer_id
            expression: customer_id
          - name: amount
            expression: amount
          - name: region
            expression: region
          - name: status
            expression: status

# All metrics live at the top-level `metrics:` section, referenced by bare name.
metrics:
  - name: total_revenue
    expression: SUM(orders.amount)
  - name: order_count
    expression: COUNT(orders.order_id)
```

**Query:**
```yaml
dimensions: [orders.region]
measures:   [total_revenue, order_count]
where:      "orders.status = 'completed'"
order_by:   [{field: total_revenue, direction: DESC}]
```

**Compiled SQL:**
```sql
SELECT region,
       SUM(amount)    AS total_revenue,
       COUNT(order_id) AS order_count
FROM sales.public.orders
WHERE status = 'completed'
GROUP BY region
ORDER BY total_revenue DESC
```

No joins. Standard aggregation.

### 8.2 Fact-to-Dimension Enrichment (N:1)

Add customers:

```yaml
datasets:
  - name: customers
    source: sales.public.customers
    primary_key: [id]
    fields:
      - name: id
        expression: id
      - name: segment
        expression: segment

relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]
```

**Query:**
```yaml
dimensions: [customers.segment]
measures:   [total_revenue]
```

**Compiled SQL (`LEFT JOIN` per the Foundation default — orphan orders surface as a NULL `segment` row instead of being silently dropped):**
```sql
SELECT c.segment, SUM(o.amount) AS total_revenue
FROM sales.public.orders o
LEFT JOIN sales.public.customers c ON o.customer_id = c.id
GROUP BY c.segment
```

Authors who know the data is referentially intact and want `INNER` semantics will get them via the deferred referential-integrity extension (§10).

### 8.3 Chasm-Trap Resolution

Add returns:

```yaml
datasets:
  - name: returns
    source: sales.public.returns
    primary_key: [return_id]
    fields:
      - name: return_id
        expression: return_id
      - name: customer_id
        expression: customer_id
      - name: amount
        expression: amount

relationships:
  - name: returns_to_customers
    from: returns
    to: customers
    from_columns: [customer_id]
    to_columns: [id]

# Add the new metric to the top-level metrics section.
metrics:
  - name: total_returns
    expression: SUM(returns.amount)
```

**Query:** `total_revenue` and `total_returns` by `customers.segment`.

**Compiled SQL:** independent aggregation + composition on shared dim:

```sql
WITH order_metrics AS (
  SELECT c.segment, SUM(o.amount) AS total_revenue
  FROM sales.public.orders o
  LEFT JOIN sales.public.customers c ON o.customer_id = c.id
  GROUP BY c.segment
),
return_metrics AS (
  SELECT c.segment, SUM(r.amount) AS total_returns
  FROM sales.public.returns r
  LEFT JOIN sales.public.customers c ON r.customer_id = c.id
  GROUP BY c.segment
)
SELECT COALESCE(o.segment, r.segment) AS segment,
       o.total_revenue,
       r.total_returns
FROM order_metrics o
FULL OUTER JOIN return_metrics r USING (segment)
```

Neither fact fans the other; neither segment drops if it exists on only one side. A segment present on only one side has `NULL` for the missing fact's measure — standard SQL behaviour, preserved by §6.11. Models that prefer `0` on missing cells declare it explicitly: `COALESCE(SUM(amount), 0) AS total_revenue`.

### 8.4 Semi-Join Filter (deferred)

A semi-join filter form (e.g., `EXISTS_IN(orders.customer_id,
returns.customer_id)` — "orders for customers who have returned
something") is intentionally **deferred** to a future proposal that
addresses semi-join semantics in full (NULL-safety, `NOT`-form,
correlated/uncorrelated shapes, compilation contract). Until that
proposal lands, the Foundation does not define `EXISTS_IN`. Models
that need cross-fact filtering today must express it via aggregation
(produce the filter set as an explicit dimension, then filter on it).

### 8.5 Ambiguous Path → Error

```yaml
relationships:
  - name: order_placed_by
    from: orders
    to: users
    from_columns: [placed_by_id]
    to_columns: [id]
  - name: order_fulfilled_by
    from: orders
    to: users
    from_columns: [fulfilled_by_id]
    to_columns: [id]
```

A Foundation query for `COUNT(orders.order_id)` grouped by `users.region` has two valid paths to `users` (via `order_placed_by` or `order_fulfilled_by`). The Foundation has no in-model disambiguation mechanism, so the engine MUST fail:

```
E_AMBIGUOUS_PATH: 2 paths from 'orders' to 'users':
  - order_placed_by   (orders.placed_by_id = users.id)
  - order_fulfilled_by (orders.fulfilled_by_id = users.id)
Path-disambiguation is deferred to OSI_Proposal_Path_Disambiguation.md (§10).
Workaround: split the model into two queries, or define separate dataset
views over `orders` that pre-pick one path.
```

---

## 9. Extensibility Hooks

The Foundation's surface is intentionally narrow. Each of the deferred features in §10 layers on additively, and the spec leaves open hooks so they can be adopted later without breaking any Foundation-conformant model:

| Feature | Hook |
|:---|:---|
| Explicit grain overrides and grain-aware functions | Fields and metrics MAY in future carry an optional `grain:` keyword. The §10 grain-aware-functions proposal bundles four constructs that all require an implicit grain pin: (a) **all aggregate-bodied fields** (any aggregate in a field expression, `E_AGGREGATE_IN_FIELD`, §4.3), (b) nested aggregation in metrics (`E_NESTED_AGGREGATION_DEFERRED`, §4.5), (c) per-dataset `metrics:` blocks and the `dataset.metric_name` reference form (§4.5), and (d) explicit grain markers (`FIXED`/`INCLUDE`/`EXCLUDE`). Foundation users express every aggregate as a model-scoped metric (top-level `metrics:`, referenced by bare name). |
| Filter context | Metrics MAY in future carry an optional `filter:` block (`reset`, `expression`). |
| Non-equijoins / ASOF / range | Relationships MAY in future carry `condition`, `asof`, or `range`. |
| Access modifiers | `access_modifier: public | private` already defined (§4.3, §4.5). |
| Parameterized window frame bounds | Window `OVER (... ROWS BETWEEN ... )` may in future allow bind-parameter bounds (`:lookback_frame PRECEDING`). |
| Windowed-metric composition | A metric whose `expression` references another metric whose expression contains a window function is deferred — see §10. |

Any tool that today reads Snowflake Semantic Views YAML, Cube.js YAML, Looker LookML, or dbt semantic-layer YAML can round-trip to Foundation OSI without losing the constructs it actually uses.

---

## 10. Explicitly Deferred Features

Each deferred feature has an existing design in `specs/`. Adopting any of them is a strict addition on top of the Foundation.

| Feature | Design Doc |
|:---|:---|
| Explicit grain overrides and grain-aware functions — covering: (a) explicit grain markers (`FIXED`, `INCLUDE`, `EXCLUDE`, and an explicit `TABLE` keyword); (b) **all aggregate-bodied fields** (any aggregate in a field expression, same-grain or cross-grain) (`E_AGGREGATE_IN_FIELD`, §4.3); (c) **nested aggregation** in any metric expression (`E_NESTED_AGGREGATION_DEFERRED`, §4.5); (d) **per-dataset `metrics:` blocks** and the `dataset.metric_name` reference form (§4.5). | `OSI_Core_Abstractions.md` §Grain, `OSI_Calc_Model_Semantics.md`. **Note:** these are bundled because each carries an implicit "this metric's home dataset is fixed" pin whose `Where`-context interaction §10 will settle explicitly. The Foundation has exactly one place metrics live (top-level `metrics:`) and one syntax for referencing them (bare name); fields are non-aggregate scalar expressions. |
| Filter context propagation (`reset`, `filter.expression` on metrics) | `OSI_Core_Abstractions.md` §Filter |
| Metric composition (metric-refs-metric with grain/filter interaction) | `OSI_Core_Abstractions.md` §Metric References & Composition |
| Model-level `natural_grain` declaration | Future proposal (separate addition). |
| Path disambiguation (`using_relationships`) and per-metric join-type overrides | `OSI_Proposal_Path_Disambiguation.md` (to be drafted) |
| Non-equijoin relationships (`condition`, `cardinality`) | `OSI_Proposal_Non_Equijoins.md` |
| ASOF and Range relationships | `OSI_Proposal_ASOF_and_Range_Joins.md` |
| Referential integrity (declared `from_all_rows_match` / `to_all_rows_match` to enable INNER promotion) | `OSI_Proposal_Referential_Integrity.md` |
| Semi-additive measures | `OSI_Proposal_Semi_Additive.md` |
| Grouping sets / rollup / cube | `OSI_Proposal_Grouping_Sets.md` |
| Pivot / unpivot operator | `OSI_Proposal_Pivot_Operator.md` |
| Parameterized window frame bounds (e.g. `ROWS BETWEEN :n PRECEDING AND CURRENT ROW`) | `SQL_EXPRESSION_SUBSET.md` §"Window Functions" — diverges from standard SQL, which requires integer-literal bounds. |
| `GROUPS` frame mode | Standard SQL but not portable: Postgres / Oracle / DuckDB support; Snowflake / BigQuery / Databricks / SQL Server / MySQL do not. `ROWS` and `RANGE` cover the same use cases for the engines that lack `GROUPS`. |
| Windowed-metric composition (`metric A = ...` references `metric B = SUM(...) OVER (...)`) | Composition rules in §4.5 forms (2) / (3) do not yet pin down what happens when the inner metric carries an `OVER (...)` clause. Direct use of windowed metrics in `Measures` works today (§6.10); composing them does not. |
| Snowflake `PARTITION BY EXCLUDING` | Non-standard SQL extension; useful but not portable. |
| Ordered-set aggregates with `WITHIN GROUP (ORDER BY ...)` (e.g. `LISTAGG`, `PERCENTILE_CONT`) | Standard SQL but inconsistently supported and orthogonal to the window-function subset already covered. |
| Symmetric aggregates (Looker hash-trick fanout-tolerant SQL) | Future codegen optimization. The Foundation gets equivalent correctness from §6.8.2 stitch (aggregate-then-merge), so symmetric aggregates would only ever be a performance lever, not a correctness mechanism. Adding them is a `codegen/` concern that swaps the SQL pattern without changing the algebra or any spec contract. |
| Multi-hop bridge resolution (`A → Br1 → Br2 → B`) | The Foundation resolves M:N through a *single* bridge dataset (§6.8.1). Two-hop bridges are common; three-or-more is rare and adds significant ambiguity surface. Authors who genuinely need it can compose two bridge resolutions by introducing an intermediate dataset modelled as a fact. Revisit if real models require it. |

---

## 11. Compliance Levels

An OSI Foundation-compliant implementation MUST:

1. Parse the YAML structure in §4 and reject any field outside the Foundation surface with `E_DEFERRED_KEY_REJECTED` (no silent acceptance of deferred features).
2. Distinguish **aggregation queries** (`Dimensions` / `Measures`) from **scalar queries** (`Fields`) per §5.1, rejecting mixed-shape queries with `E_MIXED_QUERY_SHAPE`. Execute each shape with the semantics of §5.1 and §6.2 — including `Where`, `Having` (aggregation only), `Order By`, and `Limit`.
3. Route filter predicates by **expression shape** per §6.3: aggregate inside `Where` ⇒ `E_AGGREGATE_IN_WHERE`; pure row-level predicate inside `Having` ⇒ `E_NON_AGGREGATE_IN_HAVING`; one boolean mixing row-level and aggregate terms ⇒ `E_MIXED_PREDICATE_LEVEL`.
4. Reject a bare metric reference inside `Fields` with `E_AGGREGATE_IN_SCALAR_QUERY` (§5.1.2). Reject any field whose expression contains an aggregate function with `E_AGGREGATE_IN_FIELD` (§4.3) — express it as a model-scoped metric and use an aggregation query.
5. Infer cardinality per §6.4, and resolve `N : N` traversals through a bridge dataset (§6.8.1) or shared-dimension stitch (§6.8.2) — failing with `E3012` / `E3013` when neither route applies. (Semi-join filter form is deferred — see §6.8 note.)
6. Apply aggregate-before-join and chasm-trap safety (§6.7).
7. Default join types per §6.6 (LEFT for enrichment, FULL OUTER for multi-fact composition).
8. Reject deferred relationship-level keys (`referential_integrity`, `condition`, `asof`, `range`) with `E_DEFERRED_KEY_REJECTED` **when running in default Foundation mode**. The diagnostic MUST identify the offending key and reference §10. An engine MAY accept any subset of deferred keys behind a clearly-named extension flag (per the MAY-list below), provided that:
   - the engine documents the flag and which keys it enables,
   - default Foundation behaviour (flag off) is unchanged and still rejects with `E_DEFERRED_KEY_REJECTED`, and
   - the flag is not on by default.

   This reconciles the `E_DEFERRED_KEY_REJECTED` MUST with the §11 MAY-list extension hook: a model is portable across Foundation engines exactly when it does not depend on any extension flag.
9. Resolve identifiers per §4.6: bare references in queries to the global namespace, `dataset.field` for dataset-scoped names, `E_NAME_COLLISION` on duplicate global names, `E_NAME_NOT_FOUND` when a reference does not resolve.
10. Support the required aggregations and required scalar functions defined in [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md).
11. Support standard SQL window functions (ranking, navigation, aggregate-windows; `ROWS` and `RANGE` frame modes; integer-literal frame bounds) per §6.10 and `SQL_EXPRESSION_SUBSET.md` §"Window Functions". Reject windows in `Where` with `E_WINDOW_IN_WHERE`. For any `ORDER BY <expr>` (outer or inside `OVER (...)`) that does not specify `NULLS FIRST` / `NULLS LAST`, resolve to the Foundation default — `NULLS LAST` for `ASC`, `NULLS FIRST` for `DESC` — and guarantee that resolved row order on every supported dialect, emitting the explicit clause whenever the dialect's native default would otherwise produce a different order (§5.1 Common clause semantics, D-029). Reject a metric expression that references a windowed metric with `E_WINDOWED_METRIC_COMPOSITION`.
12. *(Reserved — see deferred EXISTS_IN proposal.)* Semi-join filtering (`EXISTS_IN` / `NOT EXISTS_IN`) is deferred to a future proposal and is not part of the Foundation today.
13. Produce deterministic SQL — same `(model, query, dialect)` ⇒ byte-identical output.
14. Follow standard SQL for empty-input aggregates (§6.11 / D-033): `COUNT` family returns `0`; `SUM`, `AVG`, `MIN`, `MAX`, etc. return `NULL`. Models that need a non-`NULL` empty result MUST declare it per-metric (`COALESCE(SUM(...), 0)`).

An implementation SHOULD:

- Emit warnings (not errors) when a metric carries zero-impact annotations (e.g., a deferred-feature key that the engine accepts under an extension flag but that resolves to a no-op for this query).
- Emit a diagnostic warning when `LAST_VALUE` appears in a window without an explicit `frame_clause` (the default-frame footgun, §6.10.2).
- Expose the compiled SQL for inspection.
- Support ANSI_SQL as a baseline dialect and at least one vendor dialect.

An implementation MAY:

- Accept deferred features (§10) under a clearly-named extension flag, provided the Foundation behaviour is unchanged when the flag is off.
- Offer additional dialect-specific scalar / aggregation functions beyond the required set in [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md).
- Emit a diagnostic warning when an order-dependent window (`ROW_NUMBER`, `RANK`, `DENSE_RANK`, `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE`, `NTILE`) has an `ORDER BY` that cannot tie-break to a stable ordering (§6.10.2).
- Add observability instrumentation (plan inspection, GPA metrics, explain).

### 11.1 Compliance Test Suite

A canonical, executable compliance suite is published alongside this spec. It is the normative way to verify Foundation compliance. The suite location and exact format are implementation-published; engines verify conformance by running the suite and passing all cases marked `must_pass`. Each case is a triple:

```
(model_yaml, semantic_query, expected_outcome)
```

where `expected_outcome` is either:

| Outcome | Verification |
|:---|:---|
| **`Success(rows=...)`** | The plan compiles to SQL that, when executed against a reference engine over a seeded test database, returns the exact row multiset listed. |
| **`Failure(code=ErrorCode.E…)`** | The planner raises the specified error code (test asserts on `error.code`, never on message text). |

The suite is organized into **rounds**, where each round builds on patterns from the previous. Within each round cases are tagged `easy` / `medium` / `hard`, where:

- *easy*: single concept, one or two operators in the plan.
- *medium*: two or three concepts composed (e.g. enrichment + chasm trap; bridge + filter).
- *hard*: three or more concepts mixed, or pathological edge cases (composite keys under M:N, RI conflicts, scalar grain over multiple facts).

A compliant implementation must pass **all** cases marked `must_pass`. Cases marked `xfail_pending_implementation` correspond to features whose spec semantics are settled but whose implementation is still queued; they become `must_pass` as the implementation lands. The suite never asserts on internal plan shape (CTE count, join order, alias names) — only on observable behaviour (rows or error code), so the spec stays implementation-independent.

The Foundation does **not** mandate cross-engine SQL determinism — two compliant engines compiling the same `(model, query, dialect)` MAY produce different SQL strings as long as the observable rows or error codes agree. Per-engine determinism is required by D-014.

---

## 12. Alignment with Existing Semantic-Layer Implementations

This section catalogs how the OSI Foundation aligns or diverges from publicly documented behaviors of existing semantic-layer products. Per-vendor entries are evidence-cited where the relevant behavior is in public docs, and explicitly marked **`Unknown`** where current public documentation does not establish the answer. The Foundation's design draws on patterns common across these implementations; where the choices differ, the Foundation picks the option that satisfies the five user-visible semantics in §6.1.

The vendors covered below are not exhaustive. They are picked because their docs are publicly accessible at sufficient depth to support a comparison.

### 12.A Snowflake Semantic Views

> §12.A.2 below covers Snowflake **bugs** the Foundation resolves.
> Intentional Foundation design divergences from Snowflake — the
> "defensible design choice, not bug" category — are summarised at
> the end of this section under "Two known intentional divergences".

Snowflake's Semantic Views document a large surface of observable behaviors, including 25 cataloged in `snowflake-prod-docs/tests/semantic-views/ERRATA.md` (an errata document maintained by the Snowflake docs team). Many of these match the Foundation; a few diverge in ways the Foundation would resolve toward a typed error or a deterministic shape.

**Sources:**
- [`semantics.rst`](../snowflake-prod-docs/sphinx/source/user-guide/views-semantic/semantics.rst)
- [`sql-differences.rst`](../snowflake-prod-docs/sphinx/source/user-guide/views-semantic/sql-differences.rst)
- [`querying.rst`](../snowflake-prod-docs/sphinx/source/user-guide/views-semantic/querying.rst)
- `snowflake-prod-docs/tests/semantic-views/ERRATA.md` — errata numbers below correspond to this document.

#### 12.A.1 Convergence Already Achieved

These behaviors already match the Foundation:

| Errata | Topic | Why it aligns | Source |
|:---:|:---|:---|:---|
| #7 | LEFT OUTER JOIN exposes orphans with NULL dimensions | Matches §6.6 (default join type) and §6.7 (safety-first defaults). Opt-in INNER via declared RI is deferred (§10). | `semantics.rst` §"Join type" |
| #11 | Ad-hoc `SUM(fact)` matches pre-defined `SUM(fact)` metric | Matches the equivalence guarantee: same aggregation + same query grain ⇒ same result. | `sql-differences.rst` §"Ad-hoc aggregate" |
| #12 | Decomposability holds for additive metrics | Matches the distributive-aggregate rule in `SQL_EXPRESSION_SUBSET.md`. | `semantics.rst` §"Aggregation" |
| #13 | `COUNT(DISTINCT low_grain) GROUP BY high_grain` rejected | Matches §6.7: fan-out trap. Foundation rewrites via aggregate-before-join, Snowflake rejects; both prevent the double-count bug. | `semantics.rst` §"Granularity rule" |
| #14 | `EXPLAIN` works on semantic-view queries | Consistent with §11's SHOULD that the planner expose compiled SQL. | `semantics.rst` |
| #15 | Metrics may be aliased in the query surface | Consistent with §5.1. | (general SQL convention) |
| #18 | Inner WHERE before window / outer WHERE after | Standard SQL; matches Foundation §6.10 — windows run after `Where`, so window output reflects the filtered row set. | `sql-differences.rst` §"WHERE clause placement" |

**Cross-grain semantic equivalence.** The Foundation and Snowflake produce the same numerical results for any cross-grain reference that both engines accept, with one structural difference: Snowflake expresses cross-grain aggregation as a *column on the lower-granularity table*; the Foundation expresses every aggregate as a *model-scoped metric* (top-level `metrics:`, referenced by bare name).

- Snowflake's "Higher granularity" rule for cross-table column-level expressions ([`semantics.rst:391-393`](../snowflake-prod-docs/sphinx/source/user-guide/views-semantic/semantics.rst)) — a column on a lower-granularity table that references a higher-granularity table must aggregate (e.g. `customers.order_count AS COUNT(orders.oid)` aggregates orders down to customer grain) — has no Foundation equivalent at the field level. The Foundation defers all aggregate-bodied fields, including the same-grain case (§4.3, `E_AGGREGATE_IN_FIELD`); aggregates live in the top-level `metrics:` section. The same `COUNT(orders.oid)` expression is expressible today as a top-level metric named `order_count`, referenced by bare name.

**Two known intentional divergences:**

- **Single-step vs nested cross-grain (1:N).** Snowflake Semantic Views require **explicit nested aggregation for every cross-grain aggregate**. The Foundation, following the cross-vendor majority (Looker, Tableau, dbt-semantic-layer), accepts single-step cross-grain over `1 : N` edges with standard-SQL semantics. The Foundation does not currently accept the nested form at all (it raises `E_NESTED_AGGREGATION_DEFERRED`, §4.5). For **distributive** aggregates the two forms produce identical numbers, so the porting story is mechanical (rewrite Snowflake's nested form as the Foundation's single-step). For **non-distributive** aggregates (`AVG`, `MEDIAN`, etc.), Snowflake's nested form gives the per-home-row-first answer; the Foundation's single-step gives the heavy-side-weighted answer. The unweighted form waits for §10's grain-aware functions.

- **Cross-grain non-distributive over `N : N`.** Snowflake supports this only in the nested form `AVG(AVG(...))`, which gives the per-home-row-first answer. The Foundation accepts the bare single-step form (`AVG(movies.gross)`) and resolves it via the bridge-de-duplication construction of §6.8.1, giving the heavy-side-weighted answer (D-027) — the analogue of the 1:N rule above. The two engines agree on the result for distributive aggregates over an N:N edge and disagree for non-distributive aggregates because they pick different default interpretations: Foundation's bare form is the bridge-dedup answer, Snowflake's nested form is the per-home-row-first answer. The Foundation's per-home-row-first interpretation (the same number Snowflake produces) requires the nested form `AVG(AVG(...))`, which is **deferred** to §10's grain-aware-functions proposal and currently raises `E_NESTED_AGGREGATION_DEFERRED`. Until §10 lands, a model that needs Snowflake's per-home-row-first number on an N:N edge cannot express it in the Foundation; a model that needs the bridge-dedup number has only the Foundation form.

Snowflake's vendor-named clauses for cross-grain aggregation collapse, in the Foundation, to a single rule: write the aggregation as a model-scoped metric (§4.5 form 1). For every query expressible in both, the numbers agree.

#### 12.A.2 Differences OSI Would Resolve

These are places where Snowflake Semantic Views diverges from what the Foundation requires.

##### (a) Dimension-only cardinality divergence — Errata #2, #3

**Observed:** `SELECT * FROM SEMANTIC_VIEW(sv DIMENSIONS c.segment)` returns 2 rows (deduplicated). `SELECT segment FROM sv` returns 3 rows (customer granularity). `SELECT status FROM sv` returns 6 rows. `SELECT segment, status FROM sv` returns 6 rows.

**Foundation position:** Under §5.1's two-shape model, these are different queries with different correct answers — `DIMENSIONS c.segment` is an **aggregation query** (returns 2 rows, the distinct tuple), while `SELECT segment FROM sv` (no `GROUP BY`, no aggregates) is a **scalar query** (returns 3 rows at customer grain). Both results are right *for their shape*. The real bug is that Snowflake's bare-SQL surface offers no clear signal to the user about which shape they're getting; the same syntactic shape (`SELECT col FROM view`) silently produces aggregation-shaped or scalar-shaped output depending on the view definition.

**Resolution:** A SQL surface for OSI MUST commit to the §5.1 mapping — `SELECT` with `GROUP BY` (or with aggregates in the projection) is an aggregation query; `SELECT` with neither is a scalar query. Under that rule, `SELECT segment FROM sv` is unambiguously a scalar query, and the 3-row result is correct. The `SEMANTIC_VIEW(... DIMENSIONS ...)` form remains a convenience for explicitly declaring an aggregation query. The two surfaces are equivalent only when the user expresses both in the same shape.

##### (b) Per-aggregate native granularity in single SELECT — Errata #1

**Observed:** `SELECT AGG(order_count), COUNT(segment) FROM sv` returns `(6, 3)` — the first aggregate resolves at order granularity (6 orders), the second at customer granularity (3 customers), in one row of output.

**Foundation position:** Within an **aggregation query**, all measures in a single query resolve at the query's grain — the empty grain when no dimensions are listed (§5.1.1, §6.2). A result with two scalars computed on different row sets violates the mental model of an aggregation `SELECT` without `GROUP BY` and is the single most surprising behavior in Snowflake Semantic Views. (A scalar query (§5.1.2) is *not* what is happening here: `AGG(...)` and `COUNT(...)` are aggregates, so this is unambiguously an aggregation query, and the engine should compute both at the empty grain.)

**Resolution:** When the query is an aggregation query with no dimensions, all measures resolve at the empty grain — a single fully-aggregated row over the joined row set, with the aggregate-before-join and chasm-safe rewrites of §6.7. Authors who want measures pinned to a source-table's native granularity should declare that explicitly — which is what the deferred `grain: FIXED` extension (§10) is for. Today this is implicit and silent; it should be explicit and opt-in.

##### (c) `COUNT(*)` not supported — Errata #4

**Observed:** `SELECT COUNT(*) FROM sv` raises `ERROR 010274: Invalid metric expression 'COUNT(*)'`.

**Foundation position:** `COUNT(*)` is required by the Foundation's expression subset (see [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md)) because it is the most universal SQL aggregate.

**Resolution:** Either support `COUNT(*)` directly, or transparently rewrite `COUNT(*)` to `COUNT(<resolved_dataset>.<primary_key>)` at query time. Rejecting it with an error is user-hostile.

##### (d) `SELECT *` fails — Errata #6

**Observed:** `SELECT * FROM sv` raises `ERROR 010236: Requested semantic expression in FACTS clause must be one of: (DIMENSION, FACT)`.

**Foundation position:** The Foundation takes no explicit position on `SELECT *` — it is a SQL-surface convenience, not a semantic concept. But if the bare-SQL surface exists at all (§12.A.2.a), its failure modes should be comprehensible.

**Resolution:** Either support `SELECT *` by expanding it to all public dimensions at the common grain, or produce an error message that explains the restriction ("Semantic views require explicit dimensions and/or `AGG()`-wrapped metrics; `*` cannot expand to both") rather than the current metadata-internals message.

##### (e) Outer WHERE uses result aliases not semantic names — Errata #10

**Observed:** An outer `WHERE c.segment = 'Ent'` on a semantic-view subquery raises `invalid identifier 'C.SEGMENT'` because the outer scope only sees the flattened result alias `SEGMENT`.

**Foundation position:** The OSI Semantic Query is flat — there is no inner/outer split. `Where` and `Having` run in the same namespace as `Dimensions` and `Measures`, which is the dataset-qualified semantic namespace.

**Resolution:** When a semantic view is consumed through a subquery surface, the inner-scope semantic names should still be addressable, or a clear error explaining the namespace change should be raised. This is largely a SQL-dialect concern, but it interacts with the same-named expressions issue (§12.A.2.f).

##### (f) Same-named expressions unreachable in bare SQL — Errata #16, #17

**Observed:** A semantic view with `customers.name` and `orders.name` accepts both through `SELECT * FROM SEMANTIC_VIEW(sv DIMENSIONS c.name, o.name)` (produces duplicate `NAME` column headers). But `SELECT name FROM sv`, `SELECT c.name FROM sv`, and `SELECT sv.name FROM sv` all fail with `invalid identifier`. Same problem for same-named metrics.

**Foundation position:** SQL itself permits duplicate column names in a result set; the Foundation does the same. The bare-SQL surface should accept `dataset.field` qualification (§4.6.4) and let the caller alias when the result is consumed by tooling that cannot tolerate duplicates.

**Resolution:** Allow `customers.name` and `orders.name` both to appear in a result; recommend that callers add explicit aliases (`AS customer_name`, `AS order_name`) when the consuming surface cannot disambiguate. The bare-SQL error today is more restrictive than it needs to be.

##### (g) Granularity error message — Errata #13 (presentation)

**Observed:** `SELECT status, COUNT(customer_name) FROM sv GROUP BY status` raises `ERROR 010234: dimension entity 'O' must be ... lower granularity ... than 'C'`. The message leaks internal entity codes.

**Foundation position:** Not a semantic correctness issue — the rejection is defensible under §6.7 (aggregate-before-join). But the error message is opaque.

**Resolution:** Reword the error to reference the author-visible dataset and column names, not internal entity IDs: "COUNT(customer_name) would fan out under GROUP BY orders.status because orders is at a finer grain than customers. Either group by a customers-level dimension, or use COUNT(DISTINCT customer_name)."

##### (h) FACTS and METRICS in the same query — Errata #8

**Observed:** `SELECT * FROM SEMANTIC_VIEW(sv FACTS o.amount METRICS o.order_count)` raises `ERROR 010268: Facts and metrics cannot be requested in the same query`.

**Foundation position:** The Foundation has no FACTS clause — there is only `Dimensions` and `Measures` (aggregation queries) and `Fields` (scalar queries). Row-level facts a user wants exposed as raw rows belong in `Fields` of a scalar query; aggregations over those facts belong in `Measures` of an aggregation query. The two shapes cannot mix in one query — the §5.1 contract makes this an `E_MIXED_QUERY_SHAPE` error rather than the Snowflake FACTS-vs-METRICS error.

**Resolution:** When OSI's Foundation surface is adopted, the split disappears. Snowflake's FACTS clause remains as a dialect-specific convenience, but it is not part of OSI conformance.

##### (i) LIMIT inside the clause — Errata #9

**Observed:** `LIMIT 2` inside a `SEMANTIC_VIEW(...)` clause raises a syntax error. LIMIT must be on the outer query.

**Foundation position:** `Limit` is a first-class clause of the Semantic Query (§5.1). Whether the SQL surface accepts it inside or outside a `SEMANTIC_VIEW()` wrapper is a dialect decision.

**Resolution:** Support inner `LIMIT` in `SEMANTIC_VIEW()` clauses for ergonomic parity, or document the restriction prominently. The current error ("syntax error ... unexpected '2'") does not help the user.

### 12.B Databricks Metric Views

Databricks Metric Views are a YAML-defined semantic layer over Unity Catalog tables. Their data model is similar to OSI in spirit (dimensions, measures, joins) but the surface and several behaviors differ.

**Sources:**
- [Model metric views](https://docs.databricks.com/aws/en/metric-views/data-modeling/joins) (Apr 2026)
- [Use level of detail (LOD) expressions in metric views](https://docs.databricks.com/aws/en/metric-views/data-modeling/level-of-detail) (Apr 2026)
- [Query metric views (GCP)](https://docs.databricks.com/gcp/en/business-semantics/metric-views/query)
- Community thread on metric-view-on-joined-table behavior: [databricks-community-141694](https://community.databricks.com/t5/warehousing-analytics/metric-view-measure-on-joined-table/td-p/141694)

#### 12.B.1 Behaviors that align with the Foundation

| Behavior | Foundation reference | Source |
|:---|:---|:---|
| Default join is `LEFT OUTER JOIN` from the source (fact) table to dimension tables | §6.6 (LEFT for enrichment) | Joins doc, §"Model star schemas": "the source is the fact table and joins with one or more dimension tables using a LEFT OUTER JOIN." |
| All measures in a single query resolve at the query's grain (no per-aggregate "native granularity" leak) | §6.2 | Inferred from the docs: measures are aggregations over the source query, with no per-measure grain override. The deferred LOD feature is the explicit opt-out. |
| Filters declared in the metric-view YAML apply to all queries that reference it | (Foundation has no direct equivalent yet — see §10 deferred named filters) | Joins doc, §"Apply filters" |
| Multi-hop joins (snowflake schemas, e.g., `orders → customers → nation → region`) supported | §6.9 (path resolution) | Joins doc, §"Model snowflake schemas" |
| Composability via metric-view-as-source for derived metrics | §4.5 form (2) and (3) | Joins doc, §"Use a metric view as a source" |

#### 12.B.2 Differences

| # | Behavior | Foundation position |
|:---:|:---|:---|
| B-1 | **M:N join silently picks first match.** The docs say: "The join should follow a many-to-one relationship. In cases of many-to-many, the first matching row from the joined dimension table is selected." (Joins doc, §"Model star schemas") | The Foundation rejects silent first-match selection on `N : N`. Foundation §6.8 requires either a bridge dataset or a shared-dimension stitch; if neither applies, the engine MUST raise `E3012_MN_NO_SAFE_REWRITE`. Picking "the first matching row" is exactly the silent-wrong-answer pattern Semantic 5 (§6.1) prohibits. |
| B-2 | **Metric views cannot be directly joined to other tables at query time.** Users must wrap the metric view in a CTE and then join the CTE result. (Community thread 141694; query doc) | Foundation has no equivalent restriction — semantic queries always run inside the layer, and joins to "other tables" are not part of the semantic surface. This is a Databricks-surface concern, not a Foundation conformance issue. |
| B-3 | **Cross-grain references inside a measure expression.** Databricks does not document an implicit "aggregate down to home grain" rule for fields. Cross-table aggregation is expressed by writing a `JOIN` in a SQL-source query or by defining a measure that aggregates a joined column. | Foundation matches: aggregates never live in field expressions (§4.3, `E_AGGREGATE_IN_FIELD`); all aggregates are model-scoped metrics (§4.5 form 1), resolved at the query's grain via the relationship graph (§6.9). |
| B-4 | **`COUNT(*)` support** | `Unknown` from public docs as of fetch date. Examples in the joins doc use `COUNT(1)` and `COUNT(o_orderkey)`. The aggregate function reference (linked from the docs) does include `COUNT(*)`, so it likely works in source-query SQL; whether it is accepted as a measure expression directly is not explicitly stated. |
| B-5 | **Dimension-only / scalar query shape (no metrics)** | `Unknown`. The query doc focuses on dimension + measure interactions; whether a query for "just dimension columns" yields one row per source row (scalar) or distinct combinations (aggregation) is not directly documented in the public material I located. Foundation §5.1 distinguishes the two shapes explicitly. |
| B-6 | **Same-named expressions across joined tables** | `Unknown`. The docs use unique names in their examples; whether the surface preserves `dataset.field` qualification through the result header is not directly addressed. Foundation §4.6 requires reachability via `dataset.field`. |

The deferred Foundation features for `FIXED` / `INCLUDE` / `EXCLUDE` grain modes and resettable filters (§10) correspond closely to Databricks Metric Views' "Fixed level of detail" and "Coarser level of detail" expressions (LOD doc). When OSI adopts those, alignment with Databricks should be re-evaluated against the LOD surface.

### 12.C Google Looker (LookML)

Looker is the longest-lived public-cloud semantic layer in this comparison and has a distinctive correctness mechanism: **symmetric aggregates** (a hash-based `SUM(DISTINCT)` rewrite) that automatically de-duplicates joined rows under fan-out conditions. The Foundation reaches the same correctness via a different mechanism (pre-aggregated CTEs, §6.7); both produce correct results, and the Foundation lists symmetric aggregates as a future codegen optimization (§10).

**Sources:**
- [Understanding symmetric aggregates](https://cloud.google.com/looker/docs/best-practices/understanding-symmetric-aggregates)
- [Getting the relationship parameter right](https://cloud.google.com/looker/docs/best-practices/how-to-use-the-relationship-parameter-correctly)
- [`symmetric_aggregates` parameter](https://cloud.google.com/looker/docs/reference/param-explore-symmetric-aggregates)
- [Working with joins in LookML](https://docs.cloud.google.com/looker/docs/working-with-joins)

#### 12.C.1 Behaviors that align with the Foundation

| Behavior | Foundation reference | Source |
|:---|:---|:---|
| Default outer join shape preserves base-table rows (Looker default is `LEFT JOIN` from the explore's base view) | §6.6 (LEFT for enrichment) | "Working with joins in LookML" |
| Cardinality is declared per-join via `relationship: many_to_one` / `one_to_many` / `one_to_one` / `many_to_many`, with primary keys driving correctness | §6.4 (cardinality inference from PK / UK) | "Getting the relationship parameter right" |
| Primary keys are required for measures to participate in joins (otherwise measures don't appear) | §4.2: PK / UK declarations matter for safety | "Getting the relationship parameter right", §"How does Looker help me?" |
| `many_to_many` is allowed and produces correct numbers (via symmetric aggregates), not silently-inflated ones | §6.8 semantic guarantee: results equivalent to one of the safe rewrites | "Understanding symmetric aggregates" |
| Outer joins with right-side fanout require treating the relationship as `many_to_many` to enable symmetric aggregates | §6.7 fan-out safety | "Getting the relationship parameter right", §"Outer joins" |

The user-visible semantic that maps most cleanly: Looker's symmetric aggregates and the Foundation's pre-aggregated CTEs are two different physical realizations of **Semantic 2** (no row of A contributes more than once to a measure on A). They are exchangeable as long as both produce the same row counts and totals.

#### 12.C.2 Differences

| # | Behavior | Foundation position |
|:---:|:---|:---|
| L-1 | **`many_to_many` resolved automatically via symmetric aggregates** with no error and no required bridge dataset, provided primary keys are correct on both sides. | Foundation §6.8 requires `N : N` resolution via bridge or stitch and fails with `E3012` when neither is available. **Looker is more permissive** — it always produces correct numbers given correct keys, by paying the symmetric-aggregates SQL cost. The Foundation could subsume Looker's behavior by adopting symmetric aggregates as an additional permitted safe rewrite under §6.8, which is queued as a codegen optimization (§10). Until then, an OSI-Looker bridge would translate Looker's `relationship: many_to_many` into an explicit bridge dataset (when used to traverse) or report `E3012` and require the modeler to choose. (A semi-join filter form is deferred to a future proposal — see §6.8.) |
| L-2 | **Measure types include non-aggregate "post-SQL" types** (`number`, `yesno`) computed after SQL generation. | Foundation has no post-SQL computation tier. Such measures translate to scalar fields or to ratio-of-metrics arithmetic per §4.5 form (2). |
| L-3 | **`symmetric_aggregates: no` opt-out** disables the correctness mechanism; the modeler is then responsible for avoiding fanout. | Foundation has no equivalent escape hatch — the engine MUST either produce a result equivalent to one of the §6.8 safe rewrites or raise a typed error. A model translated from a Looker explore with `symmetric_aggregates: no` should be rejected on import unless every join used in queries has cardinality `N : 1` or `1 : 1`. |
| L-4 | **Default `outer_only` join type / explicit `join_type`** | Looker exposes `join_type: inner` / `left_outer` / `full_outer` / `cross`. Foundation does not expose per-join `type:` overrides at the Foundation level (§6.9 deferred). A Looker explore with `join_type: inner` translates to a Foundation model that documents the RI assumption in `description:` and, when the deferred RI proposal lands (§10), declares `from_all_rows_match: true` to enable the same `INNER` promotion. |
| L-5 | **Same-named fields across views** | `Unknown` how Looker handles name collisions in the result header. The docs I located describe `view.field` qualification in expressions but do not directly address the result-set column header collision case (the equivalent of Snowflake errata #16/#17). Foundation §4.6 requires reachability via `dataset.field`. |
| L-6 | **Path / explore disambiguation across multiple joins to the same view** | `Unknown` from the sources I located in this pass. Looker's `from:` clause and join naming likely cover the equivalent of OSI's path-disambiguation problem; this should be revisited when OSI's deferred path-disambiguation proposal (§10) is drafted. |

### 12.D Window-Function Behaviors

Snowflake errata #5, #18 (caveat row), #19, #20, #21, #22, #23, #24, #25 all concern window functions, `QUALIFY`, `LAG`/`LEAD`, compound window expressions, and HAVING-vs-window execution order. Windows are part of the Foundation (§6.10), so each of these items has an explicit position:

| Errata | Snowflake behavior | Foundation position |
|:---|:---|:---|
| **#5** (window placement in HAVING vs ORDER BY) | Some windows allowed in `HAVING` and `ORDER BY` only when wrapped a particular way. | Foundation §6.10.1 follows standard SQL — windows MAY appear in `Measures`, `Fields`, `Order By`, and `Having`. No special wrapping required. |
| **#19** (`LAG`/`LEAD` skip filtered rows) | `LAG` operates on the post-`WHERE` row order, so a filter that removes February rows makes January's `LAG` see December, not "the previous month." | Foundation §6.10 follows standard SQL — `LAG`/`LEAD` see the post-`Where` row order. Users wanting time-series gap-filling must densify their dimension (a future proposal — see §10 deferred). The Foundation does not silently rewrite `LAG`/`LEAD` semantics. |
| **#25** (compound window bypasses WHERE) | `MAX(SUM(fact) OVER ())` computes the inner window over the unfiltered dataset, producing wrong "% of total" under any filter. | Foundation §6.10.3 requires the opposite: every window computation MUST see the query's pre-aggregation `Where`. This is a genuine Snowflake bug; the Foundation specifies the correct behaviour. |
| **#20–#24** (various `QUALIFY`, frame-mode, and ordered-aggregate edge cases) | Snowflake-specific surface quirks. | The Foundation has no `QUALIFY` clause — top-N-within-group queries express the same shape with a `Where` on a windowed field (§6.10.4). `GROUPS` frame mode and ordered-set aggregates (`WITHIN GROUP`) remain deferred (§10). |

The Snowflake errata that report wrong numbers (`#25`) are exactly the gotchas the Foundation's §6.10 semantic contract is designed to prevent. The errata that report dialect-specific *surface* differences (`#5`, `#18`, `#20–#24`) are not correctness issues; the Foundation picks the standard-SQL surface.

---

## Appendix A: Join Behavior — Worked Examples

This appendix is a decision tree for query authors. Given a shape of query (what datasets, what measures, what filters), it tells you the shape of the result you should expect — and which of the §6.1 user-visible semantics is producing it.

Each example is tagged with the model it uses. Most examples use the **Prelude model** below. Examples that need a different shape (M:N through a bridge, ambiguous paths, disconnected datasets) declare a small **mini-model** inline.

### Prelude model

```yaml
# Used by Examples 1–4, 8, 11, 12.
datasets:
  - name: customers
    primary_key: [id]
    fields:
      - { name: id }
      - { name: region }
      - { name: segment }

  - name: orders
    primary_key: [id]
    fields:
      - { name: id }
      - { name: customer_id }
      - { name: amount }

  - name: returns
    primary_key: [id]
    fields:
      - { name: id }
      - { name: customer_id }
      - { name: amount }

  - name: premium_customers      # subset of customers, used for filters
    primary_key: [id]
    fields:
      - { name: id }

relationships:
  - { name: orders_to_customer,  from: orders,  to: customers, from_columns: [customer_id], to_columns: [id] }   # N:1
  - { name: returns_to_customer, from: returns, to: customers, from_columns: [customer_id], to_columns: [id] }   # N:1
```

### Mini-model M1 — Disconnected datasets (Example 5, Example 10)

```yaml
datasets:
  - name: orders               # same shape as Prelude
    primary_key: [id]
    fields: [{ name: id }, { name: customer_id }, { name: amount }]

  - name: inventory_movements  # tracks stock movements; no FK into customers/orders
    primary_key: [movement_id]
    fields:
      - { name: movement_id }
      - { name: warehouse_id }
      - { name: quantity }

# No relationship between orders and inventory_movements.
```

### Mini-model M2 — N:N through a bridge (Example 6 negative case, Example 7)

```yaml
datasets:
  - name: actors
    primary_key: [actor_id]
    fields: [{ name: actor_id }, { name: height }]

  - name: movies
    primary_key: [movie_id]
    fields: [{ name: movie_id }, { name: gross }]

  - name: appearances        # bridge with N:1 edges to both endpoints
    primary_key: [actor_id, movie_id]
    fields: [{ name: actor_id }, { name: movie_id }]

relationships:
  - { name: app_to_actor, from: appearances, to: actors, from_columns: [actor_id], to_columns: [actor_id] }   # N:1
  - { name: app_to_movie, from: appearances, to: movies, from_columns: [movie_id], to_columns: [movie_id] }   # N:1

# A second variant of M2 ("M2-no-bridge") drops the `appearances` dataset and
# declares an N:N relationship directly between `actors` and `movies` with no
# stitching dimension at the requested grain. That is what Example 6 references.
```

### Mini-model M3 — Two paths between the same datasets (Example 9)

```yaml
datasets:
  - name: orders
    primary_key: [id]
    fields:
      - { name: id }
      - { name: placed_by_id }
      - { name: fulfilled_by_id }

  - name: users
    primary_key: [id]
    fields: [{ name: id }, { name: region }]

relationships:
  - { name: order_placed_by,    from: orders, to: users, from_columns: [placed_by_id],    to_columns: [id] }
  - { name: order_fulfilled_by, from: orders, to: users, from_columns: [fulfilled_by_id], to_columns: [id] }
```

### Quick-reference table

| # | Model | Query you write | Result shape | Semantic | Notes |
|:---:|:---|:---|:---|:---:|:---|
| 1 | Prelude | Aggregation: measure from `orders`, group by `customers.segment` | All `orders` represented; orphan orders bucketed under `segment = NULL` | 1 | `LEFT` enrichment from `orders → customers` |
| 2 | Prelude | Aggregation: `COUNT(customers)` and `SUM(orders.amount)` grouped by `customers.region` | `COUNT(customers)` is per region (not inflated by order count); `SUM(amount)` is total per region | 2 | Auto pre-aggregation on the many-side |
| 3 | Prelude | Aggregation: `SUM(orders.amount)` and `SUM(returns.amount)` grouped by `customers.region` | Every region appearing in *either* `orders` or `returns` is in the result; missing side is `NULL` (standard SQL, §6.11) | 3 | `FULL OUTER` between two pre-aggregated facts |
| 4 | Prelude | Aggregation: `SUM(orders.amount)` and `SUM(returns.amount)` with **no** dimensions | One scalar row per side, joined via `CROSS JOIN` | 3 | Scalar grand total — no shared grain |
| 5 | Mini-model M1 | Aggregation: measures from two unrelated facts (no shared dim) | **Error `E3013_NO_STITCHING_DIMENSION`** | 5 | Refuses to emit a Cartesian product |
| 6 | Mini-model M2-no-bridge | Aggregation across an `N : N` edge with no bridge and no shared dim | **Error `E3012_MN_NO_SAFE_REWRITE`** | 5 | Engine has no safe rewrite |
| 7 | Mini-model M2 | Aggregation through an `N : N` edge resolved by a declared bridge dataset | Result is correct — engine walks two `N : 1` enrichments through the bridge | 2, 5 | §6.8.1 |
| 8 | — | Semi-join filtering — *deferred*. The shape is intentionally not part of the Foundation today; a follow-up proposal will define it in full. | — | — | See Example 8 in the body. |
| 9 | Mini-model M3 | Aggregation referencing two equally-valid relationship paths between datasets | **Error `E_AMBIGUOUS_PATH`** | 5 | Path-disambiguation hints deferred (§10) |
| 10 | Mini-model M1 | Aggregation referencing datasets with no relationship path | **Error `E_NO_PATH`** | 5 | |
| 11 | Prelude | Aggregation with no dimensions: `SUM(orders.amount)` only | A single row, fully aggregated | 2 | All measures resolve at the empty grain |
| 12 | Prelude | Scalar query: `Fields: [orders.id, orders.customer_id, customers.region]` | One row per `orders` row; orphan orders show `region = NULL` | 1 | Scalar shape per §5.1 |

### Example 1 — Semantic 1: orphan facts surface as NULL dims

**Uses:** Prelude.

**Query (sketch):**

```yaml
Measures:
  - SUM(orders.amount) AS revenue
Dimensions:
  - customers.segment
```

**What you get:**

| segment | revenue |
|:---|---:|
| Enterprise | 1,200,000 |
| SMB | 380,000 |
| `NULL` | 14,500 |

The `NULL` segment row is real revenue from `orders` whose `customer_id` did not resolve to a `customers` row. **Semantic 1**: the engine never silently drops those orders.

If you don't want them in your result, add `Where: customers.segment IS NOT NULL`.

### Example 2 — Semantic 2: no fan-out double-counting

**Uses:** Prelude.

**Query:**

```yaml
Measures:
  - COUNT(customers.id) AS num_customers
  - SUM(orders.amount) AS revenue
Dimensions:
  - customers.region
```

**What you get:**

| region | num_customers | revenue |
|:---|---:|---:|
| EAST | 412 | 980,000 |
| WEST | 305 | 760,000 |

Each customer is counted **once per region**, not once per order. The engine emits a pre-aggregated CTE at the `customers` grain and joins it back, exactly as in §6.7's worked SQL.

### Example 3 — Semantic 3: no fact loses its groups

**Uses:** Prelude.

**Query:**

```yaml
Measures:
  - SUM(orders.amount) AS revenue
  - SUM(returns.amount) AS returns_total
Dimensions:
  - customers.region
```

**What you get:**

| region | revenue | returns_total |
|:---|---:|---:|
| EAST | 980,000 | 12,400 |
| WEST | 760,000 | `NULL` |
| NORTH | `NULL` | 3,200 |

`WEST` had revenue but no returns; `NORTH` had returns but no revenue. **Semantic 3**: every region that appeared in *either* fact is in the result. The `NULL` in the missing-fact cells is standard SQL behaviour for `SUM` over an empty input row set (§6.11).

If your metric should report `0` instead of `NULL`, define it with `COALESCE(SUM(...), 0)` — that's a per-metric authoring decision, not a join-semantics concern.

### Example 4 — Multi-fact, no dimensions (scalar grand totals)

**Uses:** Prelude.

**Query:**

```yaml
Measures:
  - SUM(orders.amount) AS revenue
  - SUM(returns.amount) AS returns_total
# no Dimensions
```

**What you get:** one row.

| revenue | returns_total |
|---:|---:|
| 1,594,500 | 15,600 |

There's no shared grain to align on, so the engine produces one scalar per side and `CROSS JOIN`s them — degenerate but correct (Semantic 3 still holds: neither side is dropped).

### Example 5 — Two unrelated facts (error)

**Uses:** Mini-model M1.

**Query:** `SUM(orders.amount)` and `SUM(inventory_movements.quantity)` with no dimensions. The two facts share no dimension and have no relationship path between them.

**What you get:**

```
E3013_NO_STITCHING_DIMENSION:
  Cannot combine measures from `orders` and `inventory_movements` — no
  shared dimension and no relationship path exists.
  Suggestions: add a shared dimension that both facts can reach via
  `N : 1`, or query each fact separately.
```

**Semantic 5** in action: the engine refuses to emit a Cartesian product.

### Example 6 — Aggregating across `N : N` with no safe rewrite (error)

**Uses:** Mini-model M2-no-bridge (the variant with a direct `N : N` between `actors` and `movies`, no `appearances` bridge, and no shared dimension at the requested grain).

**Query:** `AVG(movies.gross)` grouped by `actors.height`.

**What you get:**

```
E3012_MN_NO_SAFE_REWRITE:
  Cannot resolve the M:N traversal between `actors` and `movies` at the
  query's grain.
  Suggestions:
    - introduce a bridge dataset with N:1 edges to both sides (§6.8.1), or
    - group by a dimension reachable from both sides (§6.8.2 stitch).
  (A semi-join filter form is deferred — see the §6.8 deferred-feature note.)
```

**Semantic 5** in action: no escape hatch, no plausible-but-wrong number — a typed error with corrective guidance.

### Example 7 — `N : N` resolved through a bridge

**Uses:** Mini-model M2 (with the `appearances` bridge).

**Query:** `SUM(movies.gross)` grouped by `actors.height`. Because the `appearances` bridge has `N : 1` edges to both endpoints, the engine walks the bridge to materialize the distinct `(movie, height)` associations and aggregates over that set — each movie contributes to each height at most once, even if multiple actors of that height appeared in it. See §6.8.1 for the full worked plan and intermediate tables.

This is **Semantic 2** and **Semantic 5** working together: each row of `movies` contributes once per group (Semantic 2), and the engine only does so because the bridge gives it a safe rewrite. A bare `AVG(movies.gross)` over the same bridge resolves through the same construction (D-027): `170 → AVG(100, 200) = 150`, `180 → AVG(50) = 50`. The aggregate runs once over the de-duplicated row set, so every aggregate category (distributive, algebraic, holistic) is well-defined here. The alternative per-actor-first interpretation is the nested form `AVG(AVG(movies.gross))` and is deferred to §10 (`E_NESTED_AGGREGATION_DEFERRED`).

### Example 8 — Semi-join filtering (deferred)

The "filtering join" shape — restricting rows to those whose key
appears in a sibling dataset — is intentionally deferred from this
proposal. It will be specified in full in a separate follow-up
proposal that pins the semi-join surface (the keyword(s),
NULL-safety, `NOT`-form, and compilation contract). The Foundation
does not define `EXISTS_IN` today.

A model that needs cross-fact filtering in the meantime should
express it via an aggregation step: produce the filter set as a
dimension, then filter on the dimension. (For example, to restrict
to "orders by premium customers", join `customers` and filter on a
row-level field `customers.is_premium` rather than referencing
`premium_customers` directly.)

### Example 9 — Ambiguous path (error)

**Uses:** Mini-model M3.

**Query:** `COUNT(orders.id)` grouped by `users.region`. `orders` has two relationships to `users` (`placed_by` and `fulfilled_by`); the engine cannot decide which path to take.

**What you get:**

```
E_AMBIGUOUS_PATH:
  Multiple paths from `orders` to `users` exist:
    - order_placed_by    (orders.placed_by_id → users.id)
    - order_fulfilled_by (orders.fulfilled_by_id → users.id)
  Disambiguation hints (`using_relationships`) are deferred (§10).
```

**Semantic 5**: the engine refuses to silently pick one.

### Example 10 — Disconnected datasets (error)

**Uses:** Mini-model M1.

**Query:** any aggregation referencing both `orders` and `inventory_movements` (e.g., grouping `SUM(orders.amount)` by an `inventory_movements.warehouse_id` — there is no shared key, and a semi-join filter form is deferred — see §6.8).

**What you get:**

```
E_NO_PATH:
  No relationship path connects `orders` and `inventory_movements`.
```

### Example 11 — Aggregation with no dimensions

**Uses:** Prelude.

```yaml
Measures:
  - SUM(orders.amount) AS revenue
# no Dimensions
```

A single row. All measures resolve at the empty grain over the joined row set, with the aggregate-before-join and chasm-safe rewrites of §6.7 still applied. Semantic 2 still holds.

### Example 12 — Scalar query

**Uses:** Prelude.

```yaml
Fields:
  - orders.id
  - orders.customer_id
  - customers.region
# no Dimensions, no Measures, no aggregation
```

One row per `orders` row, joined to `customers` along the `N : 1` path. Same Semantic 1 applies: orders with no matching customer appear with `region = NULL`.

### What this appendix does *not* cover

These are deferred features (§10) and produce errors today, not user-controllable behavior:

- Per-metric `joins.type` overrides (`INNER`, `LEFT`, `FULL`)
- Declared referential integrity (`from_all_rows_match` / `to_all_rows_match`) that lets the engine promote `LEFT` to `INNER`
- Path-disambiguation hints (`using_relationships`)
- Non-equijoin, ASOF, and range relationships
- The per-metric `grain: FIXED` extension and other explicit grain overrides
- Parameterized window frame bounds, `GROUPS` frame mode, and windowed-metric composition (standard SQL windows themselves are part of the Foundation — see §6.10)

When any of those land, the table at the top of this appendix grows; the five semantics in §6.1 do not change.

---

## Appendix B: Foundation Conformance Decisions

This appendix catalogs the foundational decisions made in this spec — including the refinements and ambiguity squashes from the latest revision pass — and pairs each one with a "test shape" so the compliance suite (§11.1) has a normative anchor for what to verify. It is intended to grow: future revisions add rows, never delete them silently. When a decision is superseded, the row is kept and marked `superseded_by: <new-id>`.

Each row is a small contract:

- **ID** — stable handle (`D-NNN`). Cited from compliance test names (e.g. `compliance/round1/easy/D-001-cardinality-distinct-dimensions.yaml`).
- **Decision** — one-line summary of the rule.
- **Anchored in** — the §-reference in this spec where the rule is defined.
- **Test shape** — the form of compliance test that exercises the decision. Tests assert on observable behavior (rows or error code), not internal plan shape (§11.1).

| ID | Decision | Anchored in | Test shape |
|:---|:---|:---|:---|
| **D-001** | Aggregation-query result cardinality is the distinct tuple of `Dimensions` values **reachable through the measure's join path** plus, where applicable, a `NULL`-key row for unmatched fact rows. Empty `Dimensions` ⇒ exactly one row (the empty grain). Multi-measure queries follow §6.6 row 3 (FULL OUTER stitch on shared dimensions) when the measures have **incompatible fact roots** — each measure independently produces a row set per the rule above, and the row sets are stitched so neither side loses groups. When all measures share a fact root through `N : 1` ancestors, the FULL OUTER stitch is mathematically equivalent to a single-path aggregation (both branches see the same fact rows joined to the same dimensions), and engines MAY emit either plan. | §5.1.1, §6.2, §6.6 | Single-measure: model with `customers ⋈ orders`, query `Dimensions: [customers.region]; Measures: [SUM(orders.amount)]` ⇒ one row per distinct `region` reached by an `orders` row, plus a `NULL`-region row if any `orders` row's `customer_id` does not match a `customer`. A region with no orders (e.g. NORTH if no customer in NORTH has an order) does NOT appear. Multi-measure, same root: `Measures: [SUM(orders.amount), SUM(orders.discount)]` over the same model ⇒ identical row set to the single-measure case (both measures share `orders` as their fact root). Multi-measure, incompatible roots: `Measures: [SUM(orders.amount), SUM(returns.amount)]` ⇒ every region present in *either* the orders branch or the returns branch appears (FULL OUTER stitch, §6.6 row 3). |
| **D-002** | All measures in one aggregation query resolve at the query's grain (no per-aggregate "native granularity" leak). | §6.2 | Two measures from different facts in one query without dimensions ⇒ exactly one row, both measures aggregated over their joined row sets at the empty grain. |
| ~~**D-003**~~ | **Deferred — moved to a separate proposal.** Aggregate-bodied field expressions (whether same-grain over the home dataset's own columns, like `orders.fields: [{name: total_revenue, expression: SUM(amount)}]`, or cross-grain via a `1 : N` edge, like `customers.fields: [{name: lifetime_value, expression: SUM(orders.amount)}]`) are not part of the Foundation today. A field expression containing any aggregate function MUST be rejected with `E_AGGREGATE_IN_FIELD` (§4.3). All aggregates live in **model-scoped metrics** (§4.5) — the top-level `metrics:` section, referenced by bare name. Field-level aggregation will be re-introduced by §10's grain-aware-functions proposal alongside the filter-context semantics. | §4.3 | (a) Model with field `orders.fields: [{name: total_revenue, expression: SUM(amount)}]` ⇒ `E_AGGREGATE_IN_FIELD` at validation time. Top-level metric `total_revenue = SUM(orders.amount)` ⇒ accepted. (b) Field `customers.fields: [{name: lifetime_value, expression: SUM(orders.amount)}]` ⇒ same code. Top-level metric `lifetime_value = SUM(orders.amount)` ⇒ accepted; `Dimensions: [customers.region, customers.id]; Measures: [lifetime_value]` ⇒ one row per `(region, customer)` pair. |
| **D-004** | Default join is `LEFT` for `N:1` enrichment, `FULL OUTER` for multi-fact composition on shared dims, `CROSS JOIN` for scalar grand totals. | §6.6 | (a) Aggregation joining `orders → customers` ⇒ orphan orders bucketed under `NULL` segment. (b) Two facts grouped on shared dim ⇒ regions appearing in either fact appear in the result. (c) Two facts with no dimensions ⇒ one row, both totals on the same row. |
| **D-005** | Routing of predicates is by **resolved expression shape**, not by any declared tag. The Foundation has no `role:` keyword. The two resolved shapes are **row-level scalar** (a non-aggregate expression over the home dataset's columns or `N : 1` / `1 : 1` enrichments — valid in `Where`, `Dimensions`, `Order By`, and `Fields`) and **query-grain aggregate** (an aggregate in a metric expression that resolves at the query's grain — valid in `Measures`, `Having`, and `Order By` when referenced through a measure). Aggregates only live in metric expressions; field expressions containing any aggregate raise `E_AGGREGATE_IN_FIELD` per D-003. | §4.3, §6.3 | (a) Boolean row-level scalar field used in `Where` ⇒ accepted. (b) Query-grain aggregate inside `Where` ⇒ `E_AGGREGATE_IN_WHERE`. (c) Pure row-level predicate inside `Having` ⇒ `E_NON_AGGREGATE_IN_HAVING`. (d) Mixed-level boolean ⇒ `E_MIXED_PREDICATE_LEVEL`. (e) Field expression containing an aggregate (same-grain or cross-grain) ⇒ `E_AGGREGATE_IN_FIELD` (D-003). |
| **D-006** | Bare-name references in queries resolve to the **global namespace**. Dataset-scoped *fields* use `dataset.field`. Metrics are model-scoped only and always referenced by bare name (the `dataset.metric_name` form is deferred per §4.5). | §4.6 | (a) Query `Measures: [total_revenue]` with no global metric of that name ⇒ `E_NAME_NOT_FOUND`. (b) `Measures: [orders.total_revenue]` ⇒ `E_NAME_NOT_FOUND` (the `dataset.metric` form is not part of the Foundation). (c) `Dimensions: [orders.region]` ⇒ resolves to the field `region` on dataset `orders`. |
| **D-007** | `N : N` traversals MUST produce a result equivalent to a bridge or shared-dimension stitch; otherwise `E3012_MN_NO_SAFE_REWRITE`. (Semi-join filter form is deferred — see §6.8 note.) | §6.8 | (a) Bridge model + cross-grain measure ⇒ correct rows. (b) No bridge, no shared dim ⇒ `E3012`. |
| **D-008** | No per-metric `joins.type` override at the Foundation level; the planner uses only the §6.6 defaults. | §6.6, §6.9 | Model with `joins: { type: INNER }` on a metric ⇒ `E_DEFERRED_KEY_REJECTED`. |
| **D-009** | In default Foundation mode (no extension flags enabled), deferred relationship-level keys (`referential_integrity`, `condition`, `asof`, `range`) MUST be rejected with `E_DEFERRED_KEY_REJECTED` and the diagnostic MUST identify the offending key. Engines MAY accept these keys behind a clearly-named, off-by-default extension flag per §11 — a model that uses such a key is non-portable until the corresponding deferred proposal lands. Top-level keys outside the set defined in §4.1 are simply not OSI; engines MAY reject them as malformed input. | §11 | One test per deferred relationship-level key, each asserting `error.code == E_DEFERRED_KEY_REJECTED` and `error.key == <deferred key>` **with no extension flags set**. Engines that ship an extension flag for a key MUST also pass the same test with the flag off. |
| **D-010** | Aggregation-query vs scalar-query shape is determined by projection clauses; mixing rejected with `E_MIXED_QUERY_SHAPE`. | §5.1 | Query that lists both `Dimensions` and `Fields` ⇒ `E_MIXED_QUERY_SHAPE`. |
| **D-011** | A bare metric reference inside `Fields` is rejected with `E_AGGREGATE_IN_SCALAR_QUERY`. Metrics are model-scoped (referenced by bare name) and resolve at the query's grain; they cannot satisfy a scalar-query's per-home-row contract. | §5.1.2 | (a) `Fields: [revenue]` (a model-scoped metric) ⇒ `E_AGGREGATE_IN_SCALAR_QUERY`. (b) `Fields: [SUM(orders.amount)]` (an inline aggregate) ⇒ same code. |
| **D-012** | Predicate-shape errors: aggregate in `Where` ⇒ `E_AGGREGATE_IN_WHERE`; pure row-level in `Having` ⇒ `E_NON_AGGREGATE_IN_HAVING`; mixed-level boolean ⇒ `E_MIXED_PREDICATE_LEVEL`. | §6.3 | Three independent test cases, one per code. |
| **D-014** | **Per-engine determinism.** A single engine compiling the same `(model, query, dialect)` MUST produce byte-identical SQL across runs. The Foundation does **not** require cross-engine determinism — two compliant engines MAY emit different SQL strings as long as they produce the same observable rows or error codes. The cross-engine portability contract is observable behaviour (§11.1), not SQL text. | §11 | Compile the same `(model, query, dialect)` twice on the same engine ⇒ string-equal SQL output. Engines from different vendors MAY compile to different SQL; the compliance suite asserts only on rows / error codes. |
| ~~**D-015**~~ | **Deferred — moved to a separate proposal.** The compilation-strategy equivalence for field-level cross-grain aggregation (correlated subquery vs `LATERAL` vs pre-aggregated CTE) is moot for the Foundation, since field-level cross-grain aggregation itself is deferred per D-003. The equivalence will be re-introduced alongside §10's grain-aware-functions proposal. | — | — |
| **D-016** | `COUNT(*)` is required and counts rows of the home dataset (or, in ad-hoc `Measures` entries, rows at the query grain). Engines that historically reject `COUNT(*)` MUST provide a transparent rewrite. | §7, [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md) | Query `Measures: [COUNT(*)]` over `orders` with `Where: status = 'completed'` ⇒ count of completed orders, no error. |
| ~~**D-017**~~ | **Deferred — moved to a separate proposal.** Semi-join filtering (`EXISTS_IN` / `NOT EXISTS_IN`) is not part of the Foundation today. A follow-up proposal will pin the surface syntax, NULL-safety guarantees, and compilation contract in full. Until then, the Foundation has no semi-join construct, and the M:N resolution menu (§6.8) is limited to bridge and stitch. | — | — |
| **D-018** | Path resolution: unique path used; multiple paths ⇒ `E_AMBIGUOUS_PATH`; no path ⇒ `E_NO_PATH`; cycles ⇒ engine selects an acyclic sub-path within a single query. | §6.9 | (a) Two relationships from `orders` to `users` + measure on `orders` grouped by `users.region` ⇒ `E_AMBIGUOUS_PATH`. (b) Two unrelated datasets referenced in one query ⇒ `E_NO_PATH`. |
| **D-019** | Reserved names (`GRAIN`, `FILTER`, `QUERY_FILTER`) cannot be used as user-defined identifiers. | §4.6.2 | Model defining a dataset named `FILTER` ⇒ parser rejects with a code-tagged error. |
| **D-020** | A **model-scoped metric** MAY aggregate any reachable dataset over a `1 : N` edge **single-step**; the interpretation is standard SQL semantics (the engine joins the higher-grain rows through the relationship path and aggregates them at the query's grain, satisfying Semantic 2). This holds for every aggregate category — distributive, algebraic, holistic, and sketch-based. The alternative "per-home-row first" interpretation requires nested aggregation, which is deferred per D-027. Cross-grain aggregates over an **`N : N`** edge are governed by D-026 / D-027: every aggregate category resolves via the §6.8.1 bridge-de-duplication construction in a single pass (the heavy-side-weighted analogue of the 1:N rule above). The "per-home-row-first" interpretation requires nested aggregation and is deferred per D-027. Aggregates inside a *field* expression are rejected entirely with `E_AGGREGATE_IN_FIELD` (§4.3); dataset-namespaced metrics (the `dataset.metric_name` form) are also deferred (§4.5). | §4.5 form (1), §6.1 Semantic 2, §6.8 | (a) Top-level metric `total_orders = SUM(orders.amount)` ⇒ accepted single-step; grouped by region, each order contributes once, summed per region. (b) Top-level metric `avg_order_amount = AVG(orders.amount)` ⇒ accepted single-step; grouped by region, this is the SQL average over all orders in the region (heavy-customer-weighted). (c) Top-level metric `avg_of_per_customer_avg = AVG(AVG(orders.amount))` ⇒ `E_NESTED_AGGREGATION_DEFERRED` (the unweighted form waits for §10). (d) Top-level metric `distinct_products = COUNT(DISTINCT orders.product_id)` ⇒ accepted single-step; gives the count of distinct products in the region's orders. (e) `AVG(movies.gross)` across an `actors ↔ appearances ↔ movies` M:N bridge grouped by `actors.height` ⇒ **accepted** per D-027 (single-pass over the bridge-deduped row set: `170 → 150`, `180 → 50`). (f) Field `orders.fields: [{name: total_revenue, expression: SUM(amount)}]` ⇒ `E_AGGREGATE_IN_FIELD`; rewrite as top-level metric `total_revenue = SUM(orders.amount)`. |
| **D-021** | An `expression` slot accepts either a bare string in the model's default dialect or the structured `{ dialects: [{ dialect, expression }, …] }` object. The structured form is normatively defined in `SQL_EXPRESSION_SUBSET.md`. | §4.3 / §4.5 | Field with a string `expression: "amount * 1.1"` ⇒ accepted. Field with a `dialects:` array containing an `OSI_SQL_2026` entry ⇒ accepted. Field with a `dialects:` array missing every dialect the engine recognizes ⇒ engine rejects with a clear error. |
| **D-022** | A non-decomposable aggregate (holistic or unsupported-algebraic) that the engine cannot evaluate at a single starting grain because the **chosen plan forces multi-stage decomposition** the aggregate cannot survive MUST raise `E_UNSAFE_REAGGREGATION`. The two Foundation shapes that force decomposition are **§6.7 chasm pre-aggregation** (incompatible 1:N reaches that must be aggregated independently before being merged) and **§6.8.2 stitch** (independent per-fact aggregation under a shared dimension followed by a FULL OUTER merge, then re-aggregation at the query grain). Errors of this kind MUST identify the aggregate and the grains involved. (Note: a single-step holistic aggregate over a plain `1 : N` edge is **not** caught — it is a single SQL aggregate over the joined rows, well-defined per D-020. The §6.8.1 bridge plan is also **not** caught — it is a single-pass aggregate over the de-duplicated `(measure-home-row, group-key)` row set, well-defined per D-027 for every aggregate category.) | §6.2 Starting Grain, §6.7, §6.8.2 | (a) `MEDIAN(orders.amount)` over a §6.7 chasm-pre-aggregation plan grouped by a dimension that requires aggregating two incompatible 1:N reaches first ⇒ `E_UNSAFE_REAGGREGATION` (`MEDIAN` cannot be re-aggregated from per-side partials). (b) `SUM(orders.amount)` over the same shape ⇒ succeeds (`SUM` is distributive and survives the chasm pre-aggregation). (c) `AVG(movies.gross)` over an `actors ↔ appearances ↔ movies` M:N bridge grouped by `actors.height` ⇒ **accepted** per D-027 (single-pass over the bridge-deduped row set). (d) `COUNT(DISTINCT memberships.customer_id)` over the same M:N bridge ⇒ **accepted** per §6.11.3 / D-027. |
| **D-023** | A scalar query MUST raise `E_FAN_OUT_IN_SCALAR_QUERY` when no single home grain can serve the projection without replicating home-dataset rows. Two known shapes trigger this code: (i) the join path required by `Fields` crosses an `N : N` edge or otherwise replicates the home dataset's rows; (ii) `Fields` supplies row-level references from two or more datasets that are not `1 : 1`-linked (e.g., `[orders.amount, returns.amount]`) — no single home grain exists. The aggregation-query shape handles both shapes non-fatally via §6.7 pre-aggregation and §6.8.2 stitch; only the scalar shape lacks an aggregation step in which to absorb the replication. | §6.2 Final Grain, §5.1.2 | (a) Scalar query with `Fields: [customers.region, order_lines.product_id]` over an `N : N` between `customers` and `order_lines` ⇒ `E_FAN_OUT_IN_SCALAR_QUERY` (shape i). (b) `Fields: [orders.amount, returns.amount, customers.region]` where `orders` and `returns` are independent facts under `customers` ⇒ `E_FAN_OUT_IN_SCALAR_QUERY` (shape ii). (c) Either of the above as an aggregation query ⇒ succeeds with pre-aggregation or stitch. |
| **D-024** | A row-level (non-aggregate) reference to a field at a grain finer than the consuming home grain MUST raise `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`. The remedy is to wrap the reference in an aggregate **inside a model-scoped metric** (§4.5 form 1) or pull the value from a coarser-grain related dataset. Wrapping the reference in an aggregate **inside a field expression** raises `E_AGGREGATE_IN_FIELD` (§4.3); fields are non-aggregate by construction. | §6.2 Starting Grain | Field on `customers` referencing `orders.amount` without an aggregate wrapping ⇒ `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`. Wrapping the reference in `SUM(orders.amount)` inside a *field* on `customers` ⇒ `E_AGGREGATE_IN_FIELD`. Top-level metric `lifetime_value = SUM(orders.amount)` ⇒ accepted. |
| **D-025** | A measure that the engine cannot resolve at any single starting grain, and that does not match any of the more-specific codes (`E3012`, `E3013`, `E_UNSAFE_REAGGREGATION`), MUST raise `E_AMBIGUOUS_MEASURE_GRAIN`. The diagnostic MUST list the starting grains the engine identified. **Known cases** Foundation engines SHOULD recognise and raise this code for: (i) a measure whose expression touches two facts that are connected only through a path the engine cannot pre-aggregate safely (neither §6.7 pre-aggregation nor §6.8.1 bridge nor §6.8.2 stitch applies), but the failure mode does not fit `E3012` (no M:N) or `E3013` (a path does exist); (ii) a measure whose join-path resolution finds two equally-valid finest-grain anchors at the same "level" (e.g., two unrelated 1:1-linked dimension hubs each finer than the dim set, with no relationship between them). Engines MAY use this code for additional shapes the spec does not yet enumerate, but SHOULD reach for the more-specific codes (`E3012`, `E3013`, `E_UNSAFE_REAGGREGATION`, `E_FAN_OUT_IN_SCALAR_QUERY`) first; this code is the catch-all when none of those applies. | §6.2 Starting Grain | One test per known case (i)–(ii). Cases the spec does not enumerate are still valid uses of `E_AMBIGUOUS_MEASURE_GRAIN`; portability conformance asserts only that engines raise this code rather than produce silently-wrong rows. |
| **D-026** | Bridge resolution MUST satisfy Semantic 2: each row of the measure's home dataset contributes to each output group at most once, even when multiple bridge rows associate that fact with the same group. Concretely, the bridge plan materializes the distinct `(fact, group-key)` associations and aggregates over that set; per-bridge-row fan-out is never carried into the final aggregate. | §6.1 Semantic 2, §6.8.1 | M:N model `actors ↔ appearances ↔ movies`, two actors of height `170` both appearing in movie M1 with `gross = 100`. Query `SUM(movies.gross)` grouped by `actors.height` ⇒ the `170` group's total equals 100 plus the gross of the other distinct movies the height-170 cast appears in, **not** 200 (M1 counted twice). The naive flat-join SQL `actors ⋈ appearances ⋈ movies GROUP BY actors.height` gives the doubled answer; the Foundation result MUST match the distinct-`(movie, height)` answer. Looker (symmetric aggregates) and Tableau Multi-Fact (relationships) produce the same number on the same data. |
| **D-027** | A bare cross-grain aggregate over an `N : N` edge MUST resolve via the **bridge-de-duplication construction** of D-026 / §6.8.1, regardless of aggregate category (distributive, algebraic, or holistic). The construction is a single-pass aggregate over the unique `(measure-home-row, group-key)` row set materialised by the bridge — no multi-stage decomposition is forced, so `D-022` does not fire here. The result of `AGG(home_dataset.x)` is `AGG` applied once over the de-duplicated set. This is the heavy-side-weighted single-step analogue of the D-020 rule for `1 : N` edges. The alternative "per-home-row-first" interpretation (e.g., per-actor-first averaging in the §6.8.1 fixture, which would yield `AVG = 125` rather than `AVG = 150` for height `170`) is reachable only through the *nested form* `AGG(AGG(home_dataset.x))`, which is **deferred to §10's grain-aware-functions proposal**; an attempt to write the nested form raises `E_NESTED_AGGREGATION_DEFERRED` (§4.5). | §4.5, §6.8.1 | (a) `AVG(movies.gross)` grouped by `actors.height` over the M:N from D-026 ⇒ accepted; `170 → AVG(100, 200) = 150`, `180 → AVG(50) = 50`. (b) `MEDIAN(movies.gross)` grouped by `actors.height` over the same shape ⇒ accepted; single-pass MEDIAN over the bridge-deduped row set. (c) `COUNT(DISTINCT movies.movie_id)` grouped by `actors.height` ⇒ accepted (§6.11.3 / D-027). (d) `AVG(AVG(movies.gross))` grouped by `actors.height` ⇒ `E_NESTED_AGGREGATION_DEFERRED` (the per-home-row-first rescue path waits for §10). |
| **D-028** | Standard SQL window functions (ranking, navigation, aggregate-windows; `ROWS` and `RANGE` frame modes; integer-literal frame bounds) MUST be accepted in `Measures`, `Fields`, `Order By`, and `Having`, and in any field or metric `expression`. A window function in `Where` MUST raise `E_WINDOW_IN_WHERE`. A window function nested directly inside another window function MUST raise a parse-level error (matching standard SQL). | §6.10.1 | (a) `Measures: [SUM(amount) OVER (PARTITION BY region) AS regional_total]` ⇒ accepted. (b) `Where: ROW_NUMBER() OVER (...) <= 10` ⇒ `E_WINDOW_IN_WHERE`. (c) `RANK() OVER (... ORDER BY SUM(x) OVER (...))` ⇒ parse error. |
| **D-029** | Every `ORDER BY <expr>` — both the outer query's `Order By` and any `ORDER BY` inside `OVER (...)` — has a defined NULL placement. If the user omits `NULLS FIRST` / `NULLS LAST`, the Foundation default is **`NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC`** — i.e., NULL is treated as a high-end value that lands at whichever end the maximum lands at. This preserves the symmetry property that flipping `ASC ↔ DESC` flips NULL placement (so a "top-N → bottom-N" UI flip moves the NULL rows as expected). It also matches SQL:2003's "NULLs compare-greater than non-NULLs" default and the out-of-the-box defaults of Snowflake, PostgreSQL, and Oracle. Engines MUST guarantee the resolved row order on every supported dialect; the compiled SQL emits the explicit clause whenever the dialect's native default would otherwise produce a different order. When the resolved clause matches the dialect's native default the explicit clause MAY be elided — both forms produce identical row orders on that dialect, and D-014's byte-identical guarantee is per `(model, query, dialect)` (within a dialect deterministic; across dialects the resolved row order is identical even when literal SQL text differs by an elidable `NULLS …` token). The Foundation does NOT reject models that omit the explicit clause. A user who wants every NULL pinned to a specific end regardless of direction MUST write the explicit clause; that clause is then carried unchanged on every dialect (it cannot be elided because it overrides the resolved default). | §5.1 Common clause semantics, §6.10.2 | (a) `LAG(amount) OVER (ORDER BY order_date NULLS LAST)` ⇒ accepted on every dialect; the explicit user choice is preserved. (b) `LAG(amount) OVER (ORDER BY order_date)` ⇒ accepted; the resolved order is `ASC NULLS LAST` on every dialect — emitted explicitly on Databricks/Spark (whose native default is `ASC NULLS FIRST`); MAY be elided on Snowflake / PostgreSQL / DuckDB (whose native default already produces `NULLS LAST` for `ASC`). (c) `Order By: [{field: revenue, direction: DESC}]` ⇒ accepted; the resolved order is `DESC NULLS FIRST` on every dialect — emitted explicitly on Databricks/Spark/DuckDB (whose native default produces `NULLS LAST` for `DESC`); MAY be elided on Snowflake (whose native default already produces `NULLS FIRST` for `DESC`). (d) `Order By: [{field: revenue, direction: DESC, nulls: LAST}]` ⇒ accepted; emitted SQL contains `ORDER BY revenue DESC NULLS LAST` on every dialect — the explicit user clause overrides the symmetric default and is never elided. |
| **D-030** | A window function whose home dataset is `A` MUST run over the pre-fan-out row set of `A`, satisfying Semantic 2 for windows. Engines MUST materialize the home-grain rows (pre-aggregation or equivalent) before applying the window; they MUST NOT emit SQL that evaluates the window over fan-out-duplicated rows. If a safe rewrite is unavailable, raise `E_WINDOW_OVER_FANOUT_REWRITE`. | §6.1 Semantic 2, §6.10.3 | Field `orders.running_total = SUM(amount) OVER (PARTITION BY customer_id ORDER BY order_date NULLS LAST)` referenced in a query that also joins `order_lines` (`N : 1` from `order_lines → orders`) ⇒ the window runs over distinct `orders` rows; the per-order running total is not multiplied by line count. |
| **D-031** | A metric `expression` that references another metric whose `expression` contains a window function MUST raise `E_WINDOWED_METRIC_COMPOSITION`. Direct use of a windowed metric in `Measures` is allowed; composing it from another metric is deferred (§10). | §6.10.5 | (a) Metric `running_total = SUM(amount) OVER (ORDER BY date NULLS LAST)` used directly in `Measures: [running_total]` ⇒ accepted. (b) Metric `running_total_x2 = running_total * 2` ⇒ `E_WINDOWED_METRIC_COMPOSITION`. |
| **D-032** | The Foundation supports the `ROWS` and `RANGE` frame modes with integer-literal bounds. `GROUPS` frame mode and parameterized frame bounds MUST be rejected with `E_DEFERRED_KEY_REJECTED` (or `E_DEFERRED_FRAME_MODE`). | §6.10.6 | (a) `ROWS BETWEEN 6 PRECEDING AND CURRENT ROW` ⇒ accepted. (b) `GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW` ⇒ rejected. (c) `ROWS BETWEEN :n PRECEDING AND CURRENT ROW` ⇒ rejected. |
| **D-033** | Aggregates over an **empty** input row set follow standard SQL: `COUNT(*)`, `COUNT(x)`, and `COUNT(DISTINCT x)` return `0`; `SUM`, `AVG`, `MIN`, `MAX`, `MEDIAN`, percentiles, `STDDEV`, and `VARIANCE` return `NULL`. Aggregates over an input that contains `NULL` values follow standard SQL `NULL`-handling: `COUNT(*)` counts all rows; `COUNT(x)` and `COUNT(DISTINCT x)` ignore rows where `x IS NULL`; `SUM`/`AVG`/`MIN`/`MAX`/etc. ignore `NULL` inputs and return `NULL` when every input is `NULL`. Models that prefer `0` on a missing-set cell MUST declare it explicitly via `COALESCE(SUM(...), 0)` — this is a per-metric authoring choice, not a Foundation-level rewrite. | §6.11.1, §6.11.2 | (a) `SUM(amount)` over zero orders ⇒ `NULL`. (b) `COUNT(*)` over zero orders ⇒ `0`. (c) `AVG(amount)` over zero rows ⇒ `NULL`. (d) `AVG(amount)` over rows where `amount` is `(5, NULL, NULL)` ⇒ `5` (NULLs ignored). (e) `COUNT(amount)` over the same set ⇒ `1`. (f) Stitch plan with one fact missing in a group ⇒ that fact's `SUM` cell is `NULL` (or `0` if the metric is declared as `COALESCE(SUM(...), 0)`). |

When this catalog grows, new entries are inserted with the next available `D-NNN` ID. The compliance suite (§11.1) MUST contain at least one case per entry whose `must_pass` status is gated on the entry being marked stable.

**Executable witnesses.** Concrete test vectors — fixtures, data, queries, and expected row sets — live in [`DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md). Every `D-NNN` decision in this appendix MUST eventually be witnessed by at least one `T-NNN` entry there; `DATA_TESTS.md` §5 tracks the current coverage map and §6 lists decisions still awaiting a vector. The flagship vector is **T-015** (bridge de-duplication per Semantic 2), the test that pins D-026 with the actor↔movie fixture from §6.8.1 of this document.

---

## Appendix C: Error Code Index

This appendix consolidates every error code mentioned in the Foundation. Each row gives the code, the trigger that raises it, and the §-anchor where its semantics are defined. New codes added in future revisions are appended; codes deprecated in later revisions are kept and marked `superseded_by:` so older models continue to decode their diagnostics.

The codes split into three families:

- **`E_*`** — semantic-correctness errors raised by the planner before any SQL is emitted (the family the spec defines normatively).
- **`E3xxx`** — numeric M:N / chasm errors carried over from older OSI proposals; kept for backwards-compatibility of error-handling code that pattern-matches on the prefix.
- **`E_DEFERRED_*`** — errors raised when a model uses a feature that is recognised but explicitly deferred (§10).

| Code | Trigger | §-anchor | Family |
|:---|:---|:---|:---|
| `E3011_MN_AGGREGATION_REJECTED` | Engine-capability opt-out — declared by an engine that does not support M:N traversal at all. Such an engine MUST fail every M:N query with this code and MUST NOT emit SQL. Engine-wide, not per-query: M:N-*supporting* engines never raise it, and per-query M:N failures use `E3012` / `E3013`. | §6.8 | E3xxx |
| `E3012_MN_NO_SAFE_REWRITE` | An `N : N` traversal in a measure has no semantically-equivalent safe rewrite given the current model and query grain (no bridge, no shared-dimension stitch). | §6.1, §6.8 | E3xxx |
| `E3013_NO_STITCHING_DIMENSION` | Two unrelated facts (different roots, no path) are referenced together with no dimension shared by both. The result would otherwise be a Cartesian product. | §6.1, §6.8 | E3xxx |
| `E_AGGREGATE_IN_SCALAR_QUERY` | A bare metric reference (or any query-grain aggregate) appears inside `Fields` of a scalar query. | §5.1.2 / D-011 | E_* |
| `E_AGGREGATE_IN_WHERE` | A query-grain aggregate appears inside `Where`. | §6.3 / D-005, D-012 | E_* |
| `E_AMBIGUOUS_MEASURE_GRAIN` | A measure has multiple incompatible starting grains and no more-specific code applies. | §6.2 / D-025 | E_* |
| `E_NESTED_AGGREGATION_DEFERRED` | A metric expression contains a nested aggregate (an aggregate function applied to another aggregate's result, e.g. `AVG(COUNT(orders.oid))`, `AVG(AVG(orders.amount))`). Nested aggregation requires an implicit grain pin on the inner aggregate; the rules for choosing that pin are deferred to §10's grain-aware-functions proposal. For distributive aggregates the single-step form gives identical numbers; for non-distributive aggregates the bare form gives the heavy-side-weighted answer (single-step over `1 : N`, bridge-dedup over `N : N` per D-027), and the unweighted "per-home-row first" interpretation that nested aggregation expresses waits for §10. | §4.5 / D-027 | E_DEFERRED_* |
| `E_NESTED_WINDOW` | A window function appears inside another window function's expression. Standard SQL forbids this; the parse-level rejection mandated by D-028(c) surfaces under this name so engines pinning on a stable code can pattern-match without parsing message text. | §6.10.1 / D-028(c) | E_* |
| `E_AGGREGATE_IN_FIELD` | A field expression contains an aggregate function (`SUM`, `COUNT`, `AVG`, etc., whether same-grain over the home dataset's own columns or cross-grain via a `1 : N` reach). All aggregates live in model-scoped metrics (§4.5); field expressions are non-aggregate. The field-level form is deferred to §10's grain-aware-functions proposal. | §4.3 / D-003 | E_DEFERRED_* |
| `E_AMBIGUOUS_PATH` | More than one relationship path connects the referenced datasets. | §6.9 / D-018 | E_* |
| `E_DEFERRED_FRAME_MODE` | A window expression uses `GROUPS` frame mode or parameterized frame bounds (deferred to §10). | §6.10.6 / D-032 | E_DEFERRED_* |
| `E_DEFERRED_KEY_REJECTED` | A model uses a recognised but deferred key (`referential_integrity`, `condition`, `asof`, `range`, `grain:`, `filter:`, etc.) outside an extension flag. | §11 / D-009 | E_DEFERRED_* |
| `E_EMPTY_AGGREGATION_QUERY` | An aggregation query has neither `Dimensions` nor `Measures`. | §6.2 step A.1 | E_* |
| `E_EMPTY_SCALAR_QUERY` | A scalar query has empty `Fields`. | §6.2 step B.1 | E_* |
| `E_FAN_OUT_IN_SCALAR_QUERY` | A scalar query's join path replicates home-dataset rows (e.g., crosses an `N : N` edge, or supplies row-level fields from incompatible homes). | §5.1.2, §6.2 / D-023 | E_* |
| `E_FIELD_DEPENDENCY_CYCLE` | Field expressions on the same dataset reference one another transitively, forming a cycle (`a` references `b` references `a`). Fields are required to form a DAG so resolution is well-defined and termination is guaranteed. | §4.3 | E_* |
| `E_INVALID_NATURAL_GRAIN` | A model declares `natural_grain` (deferred to a future proposal — see §10) referring to an unknown dataset, or declares two `natural_grain` keys. | §10 (deferred) | E_* |
| `E_MIXED_PREDICATE_LEVEL` | A boolean predicate mixes terms at different resolved levels (row-level + query-grain aggregate in one expression). | §6.3 / D-005, D-012 | E_* |
| `E_MIXED_QUERY_SHAPE` | A query lists both `Fields` and (`Dimensions` ∪ `Measures`). | §5.1 / D-010 | E_* |
| `E_NAME_COLLISION` | Two global names share a normalised form. | §4.6 / D-006 | E_* |
| `E_NAME_NOT_FOUND` | A bare reference does not resolve to any in-scope name. | §4.6 / D-006 | E_* |
| `E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE` | Under a `natural_grain` declaration (deferred), the query's otherwise-derived starting grain is finer than `natural_grain` and no safe pre-aggregation exists. | natural_grain proposal §3.2 | E_* |
| `E_NO_PATH` | No relationship path connects the referenced datasets. | §6.9 / D-018 | E_* |
| `E_NON_AGGREGATE_IN_HAVING` | A predicate in `Having` is purely row-level (no aggregate, not a grouping column). | §6.3 / D-005, D-012 | E_* |
| `E_PRIMARY_KEY_REQUIRED` | An engine that opts to require primary keys (§4.2) finds a dataset without one. | §4.2 | E_* |
| `E_RESERVED_NAME` | A user-declared dataset, field, metric, or relationship name is one of the OSI reserved names (`GRAIN`, `FILTER`, `QUERY_FILTER`, and other names enumerated in §4.6.2). These names are reserved so they can be repurposed as query-language keywords in future revisions without breaking existing models. | §4.6.2 / D-019 | E_* |
| `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` | A row-level (non-aggregate) reference targets a field at a grain finer than the consuming home grain. | §6.2 / D-024 | E_* |
| `E_UNKNOWN_FUNCTION` | A scalar or aggregate function call inside a metric or field expression is not in the OSI_SQL_2026 portable subset (the parse whitelist enumerated in [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md)). Vendor-specific functions reach engine code only through the per-dialect `dialects:` block. The rejection happens at parse time so portable models stay portable across engines. | §SQL_EXPRESSION_SUBSET / D-021 | E_* |
| `E_UNSAFE_REAGGREGATION` | The chosen plan forces a multi-stage decomposition the aggregate cannot survive — typically a holistic aggregate (`MEDIAN`, `PERCENTILE_CONT`) over a §6.7 chasm pre-aggregation or a §6.8.2 stitch. The §6.8.1 bridge plan is **not** in this family: it is a single-pass aggregate over the de-duplicated `(measure-home-row, group-key)` set, accepted for every aggregate category per D-027 (`AVG`, `MEDIAN`, `COUNT(DISTINCT)` over an M:N bridge are all accepted bare). | §6.1, §6.2, §6.7, §6.8.2 / D-022 | E_* |
| `E_WINDOW_IN_WHERE` | A window function appears inside `Where`. SQL forbids this; windows run after `Where`. | §6.10.1 / D-028 | E_* |
| `E_WINDOW_OVER_FANOUT_REWRITE` | A window function's home dataset would be fanned out by the query's join path, and no safe rewrite is available. | §6.10.3 / D-030 | E_* |
| `E_WINDOWED_METRIC_COMPOSITION` | A metric expression references another metric whose expression contains a window function. | §6.10.5 / D-031 | E_* |

**Diagnostic conventions.** Engines MUST set `error.code` to one of the values above; they MUST NOT raise additional codes outside this index without documenting them as engine-specific extensions. Tests in the compliance suite (§11.1) assert on `error.code`, never on `error.message` text.
