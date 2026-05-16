# DATA_TESTS.md — Concrete Test Vectors for the Foundation Compliance Suite

**Status:** Authoritative test catalog
**Companions:** [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md) Appendix B · [`JOIN_ALGEBRA.md`](JOIN_ALGEBRA.md) · [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md)

This document is the **concrete realization** of the Foundation Conformance
Decisions catalogued in `Proposed_OSI_Semantics.md` Appendix B. Each
**T-NNN** entry below is a runnable test vector: a model, a dataset, a query,
and the row set or error code an implementation MUST produce. Appendix B's
**D-NNN** entries say *what* an implementation must do; this file says *what
inputs prove it*.

Implementations are encouraged to translate each entry into a fixture under
the published Foundation compliance suite — for `osi_python` this is
[`compliance/foundation-v0.1/tests/`](../../compliance/foundation-v0.1/tests/)
(one folder per `T-NNN` containing `metadata.yaml + model.yaml +
query.json + gold_rows.json`). Implementations on other engines run the
same suite through the standard adapter contract documented in
[`compliance/foundation-v0.1/ADAPTER_INTERFACE.md`](../../compliance/ADAPTER_INTERFACE.md).
The expected outputs are normative and implementation-independent.

---

## 1. Conventions

### 1.1 Test entry shape

Every test has the same five fields:

