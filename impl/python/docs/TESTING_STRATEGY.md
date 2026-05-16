# TESTING_STRATEGY.md — The Four-Layer Test Pyramid

Every Foundation feature in `osi_python` must be covered by tests at
**four** layers. Any sprint that ships a feature without all four layers
is incomplete regardless of feature completeness.

The four layers:

| Layer | Purpose | Typical tools | Runtime budget |
|:---|:---|:---|:---:|
| **Unit** | Small, targeted tests that pin a specific behavior of a single function or class. | `pytest`, plain assertions. | < 1 ms / test |
| **Property** | Hypothesis-driven tests that assert universally-quantified invariants of the algebra and planner. | `hypothesis`. | 10 ms – 1 s / test |
| **Golden** | Snapshot tests that fix the exact `QueryPlan` and the exact SQL for a curated set of input queries. | `syrupy` (pytest plugin) or in-repo golden files. | < 50 ms / test |
| **E2E** | DuckDB-executed tests that assert the SQL we render returns the correct rows on real data. | `pytest` + `duckdb`. | 100 ms – 5 s / test |

Plus a cross-cutting quality gate:

| Tooling | Purpose |
|:---|:---|
| **Mutation testing** | Proves that the four layers above actually catch bugs, not just execute lines. |

---

## Table of Contents

