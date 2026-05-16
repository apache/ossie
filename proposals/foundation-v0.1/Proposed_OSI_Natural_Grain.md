# Proposed OSI Extension — Model-Level `natural_grain` Declaration

**Status:** Proposed (out of scope for the Foundation)
**Relationship to the Foundation:** Additive. The Foundation ships without this feature; this proposal layers on top of [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md) without changing any of its existing contracts.
**Replaces:** the model-level `natural grain` paragraphs that were drafted into `Proposed_OSI_Semantics.md` §6.2 Starting Grain and "Determining Datasets Involved In a Query". Those paragraphs have been removed from the Foundation and are restated here.

---

## 1. Vocabulary disambiguation (READ FIRST)

The phrase "natural grain" is used in two distinct ways across OSI documents. The Foundation needs the first; this proposal introduces the second.

| Term | Meaning | Status |
|:---|:---|:---|
| **natural grain of a dataset** (generic vocabulary) | The grain at which a dataset naturally lives — its primary key (or any declared unique key). Equivalent to "table grain" or "home grain". A fact about every dataset, not something the modeler declares. | **Foundation** — used throughout `Proposed_OSI_Semantics.md` §4.2.1, §4.3, and §6.2. |
| **`natural_grain` declaration** (this proposal) | An optional model-level setting that names *one* dataset whose natural-grain rows MUST be implicitly present in every query against the model, regardless of which fields the query references. | **Proposed** — not in the Foundation. |

The rest of this document is about the second meaning. Wherever it appears in backticks (`natural_grain`) it refers to the model-level declaration, never the generic vocabulary.

---

## 2. Motivation

Foundation §6.2 ("Starting Grain") says that for each query, the engine identifies the datasets touched by the query, resolves a join path, and follows the `1`-side of joins to find the finest grain involved. Two subtle properties fall out of this:

- **Each metric may have its own starting grain** when the query mixes facts from multiple roots.
- **A query that touches only dimension columns** (no measures) returns the full dimension domain — the engine has no reason to filter to "values actually used by some fact" because no fact is in the query.

Both behaviours are correct and useful, but some BI conventions want the *opposite* — every query implicitly anchored to one designated fact, so that dimension lookups, dimension-only browses, and scalar queries are all restricted to "values reachable from this fact." The clearest example:

- **Looker fact-rooted explores.** A LookML explore rooted on `orders` produces dimension queries that always pass through the `orders` table, even when the dimension comes from `users` or `dates`. Users get "regions with orders," not "all regions." This is closer to "the explore's universe is rows of orders" than to LookML's `always_filter` (which is per-explore filter expressions, a related but distinct mechanism).

The Foundation can't express this without forcing every query to spell out the fact-of-interest explicitly. `natural_grain` lets the modeler push that decision down into the model once.

---

## 3. Specification

### 3.1 Declaration syntax

A model MAY declare exactly one `natural_grain` at the top level:

```yaml
natural_grain: orders          # name of a declared dataset

datasets:
  - name: orders
    primary_key: [id]
    fields: [...]
  - name: customers
    primary_key: [id]
    fields: [...]
relationships:
  - ...
```

- `natural_grain` MUST be the name of a declared dataset in the same model.
- A model MAY omit `natural_grain` entirely; the Foundation behaviour (per §3.4 below) is the default.
- A model MAY declare at most one `natural_grain`. Multi-fact "natural grains" are out of scope; the modeller picks the single fact whose existence the query is implicitly anchored on.

### 3.2 Effect on Foundation §6.2 — Starting Grain

Without `natural_grain` (Foundation behaviour):

- Each metric's starting grain is derived independently from the datasets it touches and the join path the engine resolves.

With `natural_grain` set:

- Every query implicitly includes the `natural_grain` dataset in its dataset-set, even when no field of the query references it.
- The starting grain MUST be either the `natural_grain` dataset itself OR a dataset reachable from `natural_grain` along an `N : 1` chain (a coarser-or-equal grain).
- A query whose otherwise-derived starting grain would be *finer* than `natural_grain` MUST pre-aggregate the finer dataset(s) to a grain that is `N : 1` from `natural_grain`. If no such pre-aggregation is safe (the finer dataset is fan-out-prone in a way `natural_grain` cannot absorb), the engine MUST raise `E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE` (proposed; see §6 below).

### 3.3 Effect on Foundation §6.2 — Determining Datasets Involved In a Query

This is the user-visible difference, and it is what motivates the proposal.

Without `natural_grain` (Foundation behaviour):

> The datasets involved in a query are exactly those required to fulfil the field references in `Dimensions`, `Measures`, `Where`, `Having`, and `Fields`. A query for `Dimensions: [customers.region]` and no measures will read `customers` only and return every region present in `customers`.

With `natural_grain: orders`:

> The datasets involved in a query are the Foundation-derived set **plus `orders`**, joined along the unambiguous `N : 1` path from `orders` to the referenced dimensions. A query for `Dimensions: [customers.region]` and no measures returns every region in `customers` *that has at least one matching `orders` row*. Regions with no orders are silently filtered out.

The "silent filter" is not silent in the spec — it is the literal contract of the model. A modeler who sets `natural_grain: orders` is asserting "my model's universe is rows of `orders`; dimensions exist only insofar as orders point to them."

### 3.4 Effect on Foundation §6.2 — Final Grain

For an **aggregation query**, `natural_grain` has no effect on Final Grain — the query's dimensions still define the final grain unchanged from the Foundation rule.

For a **scalar query** (Foundation §5.1.2), the starting grain and final grain are the same row set by construction, so any change to the starting-grain row set (per §3.2 above) is also a change to the final-grain row set. Concretely: a scalar query whose home dataset is `customers` and whose only `Fields` are columns of `customers` returns one row per customer with no orders in the Foundation, but only those customers reachable from `natural_grain: orders` when the declaration is present. The set of rows in the result is filtered by `natural_grain` for exactly the same reason it would be filtered in a dimension-only aggregation query.

### 3.5 Interaction with implicit home-grain aggregation (§4.3, D-003)

`natural_grain` does NOT change implicit home-grain aggregation. A field expression like `customers.lifetime_value = SUM(orders.amount)` continues to aggregate at `customers`'s home grain (one value per customer), regardless of what `natural_grain` is set to. The `natural_grain` declaration is about *query-level* dataset inclusion, not about *field-definition-level* grain resolution.

### 3.6 Interaction with multi-fact queries (§6.8.2 stitch)

A query that references measures from multiple facts uses the Foundation's stitch plan (§6.8.2). `natural_grain` does not constrain which fact appears in the stitch — both still appear, and the merge is unchanged.

`natural_grain` is intentionally weaker than the strict §3.3 rule when other facts are present in the query, because forcing the strict rule across a multi-fact stitch would silently remove rows from facts that are not the natural grain — directly contradicting Foundation Semantic 3 ("no fact loses its groups"). The rule for multi-fact queries:

> **Dimension domain rule (multi-fact).** When a query references measures from facts other than `natural_grain`, the dimension domain is the **union** of dimension values reachable from `natural_grain` *and* from each other fact in the query. `natural_grain` is implicitly added to the dataset-set as in §3.3 (so it can contribute its share), but it does not filter out dimension values reachable through other facts.

**Worked example.** Model with `natural_grain: orders`, two facts `orders` and `returns`, both `N : 1` to `customers`:

`customers`:

| id | region |
|:---:|:---|
| C1 | EAST |
| C2 | WEST |
| C3 | NORTH |   ← no orders, has returns

`orders`: only customers C1, C2 have rows.
`returns`: customer C3 has a return; C1 also has a return.

Query:

```yaml
Dimensions: [customers.region]
Measures:
  - SUM(orders.amount)  AS revenue
  - SUM(returns.amount) AS returns_total
```

Foundation behaviour without `natural_grain`: every region appearing in *either* fact appears (Semantic 3 stitch). Result includes EAST, WEST, NORTH.

With `natural_grain: orders`: per the union rule above, the dimension domain is regions reachable from orders (`{EAST, WEST}`) ∪ regions reachable from returns (`{EAST, NORTH}`) = `{EAST, WEST, NORTH}`. Result includes all three regions. **Semantic 3 still holds** — no fact loses its groups, even under `natural_grain`.

This means `natural_grain` only narrows dimension domains in queries where it is the sole fact path — which is the case the proposal's motivation (§2) actually targets (LookML fact-rooted explores, dimension pickers anchored on one fact). In multi-fact queries, the strict interpretation would conflict with Semantic 3, so the union form is normative.

### 3.7 Interaction with `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` (Foundation §6.2, D-024)

The Foundation's `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` rule (a row-level reference to a field at a grain finer than the consuming home grain MUST fail) is unchanged. With `natural_grain` set, the rule is applied against the chain of consuming grains as before; `natural_grain` is one possible "consuming grain" in the chain but does not change the error contract.

**Worked example.** Model with `natural_grain: orders`, datasets `customers` (PK `id`) and `orders` (PK `id`, FK `customer_id`). The implicit "include `orders`" rule from §3.3 makes `orders` part of every query's dataset-set, but it does **not** make `orders` the home grain of a field defined on `customers`. A field

```yaml
- name: customers.bad_field
  expression: orders.amount        # row-level reference, no aggregate
```

is rejected with `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` exactly as it would be without `natural_grain`. The home grain of the field is `customers.id`; `orders` is finer; a row-level reference is illegal. The fix is the standard one — wrap in an aggregate:

```yaml
- name: customers.lifetime_value
  expression: SUM(orders.amount)
```

This resolves to a per-customer scalar via implicit home-grain aggregation (Foundation §4.3.1 / D-003), regardless of whether `natural_grain` is set. The "implicit dataset inclusion" of `natural_grain` is purely about the *query-level* dataset-set (which datasets are visible to the planner), not about the *field-level* home grain of any expression.

---

## 4. Examples

### 4.1 Model used in all examples

```yaml
natural_grain: orders            # optional; toggled per example

datasets:
  - name: customers
    primary_key: [id]
    fields:
      - { name: id }
      - { name: region }

  - name: orders
    primary_key: [id]
    fields:
      - { name: id }
      - { name: customer_id }
      - { name: amount }

relationships:
  - { name: orders_to_customer, from: orders, to: customers, from_columns: [customer_id], to_columns: [id] }
```

**Data:**

`customers`:

| id | region |
|:--:|:-------|
| 1  | EAST   |
| 2  | WEST   |
| 3  | NORTH  |   ← no orders

`orders`:

| id  | customer_id | amount |
|:---:|:-----------:|-------:|
| 101 | 1           | 100    |
| 102 | 2           | 50     |