| Field | Meaning |
|:---|:---|
| **Anchors** | The spec section(s) and `D-NNN` decision(s) the test pins. |
| **Fixture** | The named model fixture (§3) the test runs against. |
| **Data** | The concrete rows. For brevity, fixture-shared data lives in §3 and is referenced by name. Test-local data is shown inline. |
| **Query** | The semantic query — written in the Foundation surface used by tests (a thin YAML around §5's `Aggregation Query` / `Scalar Query` shapes). |
| **Expected** | Either a row set (presented as a markdown table, order-insensitive unless noted) or `error: <code>` with the diagnostic content the engine MUST surface. |

A test passes iff the engine's observable output (rows or typed error)
matches the **Expected** field. Tests do **not** assert on SQL string,
plan shape, CTE layout, or any other internal artefact (§11.1 of the
semantics spec).

### 1.2 Row-set comparison

- **Order-insensitive** by default — sort both sides by all output columns before comparing.
- **NULL-aware** — `NULL == NULL` for the purposes of the test (the SQL three-valued logic does not apply to assertions).
- **Type-aware** — numeric values are compared by value, not by representation; `100`, `100.0`, and `1e2` are equal. String values must match exactly.

### 1.3 Error comparison

An error result is a triple `(code, anchor, must_contain)`:

```yaml
error:
  code: E_AGGREGATE_IN_WHERE
  anchor: §5.3            # the spec section the engine MUST cite in its diagnostic
  must_contain:           # substrings the diagnostic MUST include (case-insensitive)
    - "aggregate"
    - "Where"
```

An engine MAY include additional diagnostic content. The code and the
`must_contain` substrings are the only assertions; wording is otherwise free.

### 1.4 Determinism guarantee

Every test that produces a row set MUST be deterministic: running it
twice on the same fixture MUST produce byte-identical SQL and the same
multiset of rows. This is also `D-014`'s test shape, applied uniformly.

---

### 1.5 NULL handling, empty groups, and `0` (D-033)

The Foundation follows **standard SQL** for aggregates over empty
input row sets (§6.11 / D-033):

| Aggregate family | Empty-input result |
|:---|:---|
| `COUNT(*)`, `COUNT(x)`, `COUNT(DISTINCT x)` | `0` |
| `SUM`, `AVG`, `MIN`, `MAX`, `MEDIAN`, percentiles, `STDDEV`, `VARIANCE` | `NULL` |

Tests in this catalog use these results literally — but **only for
dim values that actually appear in the result**. Per §1.6 (Plan A for
single-measure shapes), a dim value with no matching fact row is
dropped from the result entirely; there is no empty-aggregate cell
because there is no row.

The empty-aggregate rule applies to:

- **Multi-measure stitch cells** where one branch has no contribution
  for a dim value present via the other branch (T-011, T-045) —
  `SUM` → `NULL`, `COUNT` → `0`.
- **Dim values present via the `NULL`-key orphan bucket** when the
  fact row has no matching dim row (T-047, T-053 etc.) — same rule
  applies; the orphan-bucket row is part of the result, and its
  cells follow standard SQL.

Models that prefer `0` for a `SUM` cell MUST declare it explicitly:
`COALESCE(SUM(amount), 0) AS revenue`. The Foundation does NOT
auto-rewrite `SUM` to `COALESCE(SUM, 0)`.

---

### 1.6 Resolved — single-measure shapes follow Plan A (preserve facts)

Earlier drafts of this catalog had an unresolved tension between two
flagship tests:

- **T-001** (`Dims: [customers.region]; Measures: [SUM(orders.amount)]`)
  expected a `NORTH` row with `revenue = NULL` — implying the planner
  preserves the dimension domain even when no fact rows match.
- **T-006** (`Dims: [customers.region]; Measures: [COUNT(*)]; Where
  orders.status = 'completed'`) expected NORTH to *not* appear —
  implying the planner anchors on the fact and drops dim values with
  no matching fact.

Both could not be correct under one planner. The Architect resolved
this in favour of **Plan A — preserve facts, drop unmatched dim values
— for single-measure aggregation queries**, with the **multi-measure
shape using FULL OUTER stitch (§6.6 row 3) to preserve both sides** as
a deliberate contrast. The spec was updated to match (§6.6, §6.2 step 7,
D-001 example), and existing tests were brought into alignment.

#### Decision summary

| Query shape | Default join | Which dim values appear in the result |
|:---|:---|:---|
| Single-measure aggregation: `Dims: [dim.X]; Measures: [SUM(fact.Y)]` | `fact LEFT JOIN dim` (§6.6 row 1) | Distinct `dim.X` reached by a surviving `fact` row, plus a `NULL`-key bucket for orphan facts. **A dim value with no matching fact does NOT appear.** |
| Multi-measure aggregation: `Dims: [dim.X]; Measures: [SUM(factA.Y), SUM(factB.Y)]` | Each measure independently resolved per row 1, then `FULL OUTER` stitch on shared dims (§6.6 row 3) | Union of dim values reachable from *either* branch. A dim value appears if either fact pulls it in. |

This rule is now consistent across every test in the catalog:

- **T-001, T-005a-e, T-026, T-028, T-047, T-053**: single-measure or
  single-fact-source queries — NORTH does not appear.
- **T-006**: single-measure query with a fact-level WHERE — NORTH
  does not appear.
- **T-011, T-045**: multi-measure stitch — NORTH appears via the
  branch that has data for customer 4.

#### How a user gets the "preserve all dim values" behaviour

If a user wants every dim value to appear (BI-tool convention), they
make the query multi-measure by adding a measure sourced from the dim
dataset itself:

```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount)    AS revenue
  - COUNT(customers.id)   AS customer_count
```

This converts the shape into a multi-measure stitch — the `customers`
branch contributes every distinct region (because `COUNT(customers.id)`
is defined for every region in `customers`), and the FULL OUTER
preserves NORTH with `revenue = NULL`, `customer_count = 1`.

This is the BI-tool "all regions appear" behaviour, surfaced
*deliberately* through the query shape rather than as a hidden
planner default. The user opts in by asking for it.

#### Why Plan A (not preserve-dims by default)

- **Matches raw SQL.** `SELECT dim.X, SUM(fact.Y) FROM fact LEFT JOIN
  dim ... GROUP BY dim.X` produces exactly the single-measure Plan A
  result. The Foundation does not introduce surprises relative to
  the SQL the user could write by hand.
- **Matches Snowflake Semantic Views.** Snowflake's default plan
  shape is also Plan A — porting Snowflake models to OSI is a no-op
  for this dimension behaviour.
- **Composes cleanly with multi-measure stitch.** §6.6 row 3 already
  mandates FULL OUTER for multi-measure shapes; row 1 says LEFT for
  single-measure shapes. The contrast is the mental model: "the
  multi-measure stitch is the *only* place both sides are preserved
  — and that's because there is no other correct shape for merging
  two independently-aggregated facts."
- **Surfaces orphan facts.** Plan A keeps the `NULL`-key bucket for
  orphan fact rows (e.g. order 105 with `customer_id = 99`). This is
  a data-quality signal — preserve-dims would lose it.

#### Trade-off accepted

A first-time BI user typing a single-measure query and *expecting* to
see every region might be surprised when a region with no fact data
is absent. The mitigation is the documented multi-measure pattern
above. The Foundation prefers "predictable, matches raw SQL" over
"BI-tool-magical".

> **Note for test authors.** When writing a new test that groups by a
> dimension over a single-fact source, write the expected rows
> following Plan A: include only dim values reached by a fact row,
> plus the `NULL`-key bucket for orphans. If you want every dim value
> in the expected rows, the test must be multi-measure (with one of
> the measures sourced from the dim dataset).

---

## 2. How to add a new test

1. Pick the lowest-numbered free `T-NNN`.
2. Link it to a `D-NNN` in `Proposed_OSI_Semantics.md` Appendix B — if no `D-NNN` covers the behaviour, add one there first.
3. Write the test against an existing fixture (§3) if possible; create a new fixture only when the existing ones can't express the shape.
4. Cite the test from the relevant spec § so the doc and the test grow together.

---

## 3. Common fixtures

### 3.1 Fixture **F-PRELUDE** — single-fact star with multi-fact extension

Mirrors the *Prelude model* in `Proposed_OSI_Semantics.md` Appendix A.

```yaml
datasets:
  - name: customers
    primary_key: [id]
    fields:
      - { name: id,       type: integer }
      - { name: region,   type: string }
      - { name: segment,  type: string }

  - name: orders
    primary_key: [id]
    fields:
      - { name: id,           type: integer }
      - { name: customer_id,  type: integer }
      - { name: amount,       type: decimal(10,2) }
      - { name: status,       type: string }

  - name: returns
    primary_key: [id]
    fields:
      - { name: id,           type: integer }
      - { name: customer_id,  type: integer }
      - { name: amount,       type: decimal(10,2) }

  - name: premium_customers
    primary_key: [id]
    fields:
      - { name: id, type: integer }

relationships:
  - { name: orders_to_customer,  from: orders,  to: customers, from_columns: [customer_id], to_columns: [id] }   # N:1
  - { name: returns_to_customer, from: returns, to: customers, from_columns: [customer_id], to_columns: [id] }   # N:1
```

**Data:**

`customers`:

| id | region | segment |
|:--:|:-------|:--------|
| 1  | EAST   | retail  |
| 2  | EAST   | retail  |
| 3  | WEST   | wholesale |
| 4  | NORTH  | retail  |

`orders` (note: orphan order 105 with `customer_id = 99` not in `customers`):

| id  | customer_id | amount | status     |
|:---:|:-----------:|-------:|:-----------|
| 101 | 1           | 100.00 | completed  |
| 102 | 1           | 50.00  | completed  |
| 103 | 2           | 200.00 | pending    |
| 104 | 3           | 75.00  | completed  |
| 105 | 99          | 30.00  | completed  |   ← orphan: customer 99 not in customers

`returns`:

| id  | customer_id | amount |
|:---:|:-----------:|-------:|
| 201 | 1           | 10.00  |
| 202 | 3           | 5.00   |
| 203 | 4           | 15.00  |    ← customer 4 has a return but no order

`premium_customers`:

| id |
|:--:|
| 1  |
| 3  |

### 3.2 Fixture **F-BRIDGE** — M:N through a bridge

Mirrors *Mini-model M2* in Appendix A. This is the fixture for the
flagship `T-015` bridge-deduplication test.

```yaml
datasets:
  - name: actors
    primary_key: [actor_id]
    fields:
      - { name: actor_id, type: integer }
      - { name: name,     type: string }
      - { name: height,   type: integer }

  - name: movies
    primary_key: [movie_id]
    fields:
      - { name: movie_id, type: integer }
      - { name: title,    type: string }
      - { name: gross,    type: decimal(10,2) }

  - name: appearances
    primary_key: [actor_id, movie_id]
    role: bridge
    fields:
      - { name: actor_id, type: integer }
      - { name: movie_id, type: integer }

relationships:
  - { name: app_to_actor, from: appearances, to: actors, from_columns: [actor_id], to_columns: [actor_id] }   # N:1
  - { name: app_to_movie, from: appearances, to: movies, from_columns: [movie_id], to_columns: [movie_id] }   # N:1
```

**Data:**

`actors`:

| actor_id | name  | height |
|:--------:|:------|-------:|
| 1        | Alice | 170    |
| 2        | Bob   | 170    |
| 3        | Carol | 180    |

`movies`:

| movie_id | title  | gross  |
|:--------:|:-------|-------:|
| 10       | Action | 100.00 |
| 11       | Drama  | 200.00 |
| 12       | Comedy | 50.00  |

`appearances`:

| actor_id | movie_id |
|:--------:|:--------:|
| 1        | 10       |
| 1        | 11       |
| 2        | 10       |   ← M10 (Action) has two actors at height 170
| 3        | 12       |

### 3.3 Fixture **F-BRIDGE-NONE** — variant of F-BRIDGE without the bridge

Same `actors` and `movies` data as F-BRIDGE, but no `appearances` dataset
and an undeclared M:N edge between `actors` and `movies`. Used to test
that the engine fails closed instead of guessing a join path.

```yaml
datasets:
  - name: actors
    primary_key: [actor_id]
    fields: [ { name: actor_id }, { name: name }, { name: height } ]
  - name: movies
    primary_key: [movie_id]
    fields: [ { name: movie_id }, { name: title }, { name: gross } ]

# Note: no `relationships:` block. The model declares no edge; the
# engine MUST NOT silently fabricate one.
```

### 3.4 Fixture **F-AMBIG** — two relationships between the same datasets

Mirrors *Mini-model M3* in Appendix A. Used for `E_AMBIGUOUS_PATH`.

```yaml
datasets:
  - name: orders
    primary_key: [id]
    fields:
      - { name: id }
      - { name: placed_by_id }
      - { name: fulfilled_by_id }
      - { name: amount }

  - name: users
    primary_key: [id]
    fields: [ { name: id }, { name: region } ]

relationships:
  - { name: order_placed_by,    from: orders, to: users, from_columns: [placed_by_id],    to_columns: [id] }
  - { name: order_fulfilled_by, from: orders, to: users, from_columns: [fulfilled_by_id], to_columns: [id] }
```

**Data:**

`orders`:

| id  | placed_by_id | fulfilled_by_id | amount |
|:---:|:------------:|:---------------:|-------:|
| 301 | 1            | 2               | 100.00 |
| 302 | 2            | 2               | 50.00  |

`users`:

| id | region |
|:--:|:-------|
| 1  | EAST   |
| 2  | WEST   |

### 3.5 Fixture **F-NOPATH** — two disconnected datasets

Mirrors *Mini-model M1* in Appendix A. No relationships are declared.

```yaml
datasets:
  - name: orders
    primary_key: [id]
    fields: [ { name: id }, { name: customer_id }, { name: amount } ]

  - name: inventory_movements
    primary_key: [movement_id]
    fields: [ { name: movement_id }, { name: warehouse_id }, { name: quantity } ]
```

---

## 4. Tests

### 4.A Query shape

#### T-001 — Aggregation-query cardinality is `DISTINCT(Dimensions)`

**Anchors:** §5.1.1, §5.2 · D-001
**Fixture:** F-PRELUDE

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount) AS revenue
```

**Expected (single-measure ⇒ Plan A per §6.6 row 1 and §6.2 step 7):**

| region | revenue |
|:-------|--------:|
| EAST   | 350.00  |
| WEST   | 75.00   |
| NULL   | 30.00   |   ← orphan order 105 (customer_id = 99 not in customers)

Row count = `COUNT(DISTINCT region)` over the regions reached by an
`orders` row, plus one for the orphan-customer bucket. NORTH (customer
4's region) does **not** appear: customer 4 has no orders, and a
single-measure aggregation query follows the fact's natural LEFT join
shape (`orders LEFT JOIN customers`) — a dim value reachable only
from the dim domain is *not* in the result.

Engines that "carry" `customer_id` into the output grain because the
measure is sourced from `orders` fail this test on row count (they
would group by `customer_id`, not `region`, and produce four data
rows + orphan). Engines that produce a `NORTH | NULL` row are
applying the multi-measure stitch shape to a single-measure query —
also wrong (see §1.6).

If the user wants NORTH to appear with `revenue = NULL`, they must
make the query multi-measure (e.g. `Measures: [SUM(orders.amount) AS
revenue, COUNT(customers.id) AS customer_count]`) — the FULL OUTER
stitch in §6.6 row 3 then preserves every region present in
`customers`. See T-011 for the canonical multi-measure shape.

#### T-002 — Mixing `Dimensions` and `Fields` is rejected

**Anchors:** §5.1 · D-010
**Fixture:** F-PRELUDE

**Query:**
```yaml
Dimensions: [customers.region]
Fields:     [customers.id]
Measures:   [SUM(orders.amount)]
```

**Expected:**
```yaml
error:
  code: E_MIXED_QUERY_SHAPE
  anchor: §5.1
  must_contain: ["Dimensions", "Fields", "scalar"]
```

#### T-003 — Bare metric reference inside `Fields` is rejected

**Anchors:** §5.1.2 · D-011
**Fixture:** F-PRELUDE. Assume `orders.total_revenue = SUM(amount)` is declared.

**Query:**
```yaml
Fields: [orders.id, orders.total_revenue]
```

**Expected:**
```yaml
error:
  code: E_AGGREGATE_IN_SCALAR_QUERY
  anchor: §5.1.2
  must_contain: ["scalar", "total_revenue", "aggregate"]
```

A scalar query asks for row-level values; a metric is by definition an
aggregate at query grain, which has no defined value at the row grain
of `orders`. The engine MUST NOT silently aggregate, broadcast, or
"window" the metric.

---

### 4.B Field & metric grain

#### T-004 — Implicit home-grain aggregation in a field expression

**Anchors:** §4.3 · D-003, D-015
**Fixture:** F-PRELUDE. Add this field to the model:

```yaml
fields:
  - name: customers.lifetime_value
    expression: "SUM(orders.amount)"