1. [Why Four Layers](#1-why-four-layers)
2. [Layer 1: Unit](#2-layer-1-unit)
3. [Layer 2: Property-Based](#3-layer-2-property-based)
4. [Layer 3: Golden](#4-layer-3-golden)
5. [Layer 4: End-to-End](#5-layer-4-end-to-end)
6. [Mutation Testing](#6-mutation-testing)
7. [Test Directory Layout](#7-test-directory-layout)
8. [Coverage Targets](#8-coverage-targets)
9. [What NOT to Test](#9-what-not-to-test)

---

## 1. Why Four Layers

Each layer catches a class of bug the others cannot:

- **Unit** catches logic errors in a single function.
- **Property** catches violations of universal invariants — the "for all
  inputs, X holds" properties that unit tests cannot exhaustively check.
  This is where the algebra's correctness lives.
- **Golden** catches unintended changes in plan structure or SQL output
  that would otherwise be silent. A CI diff on a golden file is often
  the first sign of a regression.
- **E2E** catches the case where the SQL compiles and looks right but
  executes to wrong rows on real data (dialect quirks, operator precedence
  surprises, NULL handling).

A bug that slips through a layer gets caught by the next. A bug that
slips through all four means the test design missed something — file a
test-debt item in `INFRA.md §3`.

---

## 2. Layer 1: Unit

**Location:** `tests/unit/<package>/test_<module>.py`

**What to test.**
- Every public function's "happy path" with realistic inputs.
- Every documented precondition: write a `pytest.raises(OSIError, match="E4001")` test.
- Boundary conditions the function's docstring calls out.

**What NOT to test here.**
- Broad invariants that hold across many functions (those go to Property).
- End-to-end plans (those go to Golden or E2E).

**Example.**

```python
# tests/unit/planning/algebra/test_aggregate.py
def test_aggregate_same_grain_identity_is_noop() -> None:
    state = build_state(grain={"customer_id"}, columns=[amount_fact])
    result = aggregate(state, {"customer_id"}, [Identity("amount")])
    assert result == state

def test_aggregate_target_not_subset_raises_E3004() -> None:
    state = build_state(grain={"customer_id"}, columns=[amount_fact])
    with pytest.raises(OSIError) as exc:
        aggregate(state, {"region"}, [Sum("amount")])
    assert exc.value.code == ErrorCode.E3004_GRAIN_NOT_SUBSET
```

Unit tests are the fastest to write and should have the lowest friction
— write them first for every new line of compiler logic.

---

## 3. Layer 2: Property-Based

**Location:** `tests/properties/`

**What to test.**
- Every law in [`ALGEBRA_LAWS.md §2`](ALGEBRA_LAWS.md#2-the-laws-and-their-tests).
- Invariants that hold across *any* legal state or *any* legal query:
  idempotence, commutativity, purity, determinism.
- Error taxonomy: every exception raised anywhere in the compiler is an
  `OSIError` subclass with a valid `code`.

**What NOT to test here.**
- Specific-case behaviors (those go to Unit).
- Dialect-specific SQL (generate SQL with `dialect=ANSI` for properties).

**Hypothesis configuration.** See [`ALGEBRA_LAWS.md §1`](ALGEBRA_LAWS.md#1-generation-strategies)
for strategies. Example:

```python
# tests/properties/test_grain_closure.py
@given(chain=operator_chains(min_size=1, max_size=12))
@settings(max_examples=500, deadline=1000)
def test_grain_closure(chain: OperatorChain) -> None:
    symbolic = simulate_grain(chain)
    actual = execute_chain(chain).grain
    assert symbolic == actual
```

Property tests must run in < 60 s each in CI. If a property takes longer,
either narrow the generation strategy or move the expensive check to a
nightly mutation run.

---

## 4. Layer 3: Golden

**Location:** `tests/golden/`

**What to test.**
- Exact `QueryPlan` structure for a curated set of canonical queries.
- Exact rendered SQL for each `(plan, dialect)` pair.

**Format.** Each golden test has:

```
tests/golden/
  basic/
    single_table_revenue/
      model.yaml          # the semantic model
      query.yaml          # the semantic query
      expected.plan.json  # snapshot of QueryPlan (pretty-printed)
      expected.ansi.sql   # rendered SQL for ANSI dialect
      expected.duckdb.sql # rendered SQL for DuckDB
      expected.snowflake.sql
```

The test driver loads the model, builds the plan, compares to
`expected.plan.json`, renders SQL for each dialect, compares to the
per-dialect `.sql` file. A mismatch raises a detailed diff and prints
the command to refresh the golden: `make golden-refresh TEST=<name>`.

**Why golden tests matter.** They turn "what does the planner do for
this query?" from a paragraph of prose into a file-on-disk. Diffs are
easy to read in PR review; unintended changes become visible immediately.

**Refresh policy.** Refreshing a golden file is a deliberate action, not
a shortcut for making tests pass. A PR that refreshes goldens must
explain in the PR description which behavior change justifies the update
and why it's intentional.

### 4.1 Canonical Golden Corpus

A small set of queries covering the Foundation's joint distribution:

1. Single table, `SUM` aggregation, `GROUP BY` one dimension.
2. N:1 enrichment, dimension from join target.
3. N:1 enrichment with declared referential integrity (INNER vs LEFT diff).
4. Chasm trap: two facts joined through shared dimension.
5. Multi-grain arithmetic (derived metric via `metric_a / metric_b`).
6. `EXISTS_IN` semi-join.
7. `NOT EXISTS_IN` anti-semi-join (NULL-safe).
8. Query with `Where` + `Having` + `Order By` + `Limit`.
9. Composite-key relationship.
10. Ambiguous path disambiguation via `using_relationships`.

Every new SPEC-defined behavior earns a golden.

---

## 5. Layer 4: End-to-End

**Location:** `tests/e2e/`

**What to test.**
- The SQL we emit executes on DuckDB and returns the expected rows.
- Multi-dialect equivalence: where a query is supported on multiple
  dialects, the observable rows are the same.

**Harness.** `tests/e2e/conftest.py` provides an in-memory DuckDB with
fixture tables loaded from `tests/e2e/fixtures/`. Each test:

1. Loads a model from `examples/models/`.
2. Builds and renders a plan.
3. Executes the SQL against DuckDB.
4. Asserts on the row set (not on the SQL).

```python
# tests/e2e/test_chasm_trap.py
def test_chasm_resolves_via_merge(duckdb_conn) -> None:
    rows = run_semantic_query(
        duckdb_conn,
        model="sales_returns.yaml",
        query={"dimensions": ["customers.segment"],
               "measures": ["orders.total_revenue", "returns.total_returns"]},
    )
    assert sorted(rows) == [("Ent", Decimal("700"), Decimal("50")),
                            ("SMB", Decimal("275"), Decimal("75"))]
```

**E2E tests are not SQL-shape tests.** If you find yourself asserting
"the SQL has three CTEs", that's a golden test, not E2E. E2E asserts on
rows.

### 5.1 TPC-DS Spot Checks

A small slice of TPC-DS queries rewritten as semantic queries lives in
`tests/e2e/tpcds/`. They run against DuckDB's bundled SF1 data and
assert row-count parity with a hand-rolled SQL reference.

---

## 6. Mutation Testing

**Why.** Coverage proves lines are executed. Mutation testing proves
those lines are *checked*. A line of `<=` mutated to `<` that survives
every test means the test corpus is not actually checking that line —
only touching it.

**Tool.** `mutmut` by default; `cosmic-ray` as an alternative for
targeted algebra runs. Either is acceptable as long as per-module
thresholds in [`ALGEBRA_LAWS.md §4.1`](ALGEBRA_LAWS.md#41-per-module-thresholds)
are met.

**Run frequency.**

| When | What runs |
|:---|:---|
| Every PR | Fast-path mutation on `src/osi/planning/algebra/` (~5 min). |
| Nightly | Full project mutation (~30 min), publishes score history. |
| Before release | Full project mutation; release blocked if score regresses > 2%. |

**Key rule.** A surviving mutation in `src/osi/planning/algebra/` is
treated as a P0 — that module IS the correctness boundary, and surviving
mutations mean the boundary is leaky. Write tests to kill it before
shipping.

---

## 7. Test Directory Layout

```
tests/
  conftest.py                    # common fixtures (duckdb conn, sample models)
  unit/
    parsing/
    planning/
      algebra/                   # unit tests per algebra op
      test_classify.py
      test_joins.py
      test_planner.py
    codegen/
    diagnostics/
  properties/
    strategies.py                # Hypothesis strategies (see ALGEBRA_LAWS.md)
    reference.py                 # reference interpreter for equivalence laws
    test_algebra_totality.py
    test_algebra_purity.py
    test_algebra_determinism.py
    test_grain_closure.py
    test_aggregate_idempotent.py
    test_filter_commute.py
    test_merge_associative.py
    test_project_idempotent.py
    test_enrich_preserves_rows.py
    test_explosion_safety.py
    test_chasm_safety.py
    test_mn_rejection.py
    test_sql_determinism.py
    test_error_taxonomy.py
  golden/
    basic/
    joins/
    composition/
    filters/
    _driver.py                   # shared golden-test harness
    refresh.py                   # `make golden-refresh` entry point
  e2e/
    conftest.py                  # DuckDB in-memory fixture
    fixtures/
      sales_returns.sql
      tpcds_sf1/
    test_single_table.py
    test_enrichment.py
    test_chasm_trap.py
    test_exists_in.py
    tpcds/
      test_q01.py
      test_q35.py
```

---

## 8. Coverage Targets

These are floors, not goals. See `INFRA.md §1.1` for the authoritative
table.

| Surface | Line coverage | Branch coverage | Mutation score |
|:---|:---:|:---:|:---:|
| `src/osi/planning/algebra/` | ≥ 98% | ≥ 95% | ≥ 90% |
| `src/osi/planning/` (total) | ≥ 95% | ≥ 90% | ≥ 85% |
| `src/osi/parsing/` | ≥ 90% | ≥ 85% | ≥ 80% |
| `src/osi/codegen/` | ≥ 90% | ≥ 85% | ≥ 75% |
| `src/osi/diagnostics/` | ≥ 85% | ≥ 80% | ≥ 70% |
| Project overall | ≥ 92% | ≥ 88% | ≥ 75% |

Coverage that hits line-coverage but misses branch-coverage is a red flag
— most untested branches are error paths, and the error paths are where
the `E4xxx` codes live.

---

## 9. What NOT to Test

A short list of anti-patterns that waste cycles and produce brittle
tests. Reviewers will push back on PRs that add these:

1. **Don't test private helpers.** If `_compute_foo()` isn't exported,
   don't import it from a test. Cover it via its public entry point.
2. **Don't test sqlglot or pytest.** External libraries are their own
   project's job.
3. **Don't re-test the algebra laws in E2E.** The laws are property
   tests; re-asserting them against DuckDB is slow and redundant.
4. **Don't use `pytest.approx` on identifiers or SQL strings.** Those
   are exact comparisons — use `syrupy` or plain `==`.
5. **Don't assert on error-message text.** Assert on `error.code`. Message
   text is user-facing prose and should be free to evolve.
6. **Don't share mutable fixtures between tests.** Every test gets its
   own DuckDB connection; every fixture is frozen.
7. **Don't skip flaky tests.** Fix or delete — never `@skip`. The only
   acceptable skip is `@pytest.mark.skipif(sys.platform == 'win32')`
   when there is a real platform-specific limitation.
