# ALGEBRA_LAWS.md — Machine-Checked Correctness

Companion to [`../specs/JOIN_ALGEBRA.md`](../specs/JOIN_ALGEBRA.md). That
document states the laws informally; this document states them as
Python-executable property tests, specifies the Hypothesis strategies used
to generate test data, and lists the mutation-testing budget that guards
each law against silent regressions.

> **Why machine-check the algebra?** The algebra is the one place in the
> compiler where a silent bug is invisible: an incorrect operator could
> return valid-looking states that produce plausible-but-wrong SQL. Every
> other layer depends on the algebra being correct; no downstream test
> can catch a violation of, say, the grain-closure law. The only reliable
> defense is to generate lots of inputs and check laws directly.

---

## Table of Contents

1. [Generation Strategies](#1-generation-strategies)
2. [The Laws (and their tests)](#2-the-laws-and-their-tests)
3. [Reference Interpreter](#3-reference-interpreter)
4. [Mutation-Testing Budget](#4-mutation-testing-budget)
5. [When a Property Test Fails](#5-when-a-property-test-fails)
6. [Adding a New Law](#6-adding-a-new-law)

---

## 1. Generation Strategies

All strategies live in `tests/properties/strategies.py` and are deliberately
minimal — enough to exercise the algebra without drifting into scenarios
the Foundation does not support.

### 1.1 `identifiers()`

Generates normalized identifiers:

```python
identifiers = st.from_regex(r"^[a-z][a-z0-9_]{0,15}$", fullmatch=True)
```

Never generates reserved words or quoted identifiers (those are out of
scope for the algebra tests; they live in parser tests).

### 1.2 `schemas()`

Generates a small, valid `SemanticModel` fixture: 1–4 datasets, 0–6
relationships drawn from valid equijoin pairings, every dataset has a
declared `primary_key`. Specifically excludes anything in `specs/deferred/`.

### 1.3 `states()`

Generates `CalculationState` values **by construction through the algebra**
— never by direct instantiation. A generated state is a sequence of
operator applications starting from `source(...)`, which guarantees every
generated state satisfies the invariants `I-1` through `I-8`.

### 1.4 `operator_chains(state)`

Given a starting state, generates a valid sequence of operator applications
(each operator's precondition is satisfied by construction). The chain
length is parameterized, with `max_examples=200` as the test default.

### 1.5 `duckdb_fixtures()`

Generates small DuckDB in-memory tables matching a generated schema. Used
by the end-to-end laws (§2.9, §2.10) that need to compare OSI-generated
SQL to a reference result.

---

## 2. The Laws (and their tests)

Each law has:

- **Statement** — the informal law from `specs/JOIN_ALGEBRA.md §4`
- **Property** — the Python-level assertion
- **Test file** — location under `tests/properties/`
- **Mutation target** — module in `src/osi/` where a mutation would break this law

### 2.1 Totality (`JOIN_ALGEBRA.md §4.1`)

**Statement.** Every operator either returns a new valid state or raises
`AlgebraError`. No `None`, no partial returns.

**Property.**

```python
@given(op=operator_arguments())
def test_totality(op: OperatorArgs) -> None:
    try:
        new_state = apply(op)
    except AlgebraError as e:
        assert e.code.startswith("E4"), "algebra errors must be E4xxx"
        return
    assert isinstance(new_state, CalculationState)
    for invariant in ALL_INVARIANTS:
        invariant.check(new_state)
```

**Test file.** `tests/properties/test_algebra_totality.py`
**Mutation target.** `src/osi/planning/algebra/operations.py`

### 2.2 Purity (`§4.2`)

**Property.** For any operator and any input, `apply(op)` called twice
returns equal results and leaves inputs unchanged.

```python
@given(op=operator_arguments())
def test_purity(op: OperatorArgs) -> None:
    snapshot_before = deep_copy(op.inputs)
    result_1 = apply(op)
    result_2 = apply(op)
    assert result_1 == result_2
    assert op.inputs == snapshot_before
    assert_frozen(result_1)
```

**Test file.** `tests/properties/test_algebra_purity.py`
**Mutation target.** `src/osi/planning/algebra/` (whole package)

### 2.3 Determinism (`§4.3`)

**Property.** Rendering a plan to SQL is byte-identical across runs.

```python
@given(query=semantic_queries(), model=schemas())
def test_sql_byte_identical(query, model) -> None:
    plan_a = plan(query, build_context(model))
    plan_b = plan(query, build_context(model))
    assert plan_a == plan_b
    sql_a = render(plan_a, dialect="duckdb")
    sql_b = render(plan_b, dialect="duckdb")
    assert sql_a == sql_b
```

**Test file.** `tests/properties/test_sql_determinism.py`
**Mutation targets.** `src/osi/planning/prefixes.py`, `src/osi/codegen/transpiler.py`

### 2.4 Grain Closure (`§4.4`)

**Property.** The final grain of an operator chain can be computed
symbolically from the operator-argument sequence alone.

```python
@given(chain=operator_chains(min_size=1, max_size=12))
def test_grain_closure(chain) -> None:
    symbolic_grain = simulate_grain(chain)   # pure function over op args
    actual_grain = execute_chain(chain).grain
    assert symbolic_grain == actual_grain
```

**Test file.** `tests/properties/test_grain_closure.py`
**Mutation target.** `src/osi/planning/algebra/grain.py`

### 2.5 Aggregate Idempotence (`§4.5`)

**Property.** Re-aggregating at the same grain with identity aggregations
is a no-op.

```python
@given(state=states_with_aggregates())
def test_reaggregate_same_grain_identity(state) -> None:
    identity_aggs = identity_reaggregations(state)
    out = aggregate(state, state.grain, identity_aggs)
    assert equivalent(out, state)
```

**Test file.** `tests/properties/test_aggregate_idempotent.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::aggregate`

### 2.6 Filter Commutativity (`§4.6`)

**Property.** `filter(filter(s, p1), p2)` is equivalent to
`filter(filter(s, p2), p1)`, and both are equivalent to
`filter(s, And(p1, p2))` for non-overlapping predicates.

**Test file.** `tests/properties/test_filter_commute.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::filter`

### 2.7 Merge Associativity (`§4.7`)

**Property.** `merge(merge(a, b), c) ≡ merge(a, merge(b, c))` at equal
grains with disjoint non-grain columns.

**Test file.** `tests/properties/test_merge_associative.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::merge`

### 2.8 Projection Idempotence (`§4.8`)

**Property.** `project(project(s, c1), c2) ≡ project(s, c2)` when `c2 ⊆ c1`.

**Test file.** `tests/properties/test_project_idempotent.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::project`

### 2.9 Enrichment Preserves Parent Rows (`§4.9`)

**Property.** DuckDB-executed. After `enrich(parent, child, keys, LEFT)`,
the projection onto `parent.grain` has the same row multiset as the parent.

```python
@given(fixture=duckdb_fixtures_with_n1_join())
def test_enrich_preserves_rows(fixture) -> None:
    parent_rows = duckdb.execute(fixture.parent_sql).fetchall()
    enriched_sql = render(enrich_plan(fixture))
    enriched_rows = duckdb.execute(enriched_sql).fetchall()
    assert count_by(parent_rows, fixture.parent_grain) == count_by(
        enriched_rows, fixture.parent_grain
    )
```

**Test file.** `tests/properties/test_enrich_preserves_rows.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::enrich`

### 2.10 Explosion Safety (`§4.10`)

**Property.** For any generated schema with 1:N joins and an aggregation
on the many-side, the OSI result matches a hand-rolled pre-aggregate
reference.

```python
@given(fixture=duckdb_fixtures_with_1n_topology())
def test_no_fan_out(fixture) -> None:
    osi_result = run_osi(fixture)
    reference_result = run_pre_aggregate_reference(fixture)
    assert rows_equal(osi_result, reference_result)
```

**Test file.** `tests/properties/test_explosion_safety.py`
**Mutation target.** `src/osi/planning/classify.py`, `src/osi/planning/joins.py`, `src/osi/planning/algebra/operations.py::aggregate`

### 2.11 Chasm-Trap Safety

**Property.** For any generated schema with two facts sharing a dimension,
the OSI result equals two independent aggregations merged on the shared
dimension.

**Test file.** `tests/properties/test_chasm_safety.py`
**Mutation targets.** `src/osi/planning/planner.py`, `src/osi/planning/algebra/operations.py::merge`

### 2.12 M:N Rejection

**Property.** For any relationship declared or inferred as N:N, using it
as input to `enrich` raises `E3011`; using it with `filtering_join` succeeds.

**Test file.** `tests/properties/test_mn_rejection.py`
**Mutation target.** `src/osi/planning/algebra/operations.py::enrich`

### 2.13 Error Taxonomy

**Property.** Every raised exception is an `OSIError` subclass with a
non-empty `code` field matching a known `ErrorCode` enum value.

```python
@given(model=possibly_invalid_schemas(), query=possibly_invalid_queries())
def test_error_taxonomy(model, query) -> None:
    try:
        plan(query, build_context(model))
    except OSIError as e:
        assert e.code in ErrorCode, f"unknown error code {e.code}"
    except Exception as e:
        pytest.fail(f"non-OSIError raised: {type(e).__name__}: {e}")
```

**Test file.** `tests/properties/test_error_taxonomy.py`
**Mutation target.** `src/osi/errors.py`

---

## 3. Reference Interpreter

A few of the laws (§2.9, §2.10, §2.11) compare OSI-generated SQL to a
**reference interpreter**: a deliberately naive Python/pandas
implementation of the Foundation semantics.

The reference interpreter lives in `tests/properties/reference.py` and
is not used outside of tests. It:

- Reads DuckDB tables directly into `pandas.DataFrame`s
- Implements each algebra operator as a pure pandas transformation
- Is optimized for clarity, not speed (it may be 1000× slower than the
  compiled SQL — that's fine for tests)

Tests assert that `osi_python → SQL → DuckDB → rows` equals
`osi_python → plan → reference_interpreter → rows`. Divergence is a bug
in the compiler, because the reference interpreter IS the semantic truth
for those laws.

---

## 4. Mutation-Testing Budget

Mutation testing (`mutmut` or `cosmic-ray`, see `INFRA.md §2`) injects
small mutations into the source (changing `<=` to `<`, swapping
`True`/`False`, removing `not`, etc.) and checks whether any test catches
the change. A mutation that survives every test is a test-coverage hole.

### 4.1 Per-Module Thresholds

| Module | Mutation score target | Rationale |
|:---|:---:|:---|
| `src/osi/planning/algebra/` | **≥ 90%** | The algebra is the hard boundary. Silent bugs here are invisible to every other test. |
| `src/osi/planning/classify.py` | ≥ 85% | Filter classification decides fan-out-vs-semi-join; wrong decisions produce wrong SQL. |
| `src/osi/planning/joins.py` | ≥ 85% | Path resolution and cardinality inference. |
| `src/osi/codegen/` | ≥ 75% | Dialect-specific; harder to get high scores because of idiom branches. |
| `src/osi/parsing/` | ≥ 80% | Validation errors matter but are syntactic. |
| Project overall | ≥ 75% | Absolute floor. |

### 4.2 How Thresholds Are Enforced

- Baseline captured at each release; CI asserts no per-module score drops
  more than **2%** between runs.
- A sprint that lowers the baseline without an explicit "mutation debt"
  note in `INFRA.md §3` is a failure.

### 4.3 Running Locally

```bash
make mutation       # full project, slow (~30 min)
make mutation-fast  # algebra module only, typical dev loop (~5 min)
```

---

## 5. When a Property Test Fails

Property tests shrink failing cases to a minimal counterexample. The
standard debugging procedure:

1. Re-run with `--hypothesis-seed=<seed>` (printed at failure) to confirm
   reproducibility.
2. Copy the minimal counterexample into a new regression test under
   `tests/unit/` — property tests shrink, but the exact shrunk case may
   change between runs.
3. Fix the bug. Keep the regression test.
4. Re-run the original property test; it should now pass on the seed.

Never mark a property test as `@skip` to go green. If the property is
incorrect as stated, fix `specs/JOIN_ALGEBRA.md` first, then the test.

---

## 6. Adding a New Law

1. State the law in `specs/JOIN_ALGEBRA.md §4`.
2. Reference it from this doc (`docs/ALGEBRA_LAWS.md §2`) with:
   - Property statement
   - Test file path
   - Mutation target module
3. Add the property test under `tests/properties/`.
4. Ensure the mutation score for the target module still meets the
   threshold in §4.1 — if not, add more tests or unit cases.
5. Cite the new law in the PR description.

A law that does not have all three pieces (spec, property test, mutation
coverage) is not load-bearing and does not belong in this document.