```

**Query (scalar):**
```yaml
Fields: [customers.region, customers.id, customers.lifetime_value]
```

**Expected:**

| region | id | lifetime_value |
|:-------|:--:|---------------:|
| EAST   | 1  | 150.00         |   ← SUM(100, 50)
| EAST   | 2  | 200.00         |
| WEST   | 3  | 75.00          |
| NORTH  | 4  | NULL           |   ← no orders (or 0, depending on engine's LEFT-aggregate convention — both are acceptable; test allows either)

The inner `SUM` is evaluated at the **home grain of `customers`** (one
sum per customer row), regardless of any grouping the consuming query
applies. This is what makes `lifetime_value` reusable across queries.

**Compilation-strategy equivalence (D-015):** an engine MAY compile the
inner `SUM` as (a) a correlated subquery, (b) a `LATERAL` / `CROSS APPLY`,
or (c) a pre-aggregated CTE joined back to `customers`. The test passes
under any of the three.

#### T-005 — Cross-grain aggregation: single-step and nested forms

This is the **flagship test family for D-020** — the most-debated
semantic in cross-vendor BI portability. The Foundation accepts **both**
single-step and explicit-nested forms over a `1 : N` edge; they give
different numerical answers for non-distributive aggregates (the
single-step form gives the "all rows at once" answer, the nested form
gives the "per-home-row first" answer). For distributive aggregates
they give identical answers, so either form is acceptable for style.

**Anchors:** §4.5 form (1), §6.1 Semantic 2 · D-020

**Fixture:** F-PRELUDE.

---

##### T-005a — Single-step cross-grain `SUM` over `1 : N` is accepted (distributive)

**Metric declaration:**
```yaml
metrics:
  - name: customers.total_order_amount
    expression: "SUM(orders.amount)"   # single-step; resolves at query grain
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.total_order_amount]
```

**Expected (single-measure ⇒ Plan A; NORTH absent — see T-001):**

| region | total_order_amount |
|:-------|-------------------:|
| EAST   | 350.00 |   ← orders 101, 102, 103
| WEST   | 75.00  |   ← order 104
| NULL   | 30.00  |   ← orphan order 105

No error. For distributive aggregates the single-step form is
equivalent to the explicit-nested form `SUM(SUM(orders.amount))` — both
return identical numbers (cross-validated by T-005d below).

---

##### T-005b — Single-step cross-grain `AVG` over `1 : N` is accepted (heavy-weighted)

**Metric declaration:**
```yaml
metrics:
  - name: customers.avg_order_amount
    expression: "AVG(orders.amount)"   # single-step; standard SQL semantics
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.avg_order_amount]
```

**Expected (single-measure ⇒ Plan A; NORTH absent):** the heavy-customer-weighted SQL average.

| region | avg_order_amount |
|:-------|-----------------:|
| EAST   | 116.67 |   ← AVG(100, 50, 200) = 350/3 — three orders in EAST
| WEST   | 75.00  |   ← AVG(75) — one order in WEST
| NULL   | 30.00  |   ← orphan order 105

No error. Note that EAST's answer (`116.67`) is **not** the unweighted
average across customers — Customer 1 averages `75` over two orders,
Customer 2 averages `200` over one order, and the unweighted answer
would be `(75 + 200) / 2 = 137.5`. That second number is what the
**nested** form produces; see T-005c.

---

##### T-005c — Explicit nested cross-grain `AVG` over `1 : N` is accepted (unweighted)

The Snowflake-style explicit form is **still accepted**; it just gives a
different (and meaningfully different) answer for non-distributive
aggregates. The Foundation does not force this style — but neither does
it reject it. Users porting from Snowflake Semantic Views can leave their
nested expressions unchanged.

**Metric declaration:**
```yaml
metrics:
  - name: customers.avg_of_per_customer_avg
    expression: "AVG(AVG(orders.amount))"   # inner: per-customer avg; outer: avg across customers
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.avg_of_per_customer_avg]
```

**Expected (single-measure ⇒ Plan A; NORTH absent):** the unweighted average across customers in each region.

| region | avg_of_per_customer_avg |
|:-------|------------------------:|
| EAST   | 137.50 |   ← AVG(Cust1=75, Cust2=200) = 275/2 — two customers in EAST
| WEST   | 75.00  |   ← AVG(Cust3=75) — one customer in WEST
| NULL   | 30.00  |   ← orphan order's customer is unknown; treated as one customer-of-one-order

The user gets to pick: write `AVG(orders.amount)` for the
heavy-weighted answer (T-005b) or `AVG(AVG(orders.amount))` for the
unweighted answer (T-005c). Both are conformant. The two numbers differ
for non-distributive aggregates — this difference is the central
BI-portability decision in D-020.

---

##### T-005d — Single-step and nested `SUM` are numerically equivalent (distributive)

Run both forms against identical data and assert the row sets are
identical.

**Metric A (single-step):**
```yaml
- name: customers.sum_single
  expression: "SUM(orders.amount)"
```

**Metric B (nested):**
```yaml
- name: customers.sum_nested
  expression: "SUM(SUM(orders.amount))"
```

**Query A:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.sum_single]
```

**Query B:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.sum_nested]
```

**Expected:** identical row sets (after column-name normalisation).
Both forms are single-measure aggregation queries ⇒ Plan A ⇒ NORTH
is absent and the orphan `NULL`-region row is present. This test is
what makes "port your Snowflake nested form unchanged" portable in
the distributive case — and is the canonical demonstration that
nested-vs-single is purely a stylistic choice when the aggregate is
distributive.

---

##### T-005e — `COUNT(DISTINCT)` single-step over `1 : N` is accepted

**Anchors:** §4.5, §6.1 · D-020 case (d), D-022 caveat

**Metric declaration:**
```yaml
metrics:
  - name: customers.distinct_order_statuses
    expression: "COUNT(DISTINCT orders.status)"
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [customers.distinct_order_statuses]
```

**Expected (single-measure ⇒ Plan A; NORTH absent):**

| region | distinct_order_statuses |
|:-------|------------------------:|
| EAST   | 2 |   ← `completed` (101, 102) + `pending` (103)
| WEST   | 1 |   ← `completed` (104)
| NULL   | 1 |   ← orphan 105 has `completed`

No `E_UNSAFE_REAGGREGATION`. This pins D-020 (d) and the D-022 caveat
explicitly: a holistic aggregate over a plain `1 : N` edge is one SQL
aggregate over the joined rows — well-defined per D-020. The
`E_UNSAFE_REAGGREGATION` error only fires when the plan would require
*decomposing* a holistic aggregate across grains, which happens on M:N
(D-022) or fan-out shapes the engine cannot pre-aggregate safely.

#### T-006 — `COUNT(*)` over a dataset is well-defined at the home grain

**Anchors:** §7.2 · D-016
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - COUNT(*) AS order_count
Where:
  - orders.status = 'completed'
```

**Expected:**

| region | order_count |
|:-------|------------:|
| EAST   | 2           |   ← orders 101, 102 (102? wait 103 is pending). 101+102 completed. 103 pending excluded.
| WEST   | 1           |   ← order 104
| NULL   | 1           |   ← orphan order 105 (status=completed)

`COUNT(*)` is the count of rows of the **measure-bearing dataset**
that survive the predicates — here, `orders`. Engines that emit
`COUNT(<orders.primary_key>)` as a transparent rewrite (e.g.
Snowflake's `COUNT(*)`-rejection workaround) MUST produce the same
numbers.

---

### 4.C Predicate routing

#### T-007 — Aggregate in `Where` is rejected

**Anchors:** §5.3 · D-005, D-012a
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount)]
Where:
  - SUM(orders.amount) > 100         # aggregate inside Where
```

**Expected:**
```yaml
error:
  code: E_AGGREGATE_IN_WHERE
  anchor: §5.3
  must_contain: ["aggregate", "Where", "Having"]    # diagnostic should hint at Having
```

The diagnostic MUST identify the offending aggregate by name and SHOULD
suggest moving the predicate to `Having`.

#### T-008 — Pure row-level predicate in `Having` is rejected

**Anchors:** §5.3 · D-012b
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount)]
Having:
  - customers.region = 'EAST'        # row-level dimension predicate
```

**Expected:**
```yaml
error:
  code: E_NON_AGGREGATE_IN_HAVING
  anchor: §5.3
  must_contain: ["Having", "Where"]
```

Note this test is about *routing*, not behaviour. An engine that
silently treats `Having` as `Where` produces the right numbers but
breaks the contract, and so fails the test on the error code, not
the rows.

#### T-009 — Mixed-level boolean predicate is rejected

**Anchors:** §5.3 · D-012c
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Having:
  - customers.region = 'EAST' AND SUM(orders.amount) > 100
```

**Expected:**
```yaml
error:
  code: E_MIXED_PREDICATE_LEVEL
  anchor: §5.3
  must_contain: ["row-level", "aggregate", "split"]
```

The diagnostic SHOULD suggest splitting the predicate (row-level half
→ `Where`, aggregate half → `Having`).

---

### 4.D Default join behaviour

#### T-010 — `N:1` enrichment defaults to `LEFT`; orphans appear under `NULL`

**Anchors:** §6.4 · D-004a
**Fixture:** F-PRELUDE.

Covered indirectly by T-001's expected rows — the `region = NULL` row
exists *only because* the default join from `orders → customers` is
`LEFT` and order 105's `customer_id = 99` does not match. An engine
that defaults to `INNER` returns one fewer row and fails the test on
row count. This is the single most common silent-correctness failure
in BI tools (Snowflake's "first matching row" rule is the worst case;
see Appendix B note B-1).

#### T-011 — Multi-fact composition defaults to `FULL OUTER` on shared dims

**Anchors:** §6.4 · D-004b
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount)  AS revenue
  - SUM(returns.amount) AS return_total
```