### 4.2 Query: dimension only, no measure

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   []
```

**Foundation behaviour (no `natural_grain` set):**

| region |
|:-------|
| EAST   |
| WEST   |
| NORTH  |   ← present even though no orders

**With `natural_grain: orders`:**

| region |
|:-------|
| EAST   |
| WEST   |
                ← NORTH is implicitly filtered: no orders, so the "orders universe" does not include it

### 4.3 Query: dimension + fact measure

**Query:**
```yaml
Dimensions: [customers.region]
Measures:   [SUM(orders.amount) AS revenue]
```

**Foundation behaviour (no `natural_grain` set):**

| region | revenue |
|:-------|--------:|
| EAST   | 100     |
| WEST   | 50      |
| NORTH  | NULL    |   ← surfaced because of `LEFT` enrichment in §6.6, even though no orders point at NORTH

**With `natural_grain: orders` (strict interpretation, per §3.3):**

| region | revenue |
|:-------|--------:|
| EAST   | 100     |
| WEST   | 50      |
                          ← NORTH is implicitly filtered: the `natural_grain` rule restricts dimensions to those reachable from `orders`, and no `orders.customer_id` points to a NORTH customer.

The strict interpretation makes `natural_grain` a uniform contract: the dimension domain in the result is always "values reachable from `natural_grain`," regardless of whether the query happens to include a measure that already joins to `natural_grain`. This is the rule a modeller who set `natural_grain` is asking for; without it the dimension domain would silently flip between two definitions depending on what else the query references.

### 4.4 Query: scalar query under `natural_grain`

**Query:**
```yaml
Fields: [customers.id, customers.region]
```

**Foundation behaviour (no `natural_grain` set):** one row per customer, including the `NORTH` customer with no orders.

**With `natural_grain: orders` (per §3.4):** only customers reachable from `orders` appear. The `NORTH` customer is filtered out.

This is the scalar-query analogue of §4.2 — `natural_grain` reshapes the row set of any query whose final grain is the natural grain or a coarser ancestor of it.

### 4.5 Query: dimension + measure from a different root (multi-fact)

If the model had a second fact `returns` not covered by `natural_grain`, a query mixing `orders` and `returns` measures would use the stitch plan from Foundation §6.8.2 unchanged; `natural_grain` does not alter which facts appear in the stitch.

---

## 5. Trade-offs

| Property | No `natural_grain` (Foundation default) | `natural_grain` set |
|:---|:---|:---|
| Result of dimension-only query | All dimension values | Only those reachable from `natural_grain` |
| Predictability across queries | Lower — same dimension can return different domains depending on what else is queried | Higher — every query is implicitly anchored on one fact |
| Cost (datasets read) | Lower — only what the query references | Higher — `natural_grain` is always read, even if not referenced |
| Match to Tableau extracts / Looker fact-rooted explores | Poor | Good |
| Match to "ad-hoc dimensional browsing" UX | Good | Poor — dimensions are silently filtered |
| Compatibility with multi-fact / chasm patterns | Native | Limited — `natural_grain` picks one fact; others must be reached via stitch |

The `natural_grain` declaration is essentially a **modelling-time pre-commitment to one fact's universe**. It trades flexibility for predictability and BI-tool familiarity. The Foundation defaults to the flexible option because it composes better with the Foundation's other rules (especially multi-fact stitch).

---

## 6. Conformance decisions (added when this proposal lands)

If/when `natural_grain` is adopted into the Foundation or shipped as a recognised extension, the following entries SHOULD be added to `Proposed_OSI_Semantics.md` Appendix B:

| ID | Decision | Anchored in | Test shape |
|:---|:---|:---|:---|
| **D-NG-1** | A model MAY declare exactly one `natural_grain`. The value MUST be the name of a declared dataset. Declaring two `natural_grain` keys, or referencing a non-existent dataset, MUST raise `E_INVALID_NATURAL_GRAIN`. | this proposal §3.1 | Two YAML files: one with `natural_grain: not_a_dataset` ⇒ error; one with two `natural_grain:` keys ⇒ parser-level error. |
| **D-NG-2** | With `natural_grain: X` set, every query implicitly includes dataset `X` in its dataset-set, joined via the unambiguous `N : 1` path to the query's referenced dimensions. A dimension-only query returns the dimension values reachable from `X`, not the full dimension domain. | this proposal §3.3 | Fixture `customers + orders` with one orphan customer (no orders). Query `Dimensions: [customers.region]` ⇒ orphan-customer region excluded. Same query without `natural_grain` ⇒ orphan-customer region included. |
| **D-NG-3** | A query whose otherwise-derived starting grain would be *finer* than `natural_grain` MUST pre-aggregate to a grain that is `N : 1` from `natural_grain`. If no safe pre-aggregation exists, raise `E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE`. The error code follows the Foundation `E_*` convention; the descriptive name makes the failure mode clear without leaking the proposal name into the diagnostic. | this proposal §3.2 | Fixture with `order_lines` (finer than `orders`) and `natural_grain: orders`. Query referencing both `order_lines.sku` and `customers.region` ⇒ either pre-aggregate `order_lines → orders` and succeed, or error with `E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE`. |
| **D-NG-4** | `natural_grain` does NOT alter implicit home-grain aggregation (D-003), `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` (D-024), or the multi-fact stitch rule (§6.8.2). | this proposal §3.5, §3.6, §3.7 | Re-run the existing T-004 (lifetime_value), T-023 (finer-grain ref), and T-011 (multi-fact stitch) under `natural_grain: orders` ⇒ identical row-sets / error codes. |
| **D-NG-5** | For a scalar query (Foundation §5.1.2), `natural_grain` filters the result row set: only rows reachable from the natural-grain dataset appear, even when the home dataset of the scalar query is unrelated. | this proposal §3.4 | Scalar query `Fields: [customers.id]` against the F-NG-FACT-ROOTED fixture ⇒ orphan customer omitted under `natural_grain: orders`. |

A corresponding test group (`T-NG-1` … `T-NG-4`) would be added to [`DATA_TESTS.md`](DATA_TESTS.md), with new fixture `F-NG-FACT-ROOTED` carrying the orphan-customer data.

---

## 7. Open questions

These are the points the spec-review needs to resolve before this proposal can be ratified.

### 7.1 Where `natural_grain` is declared

This proposal says model-top-level. Alternatives:

- Per-explore / per-view declaration (Looker style). Allows different "natural grains" for different analytical surfaces over the same datasets. More flexible, more confusing.
- Per-query override. Same flexibility but pushed onto the query author. Defeats most of the point of the feature.

**Recommendation:** model-top-level only for v1. Per-surface is a future extension.

### 7.2 Multi-fact `natural_grain`

Models with two facts of equal weight (e.g. `orders` and `returns`) might want both to be "natural." The strict-single-fact rule forces a choice, which is sometimes wrong.

**Alternatives considered:**

- Allow `natural_grain: [orders, returns]` as a list. Semantics: every query implicitly includes BOTH, joined via stitch on shared dimensions. Concretely this means a dimension-only query returns the dimensions reachable from *either* fact.
- Allow per-fact "naturalness" annotations on relationships rather than a top-level key.

**Recommendation:** out of scope for v1. The single-fact rule is enough to validate the feature's value. Multi-fact is a follow-up if v1 ships and users want it.

### 7.3 Naming

`natural_grain` is the working name. Alternatives:

- `root_dataset` — clearer about "this is the fact every query is rooted on" but less aligned with existing OSI vocabulary.
- `always_include` — describes the effect but not the intent.
- `anchor` — short, vendor-neutral, but vague.

**Recommendation:** decide before ratification; rename in this proposal at that time.

---

## 8. Interaction with the deferred-key contract

The Foundation's top-level schema is defined by [`OSI_core_file_format.md`](OSI_core_file_format.md); `natural_grain` is not in that schema. A Foundation-conformant engine reading a model that declares `natural_grain:` is therefore reading something that is not OSI core. Engines MAY reject the unknown key as malformed input, MAY ignore it with a diagnostic warning, or MAY accept it under a clearly-named extension flag (per Foundation §11 MAY-list).

When/if this proposal is adopted, the implementation's set of recognised top-level keys grows by exactly one (`natural_grain`). No amendment to Foundation Appendix B D-009 is required — D-009 governs only the *relationship-level* keys (`referential_integrity`, `condition`, `asof`, `range`) that the Foundation explicitly defers. The top-level key list is owned by `OSI_core_file_format.md`, which this proposal would update at adoption time.