**Expected (multi-measure ⇒ FULL OUTER stitch per §6.6 row 3 / §6.2
step 7; *both* sides preserved):**

| region | revenue | return_total |
|:-------|--------:|-------------:|
| EAST   | 350.00  | 10.00        |   ← both facts have data
| WEST   | 75.00   | 5.00         |   ← both facts have data
| NORTH  | NULL    | 15.00        |   ← only `returns` has data (customer 4); revenue is `SUM` over zero rows = `NULL` (§6.11 / D-033)
| NULL   | 30.00   | NULL         |   ← only `orders` has data (orphan 105); return_total is `SUM` over zero rows = `NULL`

This is the **multi-measure** counterpart to T-001's single-measure
Plan A: because there are two independently-aggregated facts
(`orders` and `returns`), §6.6 row 3 mandates a `FULL OUTER` between
the two pre-aggregated branches. The union of dim values appearing in
*either* branch is the result. NORTH appears here (via the `returns`
branch) because customer 4 has a return, even though customer 4 has
no orders. Without `returns` as a second measure, this would be a
single-measure query and Plan A would drop NORTH (see T-001). An
engine that defaults to `INNER` between the two pre-aggregated
branches loses NORTH and the orphan-bucket, failing on row count.

**Companion `COUNT` test:** replacing either `SUM` with `COUNT(*)` or
`COUNT(orders.id)` would produce `0` in the missing-side cell, not
`NULL`, per standard SQL. See T-047 for the explicit
`SUM`-returns-`NULL` / `COUNT`-returns-`0` contrast.

#### T-012 — Scalar grand total uses `CROSS JOIN`

**Anchors:** §6.4 · D-004c
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: []                  # no dimensions ⇒ scalar grain
Measures:
  - SUM(orders.amount)  AS total_revenue
  - SUM(returns.amount) AS total_returns
```

**Expected:** exactly one row.

| total_revenue | total_returns |
|--------------:|--------------:|
| 455.00        | 30.00         |

`455.00 = 100+50+200+75+30` (all orders including orphan);
`30.00 = 10+5+15` (all returns). The two scalars sit on a single row
joined via `CROSS JOIN` (`1 ⋈ 1`), not on separate rows.

---

### 4.E M:N resolution — the flagship section

#### T-013 — Two unrelated facts with no shared dimension raise `E3013`

**Anchors:** §6.6 · D-007
**Fixture:** F-NOPATH.

**Query:**
```yaml
Measures:
  - SUM(orders.amount)
  - SUM(inventory_movements.quantity)
```

**Expected:**
```yaml
error:
  code: E3013_NO_STITCHING_DIMENSION
  anchor: §6.6
  must_contain: ["orders", "inventory_movements", "shared dimension"]
```

An engine that emits a Cartesian product here is producing a
plausible-but-wrong total and is the single failure mode Promise 4 is
designed to prevent.

#### T-014 — `N:N` with no bridge and no shared dim raises `E3012`

**Anchors:** §6.6 · D-007
**Fixture:** F-BRIDGE-NONE.

**Query:**
```yaml
Dimensions: [actors.height]
Measures:   [SUM(movies.gross)]
```

**Expected:**
```yaml
error:
  code: E3012_MN_NO_SAFE_REWRITE
  anchor: §6.6
  must_contain: ["actors", "movies", "bridge"]    # diagnostic should suggest the remedy
```

#### T-015 — Bridge resolution de-duplicates per `(fact, group)` (FLAGSHIP)

**Anchors:** §6.6.1, §6.3 Semantic 2 · D-026
**Fixture:** F-BRIDGE.

**Query:**
```yaml
Dimensions: [actors.height]
Measures:
  - SUM(movies.gross) AS total_gross
```

**Expected:**

| height | total_gross |
|-------:|------------:|
| 170    | 300.00      |   ← M10 (100) counted ONCE, plus M11 (200). Not 400.
| 180    | 50.00       |

**Why this test exists.** The naive flat-join SQL
`actors ⋈ appearances ⋈ movies GROUP BY actors.height` produces
`(170 → 400, 180 → 50)` because M10's gross is replicated by Alice's
*and* Bob's appearances. The Foundation's bridge plan, per §6.6.1,
de-duplicates at the `(movie_id, height)` level before the final
`SUM`, and so M10 contributes 100 to height 170 exactly once. This
is **Semantic 2** in operation: no row of `movies` (the measure's
home dataset) contributes more than once to any output group.

**Vendor cross-check.**

| Tool | Same data, same query, expected output |
|:---|:---|
| Looker (symmetric aggregates on, declared PK on each view) | `170 → 300, 180 → 50` |
| Tableau (Relationships, 2020.2+) | `170 → 300, 180 → 50` |
| Power BI (bridge with single-direction filter + `SUMX(DISTINCT(...))`) | `170 → 300, 180 → 50` |
| Naive SQL join (no de-duplication) | `170 → 400, 180 → 50` — **wrong** |

An OSI implementation that matches the naive-SQL number on this fixture
is non-conformant on D-026, regardless of how it generates the SQL.

#### T-016 — Bare non-distributive aggregate over a bridge is rejected

**Anchors:** §4.5, §6.1, §6.6.1 · D-027
**Fixture:** F-BRIDGE.

**Query:**
```yaml
Dimensions: [actors.height]
Measures:   [AVG(movies.gross)]
```

**Expected:**
```yaml
error:
  code: E_UNSAFE_REAGGREGATION
  anchor: §6.1
  must_contain: ["AVG", "non-distributive", "nested", "AVG(AVG"]
```

The diagnostic MUST suggest the nested form (e.g., `AVG(AVG(movies.gross))`
to mean "average across actors of their per-actor average gross").

#### T-017 — Nested aggregation over a bridge succeeds with per-endpoint plan

**Anchors:** §4.5 form (1), §6.6.1 · D-020, D-027
**Fixture:** F-BRIDGE.

**Query:**
```yaml
Dimensions: [actors.height]
Measures:
  - AVG(AVG(movies.gross)) AS avg_of_per_actor_avg
```

**Expected:**

| height | avg_of_per_actor_avg |
|-------:|---------------------:|
| 170    | 125.00               |   ← AVG(per-actor avg) = AVG(Alice=150, Bob=100) = 125
| 180    | 50.00                |   ← AVG(Carol=50) = 50

The inner `AVG` runs at actor grain (per actor: Alice averages M10/M11
= 150; Bob averages M10 = 100; Carol averages M12 = 50); the outer `AVG`
runs at query grain (per height). This is the explicit case where the
per-endpoint-first plan is correct and produces different numbers from
T-015's de-duplication plan — and that's the point of requiring nested
aggregation: user intent is now unambiguous.

#### ~~T-018~~ — Deferred (was: `EXISTS_IN` compiles to `EXISTS`)

The original T-018 exercised the semi-join filter form
(`EXISTS_IN` / `NOT EXISTS_IN`). That construct has been **removed
from the Foundation** and moved to a separate follow-up proposal that
will specify its surface syntax, NULL-safety guarantees, and
compilation contract in full. This test will be reinstated (and
expanded — positive case, `NOT`-form, NULL on both sides) once the
EXISTS_IN proposal lands.

Until then: the Foundation has no semi-join construct, and there is
nothing for this slot to test. The slot is retained to keep T-NNN
numbering stable; future re-use of `T-018` for an unrelated test
would be misleading.

---

### 4.F Path resolution & namespacing

#### T-019 — Two relationships between the same datasets raise `E_AMBIGUOUS_PATH`

**Anchors:** §6.7 · D-018a
**Fixture:** F-AMBIG.

**Query:**
```yaml
Dimensions: [users.region]
Measures:   [COUNT(orders.id)]
```

**Expected:**
```yaml
error:
  code: E_AMBIGUOUS_PATH
  anchor: §6.7
  must_contain: ["order_placed_by", "order_fulfilled_by"]    # both candidate paths named
```

The diagnostic MUST list **every** candidate relationship by name so
the user can pick one (the planned remedy is to annotate the query
with the relationship to use; see §10).

#### T-020 — Bare-name references resolve to the global namespace, not a dataset

**Anchors:** §4.6 · D-006
**Fixture:** F-PRELUDE. Declare `orders.total_revenue = SUM(amount)` as a
*dataset-scoped* metric.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [total_revenue]              # bare name, not qualified
```

**Expected:**
```yaml
error:
  code: E_NAME_NOT_FOUND
  anchor: §4.6
  must_contain: ["total_revenue", "global", "orders.total_revenue"]
```

The bare name MUST NOT silently match `orders.total_revenue`. Using
the dataset-scoped metric requires the qualified reference:

**Query (positive case):**
```yaml
Measures: [orders.total_revenue]
```

Resolves and returns the per-region revenue from T-001.

---

### 4.G Grain error contracts

#### T-021 — `COUNT(DISTINCT)` under fan-out raises `E_UNSAFE_REAGGREGATION`

**Anchors:** §6.1 Starting Grain, §7.2 · D-022
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [orders.status]
Measures:   [COUNT(DISTINCT customers.id) AS unique_customers]
```

**Expected:**
```yaml
error:
  code: E_UNSAFE_REAGGREGATION
  anchor: §6.1
  must_contain: ["COUNT(DISTINCT)", "holistic", "pre-aggregate"]
```

The aggregate is holistic and reads a column from the one-side
(`customers.id`) over a join that fans out to orders. The diagnostic
MUST identify the aggregate, classify it as holistic, and suggest a
pre-aggregation at the customer grain.

For comparison, swapping in `SUM(orders.amount)` (distributive) on the
same shape succeeds with no error — this is the discriminator between
D-022 and the safe-by-pre-aggregation case.

#### T-022 — Fan-out in a scalar query raises `E_FAN_OUT_IN_SCALAR_QUERY`

**Anchors:** §6.1 Final Grain, §5.1.2 · D-023
**Fixture:** F-PRELUDE.

**Query (scalar):**
```yaml
Fields: [customers.region, orders.id]
```

**Expected:**
```yaml
error:
  code: E_FAN_OUT_IN_SCALAR_QUERY
  anchor: §5.1.2
  must_contain: ["scalar", "fan-out", "customers", "orders"]
```

The scalar query has no aggregation step in which to absorb the
fan-out; either the user must aggregate one side or switch to an
aggregation query. The diagnostic MUST suggest both remedies.

#### T-023 — Row-level reference to a finer grain raises `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`

**Anchors:** §6.1 Starting Grain · D-024
**Fixture:** F-PRELUDE. Declare this *invalid* field on `customers`:

```yaml
fields:
  - name: customers.first_order_amount
    expression: "orders.amount"          # bare row-level reference to higher-grain table
```

**Expected (at model load or first query referencing the field):**
```yaml
error:
  code: E_UNAGGREGATED_FINER_GRAIN_REFERENCE
  anchor: §6.1
  must_contain: ["orders.amount", "aggregate", "customers"]
```

The remedy is to either wrap the reference in an aggregate
(`SUM(orders.amount)` — implicit home-grain aggregation per D-003),
or pull the value from a coarser-grain dataset where it is already
at the home grain. The diagnostic MUST surface both options.

---

### 4.H Common-clause semantics

This section pins behaviour that applies to **both** query shapes:
predicate-list interpretation, `Order By` NULL handling, `Limit`
without `Order By`, and home-grain scalar usability in `Where`.

#### T-024 — Boolean home-grain scalar usable in `Where`

**Anchors:** §4.3 routing rules, §6.1 Semantic 2 · D-005 (e)
**Fixture:** F-PRELUDE.

**Model addition:**
```yaml
fields:
  - dataset: customers
    name: has_completed_orders
    expression: "COUNT(orders.id WHERE orders.status = 'completed') > 0"
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [COUNT(customers.id) AS customer_count]
Where:      ["customers.has_completed_orders"]
```

**Expected:**

| region | customer_count |
|:-------|---------------:|
| EAST   | 1 |   ← Customer 1 (orders 101, 102 completed). Customer 2 has only pending order 103, excluded.
| WEST   | 1 |   ← Customer 3 (order 104 completed)

No error. The field `has_completed_orders` contains `COUNT(orders.*) > 0` —
an aggregate over a finer-grain dataset — but it resolves at customer
grain via implicit home-grain aggregation (§4.3), so its **resolved
shape** is a boolean scalar at customer grain. By D-005 (e), it is
therefore valid in `Where` for any query at customer grain or coarser,
where it filters customers pre-aggregation. An engine that classifies
fields by surface syntax (i.e., "contains `COUNT`, therefore route to
`Having`") would raise `E_WRONG_PREDICATE_LOCATION` and fail this test.

---

#### T-026 — Outer `Order By` defaults to `NULLS LAST`

**Anchors:** §5.1 Common clause semantics, §6.10.2 · D-014, D-029
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue]
Order By:   ["customers.region ASC"]
```

**Expected (single-measure ⇒ Plan A; NORTH absent — see T-001):**

| region | revenue |
|:-------|--------:|
| EAST   | 350.00  |
| WEST   | 75.00   |
| NULL   | 30.00   |   ← orphan-bucket sorts LAST (NULLS LAST default)

The non-NULL `region` values sort by ASC; the orphan `NULL`-region
bucket sorts last per the `NULLS LAST` default.

Compilation MUST emit explicit `NULLS LAST` into the compiled SQL even
though the query body did not say so:

```yaml
sql_contract:
  forbidden_substrings:
    - "ORDER BY .* ASC$"      # bare ASC without explicit NULLS LAST
  required_substrings:
    - "ORDER BY .* ASC NULLS LAST"
```

This MUST be byte-stable across compilations (D-014) and MUST agree
across dialects (Snowflake / Databricks / Postgres) even though each
dialect's *native* default differs. An engine that omits the explicit
`NULLS LAST` from compiled SQL fails on the SQL contract, even if the
runtime row order happens to match.

---

#### T-028 — `Where: [P1, P2]` is `P1 AND P2`

**Anchors:** §5.1 Common clause semantics · D-014
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue]
Where:
  - "orders.status = 'completed'"
  - "orders.amount > 60"
```

**Expected (single-measure ⇒ Plan A; NORTH absent — see T-001):**

| region | revenue |
|:-------|--------:|
| EAST   | 100.00  |   ← only order 101 (status=completed, amount=100). Order 102 (amount=50) excluded by amount filter. Order 103 (pending) excluded by status filter.
| WEST   | 75.00   |   ← order 104 (status=completed, amount=75)
| NULL   | 200.00  |   ← orphan 105 (status=completed, amount=200)

The result MUST equal what a single equivalent `Where:` entry
`"orders.status = 'completed' AND orders.amount > 60"` produces — same
rows, same numbers, byte-identical compiled SQL (after alias
normalisation). An engine that interprets a predicate list as `OR`
would include order 102 (status=completed, amount=50) and inflate
EAST to `150` — a runtime test failure.

---

#### T-052 — `Limit` without `Order By` compiles and runs

**Anchors:** §5.1 Common clause semantics · D-014
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue]
Limit:      2
```

**Expected:**

(a) **No error.** The query compiles successfully — `Limit` without
    `Order By` is a legitimate (if non-deterministic) BI query.

(b) **No warning required.** The Foundation does NOT mandate that
    engines emit a diagnostic. An engine that emits a "non-deterministic
    Limit" advisory is conformant; an engine that emits no diagnostic at
    all is also conformant.

(c) **Compiled SQL is byte-stable (D-014).** Two compilations of the
    same `(model, query, dialect)` MUST produce byte-identical SQL —
    the *output rows* may differ between runs of that SQL (since the
    engine is free to pick any two rows), but the SQL string itself is
    deterministic.

(d) **Result has exactly two rows**, both with valid `region` values
    from the result set `{EAST, WEST, NORTH, NULL}`. Specific row
    selection is engine-defined.

---

### 4.I Window functions

The Foundation supports a defined subset of SQL window functions
(§6.10). This section exercises both the supported shapes (with strong
result expectations) and the rejected shapes (with explicit error
codes).

#### T-027 — `ORDER BY` inside `OVER (...)` defaults to `NULLS LAST`

**Anchors:** §6.10.2 · D-029
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Fields:
  - orders.id
  - "ROW_NUMBER() OVER (PARTITION BY orders.customer_id ORDER BY orders.amount DESC) AS rn"
Where:
  - "orders.status = 'completed'"
```

**Expected:** the compiled SQL MUST contain `ORDER BY ... DESC NULLS
LAST` inside the `OVER (...)` clause, even though the query body did
not specify it. Engines compiling to Snowflake (native `DESC NULLS
FIRST`) MUST emit the explicit clause to override the dialect default.

```yaml
sql_contract:
  required_substrings:
    - "OVER (.*ORDER BY .* DESC NULLS LAST.*)"
```

The result row set: every `(customer_id, amount)` group's NULL `amount`
rows (if any existed) would sort after non-NULL rows. In F-PRELUDE all
orders have non-NULL `amount`, so this test exercises the SQL contract
above rather than data NULLs.

---

#### T-029 — Window function in `Where` is rejected

**Anchors:** §6.10.3 · D-030 (E_WINDOW_IN_WHERE)
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [orders.customer_id]
Measures:   [SUM(orders.amount) AS revenue]
Where:
  - "ROW_NUMBER() OVER (PARTITION BY orders.customer_id ORDER BY orders.amount) = 1"
```

**Expected:** the engine MUST reject:

```yaml
error:
  code: E_WINDOW_IN_WHERE
  anchor: §6.10.3
  must_contain: ["window", "Where", "Qualify", "subquery"]
```

The diagnostic MUST mention both supported alternatives: (a) move the
predicate into a `Having` or `Qualify` clause, or (b) wrap the windowed
expression in a derived field and filter that field at a coarser grain.

---

#### T-030 — Nested window functions rejected

**Anchors:** §6.10.4 · D-031
**Fixture:** F-PRELUDE.

**Query (illegal — window over window):**
```yaml
Fields:
  - orders.id
  - "SUM(SUM(orders.amount) OVER (PARTITION BY orders.customer_id)) OVER ()"
```

**Expected:** rejected at compile time:

```yaml
error:
  code: E_NESTED_WINDOW
  anchor: §6.10.4
  must_contain: ["nested window", "single window", "derived field"]
```

This matches every major SQL dialect (Snowflake, Databricks, Postgres,
BigQuery all reject nested windows). The diagnostic MUST suggest
factoring the inner window into a derived field.

---

#### T-031 — Running-total window accepted (the canonical positive)

**Anchors:** §6.10.1, §6.10.2 · D-030 (positive)
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Fields:
  - orders.id
  - orders.customer_id
  - orders.amount
  - "SUM(orders.amount) OVER (PARTITION BY orders.customer_id ORDER BY orders.id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total"
Where:
  - "orders.customer_id IS NOT NULL"
Order By:
  - "orders.customer_id ASC"
  - "orders.id ASC"
```

**Expected:**

| id  | customer_id | amount | running_total |
|----:|------------:|-------:|--------------:|
| 101 |           1 | 100.00 | 100.00        |
| 102 |           1 |  50.00 | 150.00        |
| 103 |           2 | 200.00 | 200.00        |
| 104 |           3 |  75.00 |  75.00        |

The compiled SQL MUST contain explicit `ROWS BETWEEN UNBOUNDED
PRECEDING AND CURRENT ROW` (the literal frame; not a synonym), and the
`OVER (...)` `ORDER BY` MUST include the `NULLS LAST` default
resolution.

---

#### T-032 — `ROW_NUMBER` with outer `Where` filter (qualify-style pattern)

**Anchors:** §6.10.1, §6.10.3 · D-030 (positive)
**Fixture:** F-PRELUDE.

**Strategy:** the legal way to "filter by `ROW_NUMBER = 1`" is to
expose the window as a derived field and filter at a coarser grain,
**OR** to use the engine's `Qualify`-style mechanism (deferred —
§10). This test exercises the derived-field strategy.

**Model addition:**
```yaml
fields:
  - dataset: orders
    name: order_rank_in_customer
    expression: "ROW_NUMBER() OVER (PARTITION BY orders.customer_id ORDER BY orders.amount DESC, orders.id ASC)"
```

**Query:**
```yaml
Fields:
  - orders.id
  - orders.customer_id
  - orders.amount
  - orders.order_rank_in_customer
Where:
  - "orders.order_rank_in_customer = 1"
```

**Expected:** the engine MUST compile the inner window in a CTE / inline
subquery and the outer `Where` MUST filter that subquery's result —
NEVER push the window inside the outer `WHERE`. The result is one row
per customer (their highest-amount order):

| id  | customer_id | amount | order_rank_in_customer |
|----:|------------:|-------:|-----------------------:|
| 101 |           1 | 100.00 |                      1 |
| 103 |           2 | 200.00 |                      1 |
| 104 |           3 |  75.00 |                      1 |
| 105 |          99 | 200.00 |                      1 |

`Order By` was omitted; engines are not required to return rows in any
specific order, but the row *content* MUST match.

---

#### T-033 — Composing onto a windowed metric is rejected

**Anchors:** §6.10.5 · E_WINDOWED_METRIC_COMPOSITION
**Fixture:** F-PRELUDE.

**Model addition:**
```yaml
metrics:
  - name: orders.running_total_by_customer
    expression: "SUM(orders.amount) OVER (PARTITION BY orders.customer_id ORDER BY orders.id)"
  - name: orders.running_total_ratio
    expression: "orders.running_total_by_customer / SUM(orders.amount)"   # composition onto windowed metric
```

**Expected:** model load (or first reference to the second metric) MUST
fail:

```yaml
error:
  code: E_WINDOWED_METRIC_COMPOSITION
  anchor: §6.10.5
  must_contain: ["window", "composition", "derived field", "§10"]
```

The diagnostic MUST acknowledge that the feature is deferred to §10
(parameterised window composition) — not a permanent prohibition.

---

#### T-034 — `GROUPS` frame mode rejected

**Anchors:** §6.10.6 · D-032
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Fields:
  - orders.id
  - "SUM(orders.amount) OVER (PARTITION BY orders.customer_id ORDER BY orders.amount GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) AS recent_sum"
```

**Expected:**

```yaml
error:
  code: E_DEFERRED_FRAME_MODE
  anchor: §6.10.6
  must_contain: ["GROUPS", "ROWS", "RANGE", "§10"]
```

Diagnostic MUST list the two supported alternatives (`ROWS` and `RANGE`)
and point at §10 as the future home of `GROUPS`.

---

#### T-035 — Parameterised frame bound rejected

**Anchors:** §6.10.6 · D-032
**Fixture:** F-PRELUDE; query parameter `:n` declared.

**Query:**
```yaml
Fields:
  - orders.id
  - "SUM(orders.amount) OVER (PARTITION BY orders.customer_id ORDER BY orders.id ROWS BETWEEN :n PRECEDING AND CURRENT ROW) AS recent_sum"
```

**Expected:**

```yaml
error:
  code: E_DEFERRED_FRAME_BOUND
  anchor: §6.10.6
  must_contain: ["parameter", "literal integer", "UNBOUNDED", "§10"]
```

Diagnostic MUST mention the three accepted bound shapes (integer
literal, `UNBOUNDED`, `CURRENT ROW`) and point at §10.

---

#### T-036 — Window over a `1 : N` fan-out is accepted (no implicit aggregation)

**Anchors:** §6.10.1, §6.7 · D-022, D-030 (positive)
**Fixture:** F-PRELUDE.

The interesting question is what happens when a window-aggregate is
defined at the customer-row grain but its argument lives at the orders
grain. Windows do NOT trigger implicit home-grain aggregation — they
require the user to be explicit about which grain the window runs over.
This test exercises the *correct* shape: the window is defined inside a
scalar query at orders grain (its native home), so no fan-out:

**Query:**
```yaml
Fields:
  - orders.id
  - orders.customer_id
  - "RANK() OVER (PARTITION BY orders.customer_id ORDER BY orders.amount DESC) AS rank_by_customer"
```

**Expected:** the query compiles at orders grain (the natural home of
the window). The result is one row per order with the customer-internal
rank:

| id  | customer_id | rank_by_customer |
|----:|------------:|-----------------:|
| 101 |           1 |                1 |   ← amount 100 > amount 50
| 102 |           1 |                2 |
| 103 |           2 |                1 |
| 104 |           3 |                1 |
| 105 |          99 |                1 |

**Companion negative — T-036b:** trying to use the same window
expression as a *field on customers* (i.e., declaring
`customers.rank_in_some_customer = RANK() OVER (...)`) MUST raise
`E_AMBIGUOUS_MEASURE_GRAIN` (D-025) — the field's home grain is
underspecified.

---

### 4.J BI-shape coverage

These are the four "shape archetypes" every real BI semantic layer
must handle. Without explicit tests, regressions can ship silently.

#### T-043 — Multi-hop `N : 1` enrichment chain

**Anchors:** §6.4 (cardinality inference), §6.6 (join defaults)
**Fixture:** F-CHAIN — a three-hop `N : 1` chain
(`order_lines → orders → customers → segments`):

```yaml
datasets:
  - { name: segments,    primary_key: [id], fields: [name] }
  - { name: customers,   primary_key: [id], fields: [segment_id, region] }
  - { name: orders,      primary_key: [id], fields: [customer_id, amount, status] }
  - { name: order_lines, primary_key: [id], fields: [order_id, sku, qty, price] }

relationships:
  - { from: customers,   from_keys: [segment_id],  to: segments,  to_keys: [id], cardinality: 1:1 → many:1 }
  - { from: orders,      from_keys: [customer_id], to: customers, to_keys: [id], cardinality: N:1 }
  - { from: order_lines, from_keys: [order_id],    to: orders,    to_keys: [id], cardinality: N:1 }
```

**Query:**
```yaml
Dimensions: [segments.name]
Measures:   [SUM(order_lines.qty * order_lines.price) AS revenue]
```

**Expected:** the planner finds a path
`order_lines → orders → customers → segments` (three `N : 1` hops).
Cardinality inference proves no fan-out (every hop is many-to-one), so
the SQL is a chain of `LEFT JOIN`s with no de-duplication step.

Compiled SQL contract:

```yaml
sql_contract:
  required_substrings:
    - "FROM .*order_lines"
    - "LEFT JOIN .*orders"
    - "LEFT JOIN .*customers"
    - "LEFT JOIN .*segments"
  forbidden_substrings:
    - "DISTINCT"    # no de-duplication needed; chain is N:1 all the way
```

Wrong join direction (e.g., `customers LEFT JOIN order_lines`) would
fan out; the test fails if the row count differs from
`COUNT(DISTINCT segments.id) + 1` (for orphan segments).

---

#### T-044 — Composite primary key / foreign key

**Anchors:** D-009 (deferred-key rejection — composite keys ARE supported
in the foundation), §6.4
**Fixture:** F-COMPOSITE — `inventory` keyed by `(store_id, sku)` with
a fact `sales` referencing the composite:

```yaml
datasets:
  - name: inventory
    primary_key: [store_id, sku]
    fields: [stock_level, reorder_point]
  - name: sales
    primary_key: [id]
    fields: [store_id, sku, qty, sale_ts]

relationships:
  - from: sales
    from_keys: [store_id, sku]
    to: inventory
    to_keys: [store_id, sku]
    cardinality: N:1
```

**Query:**
```yaml
Dimensions: [inventory.reorder_point]
Measures:   [SUM(sales.qty) AS units_sold]
```

**Expected:** the engine generates a join `ON sales.store_id =
inventory.store_id AND sales.sku = inventory.sku`. The result is one
row per distinct `reorder_point` value.

Compiled SQL contract:

```yaml
sql_contract:
  required_substrings:
    - "ON .*store_id .* AND .*sku"   # both key components in ON clause
  forbidden_substrings:
    - "ON .*store_id\\s+\\)"          # only one key component
    - "ON .*sku\\s+\\)"
```

An engine that uses only one key component fails this test (it would
produce row duplication if any `(store_id, sku)` collision exists, or
silent NULLs).

---

#### T-045 — Two-measure stitch with bridge resolution

**Anchors:** §6.8 (M:N), §6.8.2 (stitch) · D-026
**Fixture:** F-BRIDGE — `customers ⇌ orders` with `customers ⇌
returns` and a separate `customers ⇌ campaigns` many-to-many via
`customer_campaigns` bridge.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount) AS revenue
  - COUNT(DISTINCT campaigns.id) AS active_campaign_count
```

**Expected:** the planner must:

1. Aggregate `orders` to customer grain (per-customer revenue).
2. Resolve the M:N path `customers → customer_campaigns → campaigns`
   with bridge de-duplication per D-026 (count each campaign at most
   once per customer).
3. Aggregate each branch to region grain.
4. `FULL OUTER` stitch on region (multi-measure ⇒ §6.6 row 3 / §6.2
   step 7).

Result: every region present in *either* branch's join path appears
(the union of "regions reached by an order" and "regions reached by
a campaign-association"). A region with neither order nor campaign
activity does NOT appear — same Plan A logic as T-001, just applied
per-branch and then unioned by the FULL OUTER. Missing-side cells
follow standard SQL per §6.11: `revenue` (a `SUM`) returns `NULL`
when no orders contribute to the region; `active_campaign_count` (a
`COUNT DISTINCT`) returns `0` when no campaigns contribute. The
differing empty-set values across the two measure types is the
*point* of the test: an engine that returns the same value for both
(e.g., both `0` or both `NULL`) is doing a Foundation-level rewrite
the spec forbids.

The CRITICAL invariant: `active_campaign_count` MUST NOT be inflated by
the M:N fan-out. An engine that joins everything first then groups
will inflate the count and fail; the engine MUST aggregate each branch
independently.

```yaml
sql_contract:
  required_substrings:
    - "WITH .*orders_branch"      # or two named CTEs
    - "FULL OUTER JOIN"
```

---

#### T-046 — Reflexive (self-referential) relationship

**Anchors:** §6.4 (cardinality), §6.6 (join defaults), §6.9 (path
resolution with roles)
**Fixture:** F-REFLEXIVE — `employees` references itself via
`manager_id`:

```yaml
datasets:
  - name: employees
    primary_key: [id]
    fields: [name, manager_id, region]

relationships:
  - name: reports_to
    from: employees
    from_keys: [manager_id]
    to: employees
    to_keys: [id]
    cardinality: N:1
    role: manager       # qualifier — the "to" side acts as `manager`
```

**Query:**
```yaml
Dimensions:
  - "employees{role=manager}.region AS manager_region"
Measures:
  - COUNT(employees.id) AS direct_report_count
```

**Expected:** the planner generates `employees AS employee LEFT JOIN
employees AS manager ON employee.manager_id = manager.id` and groups by
`manager.region`. Each employee is counted exactly once.

```yaml
sql_contract:
  required_substrings:
    - "FROM .*employees"
    - "LEFT JOIN .*employees"        # self-join with distinct aliases
```

An engine that fails to disambiguate the two `employees` references
either errors with `E_AMBIGUOUS_PATH` (if the role qualifier is not
honoured) or silently produces wrong counts.

---

### 4.K Boundary, empty, and NULL cases

#### T-025 — Scalar query with two unrelated row-level facts is rejected

**Anchors:** §5.1.2, Appendix B · D-023 (extended)
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Fields:
  - orders.amount
  - returns.amount
  - customers.region
```

**Expected:**

```yaml
error:
  code: E_FAN_OUT_IN_SCALAR_QUERY
  anchor: §5.1.2
  must_contain: ["orders", "returns", "aggregation", "single home"]
```

`orders` and `returns` are independent facts both on the many-side of
`customers`. A row-level scalar query referencing fields from both
would have to either fan out (replicate orders rows per return row, or
vice versa) or pick a single home — neither is unambiguous. The error
MUST suggest two resolutions: convert to an aggregation query, or pick
a single fact as the home. (A semi-join filter form is deferred to a
future proposal — see the §6.8 deferred-feature note.)

---

#### T-040 — `E_NO_PATH` when no relationship connects two datasets

**Anchors:** §6.9 · D-018b
**Fixture:** F-NOPATH — two datasets with no declared relationship
between them:

```yaml
datasets:
  - name: customers
    primary_key: [id]
    fields: [name, region]
  - name: weather_observations    # no relationship to customers
    primary_key: [id]
    fields: [station_id, observation_date, temperature]
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [AVG(weather_observations.temperature) AS avg_temp]
```

**Expected:**

```yaml
error:
  code: E_NO_PATH
  anchor: §6.9
  must_contain: ["customers", "weather_observations", "no path", "relationship"]
```

Diagnostic MUST name both endpoint datasets and explicitly state that
no declared `relationships` entry connects them.

---

#### T-041 — Reserved-name rejection

**Anchors:** §3 reserved names · D-019
**Fixture:** F-PRELUDE with a deliberately illegal addition.

**Model addition (illegal):**
```yaml
fields:
  - dataset: customers
    name: select        # reserved SQL keyword
    expression: "'placeholder'"
```

**Expected:** model load fails:

```yaml
error:
  code: E_RESERVED_NAME
  anchor: §3
  must_contain: ["select", "reserved", "rename"]
```

Diagnostic MUST quote the offending name verbatim.

---

#### T-042 — Deferred-key rejection

**Anchors:** §10 deferred keys · D-009
**Fixture:** F-PRELUDE with a deliberately illegal addition.

**Model addition (illegal — `grain: FIXED` is deferred to §10):**
```yaml
metrics:
  - name: orders.revenue_at_orders_grain
    expression: "SUM(orders.amount)"
    grain: FIXED   # deferred key
```

**Expected:**

```yaml
error:
  code: E_DEFERRED_KEY_REJECTED
  anchor: §10
  must_contain: ["grain", "deferred", "§10"]
```

The diagnostic MUST identify which deferred key was used (`grain`) and
cite §10. This is the canonical instance; the full coverage of D-009
requires one instance per deferred key — see §6 below.

---

#### T-047 — Empty-set aggregate behaviour: `SUM` → `NULL`, `COUNT` → `0` (D-033)

**Anchors:** §6.11 · D-033
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount) AS revenue
  - COUNT(orders.id)   AS order_count
  - COUNT(*)           AS row_count
```

**Expected (three measures over one fact — still a single fact source,
so Plan A applies; NORTH does not appear):**

| region | revenue | order_count | row_count |
|:-------|--------:|------------:|----------:|
| EAST   | 350.00  | 3 | 3 |
| WEST   | 75.00   | 1 | 1 |
| NULL   | 30.00   | 1 | 1 |

> Why no NORTH? All three measures are sourced from `orders` — the
> measure dataset-set is the same for all of them, so step 6 of the
> §6.2 evaluation algorithm produces one combined row set per the
> single-measure Plan A rule. NORTH (customer 4 with no orders) has
> no `orders` rows pulling it into the result. If the user wanted
> NORTH to appear, they would add a measure sourced from a different
> dataset that *does* have data for customer 4 — e.g.
> `COUNT(customers.id) AS customer_count` — which would convert the
> shape into a true multi-measure-stitch query and the FULL OUTER
> rule of §6.6 row 3 would preserve NORTH via the customers branch.

This pins the standard-SQL split codified by D-033: the `COUNT` family
(`COUNT(*)`, `COUNT(x)`, `COUNT(DISTINCT x)`) returns `0` on an empty
set because it counts rows; every other aggregate (`SUM`, `AVG`,
`MIN`, `MAX`, `MEDIAN`, percentiles, `STDDEV`, `VARIANCE`) returns
`NULL` because there is no defined result.

**Companion T-053** below covers the `MIN`/`MAX`/`AVG` branch
(non-distributive, also `NULL` over empty).

---

#### T-048 — NULL foreign key in `1 : N` enrichment

**Anchors:** §6.6 (LEFT default), §6.11
**Fixture:** F-PRELUDE-NULLFK — F-PRELUDE with one extra order row:

```sql
-- order 106: amount 80, status completed, customer_id NULL
```

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue]
```

**Expected:**

| region | revenue |
|:-------|--------:|
| EAST   | 350.00  |
| WEST   | 75.00   |
| NORTH  | 0       |
| NULL   | 110.00  |   ← orphan order 105 (30) + NULL-FK order 106 (80) — both bucketed under `region = NULL`

The result demonstrates that a NULL foreign key behaves the same as an
unmatched key (orphan row 105 with `customer_id = 99` not in
`customers`): both produce a `NULL`-region bucket. An engine that
silently drops NULL-FK rows during the join would understate the
NULL-bucket by 80 and fail.

---

#### T-049 — NULL in a dimension column

**Anchors:** §5.1.1 (Result grain), §6.6
**Fixture:** F-PRELUDE-NULLDIM — F-PRELUDE with customer 4's `region`
set to `NULL` (overriding NORTH).

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue, COUNT(customers.id) AS customer_count]
```

**Expected:**

| region | revenue | customer_count |
|:-------|--------:|---------------:|
| EAST   | 350.00  | 2 |
| WEST   | 75.00   | 1 |
| NULL   | 30.00   | 1 |   ← customer 4 (NULL region) + orphan order 105's NULL bucket merge into one row

The two distinct sources of NULL (the customer with NULL region + the
orphan order with unmatched customer) collapse into a *single* output
row. SQL `GROUP BY` treats all NULLs as one group; the Foundation
preserves this behaviour. An engine that emits two separate NULL rows
fails this test.

---

#### T-050 — Empty aggregation query is rejected

**Anchors:** §5.1.1 (At least one of `Dimensions` or `Measures` MUST be
non-empty), §6.2 normative evaluation step 1 · `E_EMPTY_AGGREGATION_QUERY`
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: []
Measures:   []
```

**Expected:**

```yaml
error:
  code: E_EMPTY_AGGREGATION_QUERY
  anchor: §5.1.1
  must_contain: ["Dimensions", "Measures", "at least one"]
```

A query with neither dimensions nor measures has no shape at all —
this is distinct from "no dimensions" (which is legal and produces a
grand total) or "no measures" (which is the `Dimensions`-only listing
shape, also legal). The diagnostic MUST make this distinction explicit.

---

#### T-051 — Empty scalar query is rejected

**Anchors:** §5.1.2, §6.2 normative evaluation step 1 · `E_EMPTY_SCALAR_QUERY`
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Fields: []
```

**Expected:**

```yaml
error:
  code: E_EMPTY_SCALAR_QUERY
  anchor: §5.1.2
  must_contain: ["Fields", "at least one", "scalar"]
```

A scalar query with no fields has no projection; it cannot be compiled
to any SQL. The diagnostic MUST mention that `Fields` requires at least
one entry.

---

#### T-053 — `AVG` / `MIN` / `MAX` over zero matching rows return `NULL`

**Anchors:** §6.11 · D-033 (non-`COUNT` branch)
**Fixture:** F-PRELUDE.

**Query:**
```yaml
Dimensions: [customers.region]
Measures:
  - AVG(orders.amount) AS avg_amount
  - MIN(orders.amount) AS min_amount
  - MAX(orders.amount) AS max_amount
```

**Expected (three measures over one fact — Plan A; NORTH absent):**

| region | avg_amount | min_amount | max_amount |
|:-------|-----------:|-----------:|-----------:|
| EAST   | 116.67     |  50.00     | 200.00     |
| WEST   |  75.00     |  75.00     |  75.00     |
| NULL   |  30.00     |  30.00     |  30.00     |

Combined with T-047 (`SUM` → `NULL` on empty, `COUNT` → `0`), this
fully pins D-033: only the `COUNT` family returns `0` over an empty
input. The NORTH row is absent for the same reason as in T-001 / T-047
— a single-fact aggregation query follows Plan A.

---

## 5. Coverage map

This map is the authoritative cross-reference. Every `D-NNN` decision
in the spec's Appendix B that has observable runtime behaviour MUST
appear here with at least one covering `T-NNN`.

| D-NNN | Decision | Covered by |
|:------|:---|:---|
| D-001 | Aggregation-query cardinality | T-001, T-050 (empty rejected) |
| D-003 | Implicit home-grain aggregation in fields | T-004, T-024 |
| D-004 | Default join types | T-001 (LEFT), T-010 (LEFT, indirect), T-011 (FULL OUTER), T-012 (CROSS), T-043 (multi-hop N:1 chain) |
| D-005 | Predicate routing by resolved expression shape | T-007, T-024 (e: boolean home-grain scalar in `Where`) |
| D-006 | Bare name → global namespace | T-020 |
| D-007 | M:N MUST be a safe rewrite | T-013, T-014 |
| D-009 | Deferred-key rejection | T-042 (canonical instance — `grain: FIXED`) |
| D-010 | Query shape determined by clauses | T-002 |
| D-011 | Bare metric in `Fields` rejected | T-003 |
| D-012 | Predicate-shape errors | T-007 (in WHERE), T-008 (in HAVING), T-009 (mixed) |
| D-014 | Determinism | §1.4 (applied to every test); explicit in T-026, T-052 (compiled SQL byte-stable) |
| D-015 | Three home-grain compilation strategies equivalent | T-004 |
| D-016 | `COUNT(*)` works | T-006, T-047 |
| ~~D-017~~ | **Deferred** — moved to a separate EXISTS_IN proposal | ~~T-018~~ (deferred slot) |
| D-018 | Path-resolution errors | T-019 (ambiguous), T-040 (E_NO_PATH) |
| D-019 | Reserved names rejected | T-041 |
| D-020 | Single-step AND nested cross-grain aggregation accepted | **T-005a (single-step SUM), T-005b (single-step AVG, heavy-weighted), T-005c (nested AVG, unweighted), T-005d (single-step SUM ≡ nested SUM), T-005e (single-step COUNT DISTINCT), T-017** |
| D-022 | Non-decomposable aggregate over fan-out | T-021, T-005e (caveat: D-022 fires on M:N decomposition, not plain 1:N) |
| D-023 | Fan-out in scalar query | T-022, T-025 (multiple unrelated row-level facts) |
| D-024 | Unaggregated finer-grain reference | T-023 |
| **D-026** | **Bridge resolution de-duplicates per `(fact, group)`** | **T-015 (flagship), T-045 (two-measure bridge)** |
| **D-027** | **Bare non-distributive aggregate over M:N rejected** | **T-016, T-017 (positive)** |
| D-029 | `Order By` defaults to `NULLS LAST` (outer + `OVER`) | T-026 (outer), T-027 (`OVER`) |
| D-030 | Windows: legal placements, `E_WINDOW_IN_WHERE` | T-029 (rejected), T-031 (running total positive), T-032 (qualify pattern), T-036 (window over `1:N`) |
| D-031 | Nested windows rejected | T-030 |
| D-032 | `ROWS` / `RANGE` accepted; `GROUPS` and parameterised bounds deferred | T-031 (positive `ROWS`), T-034 (`GROUPS` rejected), T-035 (param frame bound rejected) |
| **D-033** | **Empty-set aggregate behaviour follows standard SQL: `COUNT` family → `0`, everything else → `NULL`** | **T-011 (stitch missing-side `SUM` = `NULL`, the canonical test now that single-measure shapes drop missing-dim rows entirely per §1.6); T-045 (mixed `SUM`/`COUNT` branches); T-047, T-053 (one-fact multi-measure — `COUNT` = `0` for present dim values; missing dim values themselves are dropped per Plan A)** |
| E_WINDOWED_METRIC_COMPOSITION | Windowed metrics cannot be composed | T-033 |

### 5.1 BI-shape coverage

Independent of D-NNN coverage, these are the shapes every production
BI tool must handle. The catalog covers them as follows:

| Shape | Covered by |
|:---|:---|
| Single-table fact | T-006, T-021, T-031, T-032, T-036 |
| `N : 1` enrichment (one hop) — single-measure (Plan A) | T-001, T-002, T-010 |
| `N : 1` enrichment (multi-hop chain) | T-043 |
| Composite primary/foreign key | T-044 |
| `1 : N` cross-grain aggregation (single-step) | T-005a, T-005b, T-005e, T-017 |
| `1 : N` cross-grain aggregation (nested) | T-005c, T-005d |
| `M : N` via bridge | T-013, T-014, T-015, T-016, T-017, T-045 |
| Multi-measure stitch (`FULL OUTER`, §6.6 row 3) | T-011, T-045 |
| Semi-join filter | ~~T-018~~ (deferred to future EXISTS_IN proposal) |
| Reflexive (self-referential) | T-046 |
| Windowed metrics | T-027, T-031, T-032, T-036 |
| Empty / boundary queries | T-050, T-051, T-052 |
| NULL semantics | T-011 (stitch missing-side), T-047, T-048, T-049, T-053 |

## 6. Decisions awaiting coverage

The following `D-NNN` entries do not yet have a `T-NNN` vector. Adding
one is the natural next sprint of this catalog.

| D-NNN | Why deferred |
|:------|:---|
| D-002 | Multi-measure shared-grain composition — T-011 and T-045 cover the FULL-OUTER stitch path; D-002's "all measures resolve at the query's grain" claim is implicit in both. A direct test pinning the empty-dimensions / two-fact-no-dims case (Snowflake errata #4) is the natural next addition. |
| D-008 | No `UNSAFE`; no per-metric `joins.type` override. Test is a parser-level rejection — better placed in a parser-conformance suite than this row-set catalog. |
| D-009 | T-042 is the canonical instance; full coverage requires one test per deferred key (six total). Mechanically generated; out of scope for this seed but trivial follow-up. |
| D-013 | Same-named expressions reachable via qualification — needs a fixture with a deliberate name collision; planned for the next pass. |
| D-021 | Structured `expression` slot — parser/codegen-conformance. |
| D-025 | `E_AMBIGUOUS_MEASURE_GRAIN` catch-all — the D-025 enumeration lists the known cases; T-036b covers one (windowed field with no declared home grain). Remaining enumerated cases are mechanical follow-ups. |

When this list shrinks to empty, every `D-NNN` in Appendix B has an
executable witness in this file and the Foundation Conformance Suite is
self-contained.
