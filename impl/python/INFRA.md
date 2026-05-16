# INFRA.md — Infrastructure Standards & Roadmap

Source of truth for infrastructure standards, quality targets, toolchain
decisions, and the infrastructure sprint roadmap for `osi_python`.
Maintained in lockstep with the code.

**Relationship to `SPEC.md`.** `SPEC.md` defines *what the product does*
(the OSI Foundation). `INFRA.md` defines *how we ensure the product is
built well* (tests, lint, CI, quality gates, tool choices).

**Relationship to `specs/JOIN_ALGEBRA.md`.** The algebra is the hard
boundary for correctness. This document enforces the quality gates that
keep that boundary intact.

---

## Table of Contents

1. [Quality standards](#1-quality-standards)
2. [Toolchain](#2-toolchain)
3. [Infrastructure roadmap](#3-infrastructure-roadmap)
4. [Decisions log](#4-decisions-log)

---

## §1 Quality Standards

Minimum quality bars enforced on every change. Regression below these
thresholds is a failure regardless of feature completeness.

### §1.1 Test quality

| Metric | Target | Minimum gate | Scope | Tool | Notes |
|:---|:---:|:---:|:---|:---|:---|
| Unit tests pass | all | all | whole project | `pytest tests/unit/` | Hard gate. |
| Property tests pass | all | all | whole project | `pytest tests/properties/` | Hard gate. `max_examples=500` default. |
| Golden tests pass | all | all | whole project | `pytest tests/golden/` | Regen requires PR justification. |
| E2E tests pass | all | all | whole project | `pytest tests/e2e/` | DuckDB execution. |
| Line coverage | ≥ 95% | ≥ 92% | `src/osi/planning/` | `pytest-cov` | Hard gate. |
| Line coverage | ≥ 92% | ≥ 90% | `src/osi/` overall | `pytest-cov` | Hard gate. |
| Branch coverage | ≥ 90% | ≥ 88% | `src/osi/` overall | `pytest-cov` | Hard gate. |
| **Mutation score — algebra** | **≥ 90%** | **≥ 88%** | `src/osi/planning/algebra/` | `mutmut` | **Load-bearing.** See §1.1.1. |
| Mutation score — classify/joins | ≥ 85% | ≥ 82% | `src/osi/planning/{classify,joins}.py` | `mutmut` | Fan-out / chasm-trap path. |
| Mutation score — codegen | ≥ 75% | ≥ 72% | `src/osi/codegen/` | `mutmut` | Dialect idioms. |
| Mutation score — project | ≥ 75% | ≥ 72% | `src/osi/` overall | `mutmut` | Floor. |
| Max mutation drop per sprint | 0 pp | 2 pp | per module | CI | Drop > 2% fails CI. |
| Max `make test` runtime | ≤ 2 min | ≤ 5 min | whole project | CI | Property + golden + E2E. |

#### §1.1.1 Why the algebra threshold is highest

`src/osi/planning/algebra/` is the correctness boundary. A silent bug in
that module produces wrong SQL that no downstream test can reliably
catch, because every downstream test is built on top of the algebra. A
surviving mutation in the algebra module is treated as a P0 and blocks
merge until killed.

#### §1.1.2 Ratchet policy

- Baseline for each score captured at each release.
- Sprint-level regressions > 2 percentage points on any listed score
  fail CI.
- Every four sprints, reviewers consider raising the baseline by 1–2 pp
  per module.

### §1.2 Code quality

| Standard | Requirement | Tool | Notes |
|:---|:---:|:---|:---|
| Type errors | 0 | `mypy` (strict) | `disallow_untyped_defs = True`; `disallow_any_generics = True`; `warn_return_any = True`. Hard gate. |
| Tests type errors | warnings OK | `mypy` | `disallow_untyped_defs = False` under `tests/`. |
| Lint errors | 0 | `flake8` | Configured via `.flake8`. |
| Formatting | compliant | `black` (line 88), `isort` (black profile) | Auto-fixed by `make format`. |
| Import direction | `parsing` ← `planning` ← `codegen`; `common` imported by all | `import-linter` | Hard gate. |
| Ban raw-string SQL | enforced | `flake8` custom rule + `rg` check | Scans `src/` for `f".*SELECT\b"` and similar. |
| File size in `src/osi/` | ≤ 600 LOC | `rg -l` audit in CI | Exceptions call for a PR justification note. |
| Docstring on public class/function | required | `flake8-docstrings` | Only public API (`__init__.py`-exported). |
| Pre-commit hook green | required | `pre-commit` (project-local) | Installed via `make install-dev`. |

### §1.3 SQL correctness

Load-bearing invariants; override every other consideration in
`src/osi/planning/` and `src/osi/codegen/`.

- **No f-string SQL, ever.** All SQL manipulation goes through SQLGlot
  AST. Banned tokens are caught by a custom flake8 check and a CI grep.
- **Grain is explicit on every `CalculationState`.** Algebra ops
  enforce grain-safety before returning. See `specs/JOIN_ALGEBRA.md §4.4`.
- **Identifier normalization** goes through `osi.common.identifiers.normalize_identifier`.
  Raw `==` on identifier strings is a bug and is flagged by lint.
- **Unsupported semantics raise `OSIError`.** Never silently emit wrong
  SQL. `tests/properties/test_error_taxonomy.py` enforces this globally.

### §1.4 Release quality

| Gate | Requirement |
|:---|:---|
| All §1.1 / §1.2 / §1.3 gates | Green. |
| `CHANGELOG.md` | Updated for user-visible changes. |
| `specs/JOIN_ALGEBRA.md` diff | Reviewed by two maintainers if laws or operators change. |
| Mutation score | No per-module regression > 2 pp since previous release. |

---

## §2 Toolchain

Adopted tools. Do not replace without an infrastructure sprint and a new
§4 decisions log entry explaining the migration rationale.

| Tool | Purpose | Version policy | Rationale |
|:---|:---|:---|:---|
| Python | Runtime | ≥ 3.11 | Pattern matching, `StrEnum`, richer typing. |
| `pydantic` v2 | Schema validation | pinned in runtime deps | Fast; `extra="forbid"` for unknown-field detection. |
| `sqlglot` | SQL AST, dialect translation | pinned in runtime deps | **The only SQL string-manipulation library allowed.** |
| `pytest` | Test runner | pinned in `[dev]` | De-facto standard; best plugin ecosystem. |
| `hypothesis` | Property-based testing | pinned in `[dev]` | Load-bearing for algebra laws; generates counterexamples with shrinking. |
| `mutmut` | Mutation testing | pinned in `[dev]` | Primary mutation tool. Simple CLI; integrates with pytest. |
| `syrupy` | Snapshot testing | pinned in `[dev]` | Used for golden tests (plans and SQL). |
| `duckdb` | E2E test harness | pinned in `[dev]` | In-memory execution for row-level correctness. |
| `pytest-cov` | Coverage | pinned in `[dev]` | Line + branch coverage. |
| `pytest-benchmark` | Performance tests | pinned in `[dev]` | Detects planner / transpiler regressions. |
| `import-linter` | Architecture enforcement | pinned in `[dev]` | Enforces §1.2 one-way import flow. |
| `mypy` | Type checker | pinned in `[dev]` | Strict config in `pyproject.toml`. |
| `black` | Code formatter | pinned in `[dev]` | Line length 88. |
| `isort` | Import sorter | pinned in `[dev]` | Black-compatible profile. |
| `flake8` | Lint | pinned in `[dev]` | Configured via `.flake8`. |
| `flake8-docstrings` | Docstring lint | pinned in `[dev]` | Public APIs only. |
| `pre-commit` | Local git hooks | pinned in `[dev]` | Project-local config. |

### §2.1 Why `mutmut` over `cosmic-ray`

`mutmut` has a simpler CLI, a built-in baseline, and reasonable defaults
for Python 3.11+. `cosmic-ray` is more configurable; if we ever need
targeted operator-mutation sets on a specific module we can run it as a
secondary, but `mutmut` is the default and the CI gate.

### §2.2 Why `hypothesis` as a first-class dependency

The algebra is small enough to fully specify and large enough to be
impossible to exhaustively test by example. Property tests with
generation strategies are the only tractable way to check the laws in
`specs/JOIN_ALGEBRA.md §4`.

---

## §3 Infrastructure Roadmap

Every infrastructure sprint must reference an item here (or add a new
item before starting). This is the PM's source of truth for evaluating
non-SPEC sprints.

| ID | Description | Status | User value | Sprint ID |
|:--:|:---|:---:|:---|:---|
| I-1 | Per-project venv + `pyproject.toml` + `Makefile` + `.pre-commit-config.yaml` + `.flake8` + `.github/workflows/osi_python.yml`. | planned | Contributors run local CI == remote CI. | — |
| I-2 | `import-linter` contracts enforcing one-way flow and no deferred-feature imports. | planned | Architecture invariants are machine-checked; new contributors cannot accidentally violate them. | — |
| I-3 | Hypothesis strategies for `identifiers()`, `schemas()`, `states()`, `operator_chains()`. | planned | Foundation for every property test; without this, §1.1 mutation targets are unachievable. | — |
| I-4 | `mutmut` integrated into CI with per-module thresholds. Algebra-module fast-path runs on every PR; full run nightly. | planned | Enforces §1.1.1; a surviving mutation in the algebra becomes a P0 automatically. | — |
| I-5 | Reference pandas interpreter for equivalence-law testing (`tests/properties/reference.py`). | planned | Equivalence laws (§4.9, §4.10 of `specs/JOIN_ALGEBRA.md`) can compare SQL output to semantic ground truth. | — |
| I-6 | Golden test driver + `make golden-refresh` command. | planned | Plan and SQL diffs in PR review are immediate and human-readable. | — |
| I-7 | DuckDB E2E fixture harness (`tests/e2e/conftest.py` + fixtures). | planned | Row-level correctness on real data, not just shape-of-SQL. | — |
| I-8 | TPC-DS subset harness (queries expressible in the Foundation). | planned | Exercises the combined surface on real analytical idioms. | — |
| I-9 | Cursor skills: `add-new-operator-to-algebra`, `add-new-dialect`, `debug-plan-output`. | planned | Contributor velocity; recipes replace tribal knowledge. | — |
| I-10 | Performance benchmark baselines with `pytest-benchmark` and a per-release report. | planned | Prevents silent regressions; performance work gets visibility. | — |
| I-11 | `import-linter` rule: no module in `src/osi/` may import from `specs/deferred/` or mention a deferred-feature symbol. | planned | Keeps the Foundation thin; no speculative plumbing leaks. | — |
| I-12 | `SEMANTIC_VIEW(...)` SQL parser (`specs/SQL_INTERFACE.md`). Wires up `E1201`–`E1213` (all currently `RESERVED`). | planned | Portable, JDBC/ODBC-visible surface so BI tools, editors, and notebooks can author Foundation queries without a new client library. Unlocks interop with Snowflake semantic views. | — |
| I-13 | M:N resolution per `Proposed_OSI_Semantics.md §6.5`: bridge anchor discovery for dim-only queries, multi-fact stitch validation, `E3012_MN_NO_STITCH_PATH` / `E3013_NO_STITCHING_DIMENSION` error reclassification. | completed | Foundation can now plan every M:N shape the spec mandates (single bridge, stitch on shared dim, `EXISTS_IN` filter) and surfaces the spec's actionable errors when no route applies. Per-query M:N failures emit `E3012` / `E3013` (the user-facing per-query codes); `E3011_MN_AGGREGATION_REJECTED` is reserved for engine-level M:N opt-outs (vendor capability, not per-query verdict — see §6.8 *Semantic guarantee*) and is never raised at the user-facing surface by `osi_python`. Eliminates 5 of 14 `xfail` compliance cases. | — |
| I-14 | Per-metric `joins.using_relationships` path disambiguation (`§6.7`). | completed | Authors can disambiguate `E3001_AMBIGUOUS_JOIN_PATH` per metric without restructuring the model — same convention Snowflake Semantic Views uses. | — |
| I-15 | `EXISTS_IN` codegen emits correlated `EXISTS (SELECT 1 ...)` per `§7.4 + §11 #8` (was `IN (SELECT keys)`). | completed | Spec-correct NULL semantics and dialect-portable; previous shape was wrong on both counts and broke on DuckDB tuple-IN. | — |
| I-16 | Algebra honours `unique_keys`. `source` plumbs `dataset.unique_keys` into `CalculationState`; `enrich`'s fan-trap rule accepts join keys that match the PK *or any UK* via `CalculationState.is_unique_on()`. UKs are propagated through every grain-preserving operator and dropped/intersected appropriately by `aggregate`/`merge`. Removed the asymmetry between graph-layer cardinality inference (already UK-aware) and algebra-layer grain reasoning (was PK-only). Also extracted `add_columns` and `broadcast` into `algebra/composition.py` to keep the per-file LOC budget. | completed | A 1:N relationship that was *mismarked* (PK chosen on a different column set) can now be recovered by adding `unique_keys` — exactly as the spec promises in `§4.2`. Acceptance: `tests/e2e/test_cardinality_safety.py::test_recovered_model_matches_canonical_results` flipped from `xfail(strict)` to `passed`. New invariant **I-9** added to `algebra/state.py`. | fa47a74a |
| I-17 | Mid-pipeline bridge resolution (`Proposed_OSI_Semantics.md §6.5.1`, mid-pipeline form). The planner detects unsafe `N : N` enrichment edges, finds a bridge dataset that has safe `N : 1` edges to both sides, pre-aggregates the measure to the bridge's link-key grain, sources the bridge as a fresh root, and re-aggregates at the query grain. New `EnrichDerivedPayload` lets `ENRICH` accept a derived (CTE-backed) child rather than a base table. The algebra's `enrich` now reclassifies AGGREGATE columns coming from a *uniquely-keyed* child as FACT — the existing fan-trap check already guarantees safety in that case, and without the relaxation the bridge plan can't compose. Also lifted §6.5 prose to the BI tag-fan-out semantic (routes are now framed as *implementations*, not the contract) and narrowed the §10 deferred entry to the genuine chained-bridge case (query references both outer endpoints A and B). | completed | Discharges the C-3.5 `xfail` (single-bridge mid-pipeline) and pins the genuine multi-bridge case as C-3.5b `xfail`. The §10 deferred entry now describes only the multi-bridge topology — every well-defined single-bridge query plans, regardless of where the bridge sits in the chain. Without this, models with bridges that aren't directly attached to the fact table couldn't be queried even when the answer is unambiguous. | — |
| I-18 | **S-A**: Spec-doc + roadmap landing. Renames `Proposed_OSI_Semantics_updated.md` → `Proposed_OSI_Semantics.md` and `SQL_EXPRESSION_SUBSET_updated.md` → `SQL_EXPRESSION_SUBSET.md` (deletes the old files), rewrites `SPEC.md` / `INFRA.md` / `AGENTS.md` / `CLAUDE.md` and the `specs/` README + deferred README to point at the renamed authoritative spec. No `src/` changes. Anchors §10, Appendix B, Appendix C of the new Foundation. | planned | Single source of truth for the new Foundation; without this every later sprint argues over which spec is canonical. Mirrors the cleanliness clause in `SPEC.md` header — old spec is removed, not deprecated. | S-A |
| I-19 | **S-B**: New compliance suite scaffold + delete the old one. Lands `compliance/foundation-v0.1/` (README, SPEC, `pyproject.toml`, `conformance.yaml`, `proposals.yaml`, `decisions.yaml`, `adapters/`, `datasets/f_*`, empty `tests/` tree). Reuses harness from `compliance/harness` via path dep. Deletes `impl/python/tests/compliance/` so we have exactly one compliance harness. No `src/` changes. | planned | Foundation conformance lives in one external suite that targets the updated spec only. Eliminates the two-harness drift that bit `osi_impl`. | S-B |
| I-20 | **S-C**: Compliance suite tests v1 (T-001 … T-033). Encodes `DATA_TESTS.md §4` as runnable cases under `compliance/foundation-v0.1/tests/`; one negative test per `E_DEFERRED_KEY_REJECTED` family. | planned | Every D-NNN gets a runnable witness before any implementation sprint moves the planner. | S-C |
| I-21 | **S-D**: Baseline compliance run + gap report. Runs S-C against current `osi_python`; emits `results/baseline_<date>.md`. No fixes. Every red row must be cited by exactly one sprint's exit criterion. | planned | Without a baseline we can't tell which red rows the implementation sprints actually flipped green. | S-D |
| I-22 | **S-E**: Differential / edge-case audit + extra `T-NNN` cases. Cross-references every sprint S-1 … S-17 against the v1 catalog and the cross-implementation drift checklist (NULL ordering, integer/decimal precision, division-by-zero, empty-aggregate, time-zone/date arithmetic, collation/case, large-N determinism, M:N de-dup, nested-aggregate grain inference, window frame defaults, OSI_SQL_2026 function semantics). Read-only on `src/`. | completed | Pins the implementation-boundary edges where two compliant engines could legitimately disagree if the spec is read loosely. Net effect: +8.3pp compliance (75.0% → 83.3%) with zero `src/` modifications. See `compliance/foundation-v0.1/results/additions.md`. | S-E |
| I-23 | **S-1** (tech-debt): Delete deferred plumbing. Strips every reference to `EXISTS_IN`, `referential_integrity`, named filters, `role:`, per-metric `joins.{type, using_relationships}`, `ATTR`, `UNSAFE`, `AGG`, `GRAIN_AGG` from `src/`, parser models, codegen, diagnostics, tests, fixtures, examples, and docs. Mutation pass on every touched module. | completed | Removes the speculative plumbing the new Foundation defers; without it every later sprint either tiptoes around dead code or accidentally re-uses it. | S-1 |
| I-24 | **S-2**: Two query shapes (Aggregation vs Scalar) with `Fields` clause + `E_MIXED_QUERY_SHAPE` / `E_AGGREGATE_IN_SCALAR_QUERY` / `E_FAN_OUT_IN_SCALAR_QUERY`. | completed | Pins D-010 / D-011 / D-023 in the planner; closes the Snowflake errata #1, #2, #3, #6, #8 family for the OSI surface. | S-2 |
| I-25 | **S-3**: Predicate routing by resolved expression shape (D-005, D-012). Adds `E_AGGREGATE_IN_WHERE`, `E_NON_AGGREGATE_IN_HAVING`, `E_MIXED_PREDICATE_LEVEL`. | completed | Without this, queries silently route a query-grain aggregate into `Where` and produce wrong results — the single most common BI mis-modelling mistake. | S-3 |
| I-26 | **S-4**: Implicit home-grain aggregation for cross-grain field bodies (D-003, D-015). Pins one of correlated-subquery / `LATERAL` / pre-agg-CTE as the compilation strategy; ≥ 3 D-015 equivalence golden tests. | completed (partial) | What lets `customers.lifetime_value = SUM(orders.amount)` work without an explicit `grain:` keyword. Negative-rule plumbing landed; positive rewrite carries to `I-40` (`I-S4-impl`). | S-4 |
| I-27 | **S-5**: Single-step + nested cross-grain aggregates (D-020, D-024); raises `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`. | completed (partial) | Closes Snowflake `SD-1` (single-step cross-grain) for the Foundation; aligns with Looker / Tableau / dbt-semantic-layer. Error code shipped; positive planner carries to `I-41` (`I-S5-impl`). | S-5 |
| I-28 | **S-6** (tech-debt): Restore ≥ 90% mutation on `src/osi/planning/algebra/`; refactor any > 600 LOC files introduced by S-2..S-5. | completed | Keeps INFRA §1.1.1 invariant intact across the high-churn middle of the rollout. | S-6 |
| I-29 | **S-7**: Default join shape rewrite (D-001, D-004). Single-measure ⇒ `LEFT` (fact→dim). Multi-measure incompatible-root ⇒ `FULL OUTER` stitch. Scalar grand totals ⇒ `CROSS JOIN` of pre-aggregated 1-row scalars. | completed | The single biggest change in the new Foundation; replaces the old default-INNER-via-RI behaviour with safety-first defaults. | S-7 |
| I-30 | **S-8**: Bridge de-duplication contract (D-026). Bridge plan materialises distinct `(fact, group-key)`. Removes every "tag-fan-out" code path and comment — no compat wording left behind. | completed (partial) | Pins Semantic 2 across M:N traversals; matches Looker symmetric aggregates and Tableau Multi-Fact relationships on observable rows. Cleanliness gate landed; distinct materialisation carries to `I-42` (`I-S8-impl`). | S-8 |
| I-31 | **S-9**: Bridge-dedup acceptance for every aggregate category + chasm/stitch decomposition safety (D-022, D-027). Bare `AVG` / `MEDIAN` / `COUNT(DISTINCT)` over an N:N bridge resolve via the §6.8.1 single-pass bridge-dedup construction. `E_UNSAFE_REAGGREGATION` is reserved for plans that genuinely force decomposition (§6.7 chasm pre-aggregation, §6.8.2 stitch). The "per-home-row-first" interpretation continues to require the deferred nested form. | completed | Aligns the Foundation with Tableau Multi-fact / Power BI bridge-table / Looker symmetric-aggregate behaviour for M:N non-distributive (the cross-vendor majority); narrows `E_UNSAFE_REAGGREGATION` to its actually-correct shape. | S-9 |
| I-32 | **S-10**: Identifier resolution + error taxonomy alignment (D-006, D-018, D-019, Appendix C). Every internal `OSIError` code maps 1:1 to Appendix C. | completed | Without this, the compliance suite asserts on codes that don't exist or don't mean what the suite says. | S-10 |
| I-33 | **S-11** (tech-debt): Refactor `classify.py` / `joins.py` for legibility; mutation pass; `diagnostics.explain` covers every new error. | completed | The two highest-churn modules in S-2..S-10 — without a follow-up tech-debt sprint they will exceed the 600-LOC cap. | S-11 |
| I-34 | **S-12**: Window functions in Foundation (§6.10): D-028 placement rules, D-030 pre-fan-out materialisation, D-031 windowed-metric-composition rejection, D-032 deferred frame modes. | completed (partial) | Closes the Snowflake errata #5, #19, #25 family on the OSI surface; brings the implementation in line with the new Foundation's standard-SQL-windows scope. Negative rules and rejections shipped; positive planner (pre-fan-out CTE, fan-out detection, codegen) carries to `I-43` (`I-S12-impl`). | S-12 |
| I-35 | **S-13**: NULLS LAST default emission for outer `Order By` + `OVER ORDER BY` (D-029); D-014 per-engine determinism preserved. Compiled SQL contains the explicit `NULLS LAST` clause regardless of dialect default. | completed | Without this two compliant engines emit different SQL whose results differ on NULL ordering — a portability hazard. | S-13 |
| I-36 | **S-14**: Empty / NULL aggregate behaviour (D-033). `COUNT*` ⇒ 0, others ⇒ `NULL`; stitch missing-cells follow standard SQL. | completed | Pins the user-visible result on the most common edge case (a group with no rows on one side of a stitch). | S-14 |
| I-37 | **S-15** (tech-debt): Final `mutmut` sweep across planning + codegen; raise / maintain INFRA §1.1 baselines. | completed (partial) | Last quality gate before S-16 / S-17 ship; ensures the rollout exits at or above the pre-rollout mutation baseline. Property tests and file-size cleanliness landed; full mutmut sweep deferred to a post-Foundation tech-debt sprint per S-15 retro. | S-15 |
| I-38 | **S-16**: `OSI_SQL_2026` default dialect (D-021); per-dialect expression form `{ dialects: [...] }` in parser. | completed (partial) | Aligns the implementation's expression surface with the new normative SQL subset. Dialect enum and renderer landed; parser-level `dialect:` model key + function catalog whitelist + `E_UNKNOWN_FUNCTION` enforcement carries to `I-44` (`I-S16-impl`). | S-16 |
| I-39 | **S-17**: Full compliance run; root-cause every remaining failure (impl bug vs test bug vs spec ambiguity); clear `xfail`s. | completed | Foundation v0.1 compliance landed at **75.0%** (48/64) — every remaining red row triaged into a named impl-deferral, test bug (S-E), or dataset gap (S-E). See `compliance/foundation-v0.1/results/final_2026-05-13.md`. | S-17 |
| I-40 | **`I-S4-impl`** (post-Foundation): Implicit home-grain aggregation rewrite (D-003). Carry-over from S-4 — the planner needs to inject the aggregation when a field expression references columns coarser than its home grain. | planned | One of the two largest remaining red-row clusters in the final compliance run; required for `field_metric_grain` and parts of `cross_grain` to go green. | — |
| I-41 | **`I-S5-impl`** (post-Foundation): Nested cross-grain aggregate planner (D-020). Carry-over from S-5 — single-step rewrite for `AVG(SUM(...))` style nestings + inner-grain inference. | planned | Required for `nested_aggregates/*` and `cross_grain/hard/t-005c` to go green. | — |
| I-42 | **`I-S8-impl`** (post-Foundation): Distinct-bridge materialisation (D-026). Carry-over from S-8 — pre-aggregation step that emits `DISTINCT (fact_key, group_key)` before the bridge join. | planned | Required for `bridge/*` to go green and for `joins_default/hard/t-045`. | — |
| I-43 | **`I-S12-impl`** (post-Foundation): Positive window planner — pre-fan-out CTE materialiser, fan-out detection (`E_WINDOW_OVER_FANOUT_REWRITE`), windowed-metric resolution in measures/fields, codegen pass-through for `OVER (...)`. Carry-over from S-12; negative rules already shipped. | planned | Required for the four `windows/{moderate,hard}` tests currently sitting on the deferred-key code path; ~half of the windows area's remaining gap. | — |
| I-44 | **`I-S16-impl`** (post-Foundation): Parser-level `dialect: OSI_SQL_2026` model-level key (default); per-metric / per-field `{ dialects: [...] }` block; OSI_SQL_2026 function-catalog whitelist; `E_UNKNOWN_FUNCTION` enforcement at parse time. | planned | Closes the D-021 contract end-to-end; today the dialect renders as ANSI but the function catalog is not enforced. | — |
| I-45 | **S-18**: Parse-time D-019 reserved-name guard — rejects user identifiers that collide with `GRAIN`, `FILTER`, `QUERY_FILTER`. New module `osi/parsing/reserved_names.py` + cross-reference check in `validation.py`; per-name-class unit tests. | completed | Without this guard a model defining a field `filter` silently shadows the OSI grammar keyword and two compliant implementations diverge. | S-18 |
| I-46 | **S-19** (closes `I-S8-impl`): Bridge de-duplication contract (D-026 / §6.11.3). Adds an inner `aggregate` step at `(left_keys ∪ final_dim_keys)` between the bridge-enrich and the final aggregate; renames `_DISTRIBUTIVE` → `_BRIDGE_RESOLVABLE`; admits `COUNT(DISTINCT)` per D-022. Tests flipped: `t-015`, `t-045`, `t-021` (converted from negative to positive). | completed | Closes the flagship D-026 example (actor↔movie). Without this every M:N model silently double-counts. | S-19 |
| I-47 | **S-20** (closes `I-S4-impl`): Implicit home-grain aggregation (D-003 / D-015). New module `osi/planning/home_grain.py` rewrites field expressions that aggregate a single foreign dataset reachable via one safe N:1 step into a correlated subquery; codegen's `_qualify_columns` learns a subquery-aware mode keyed on the home dataset's logical name. 13 new unit tests including 5 D-015 equivalence assertions. Tests flipped: `t-004`, `t-024`. | completed | Without this, any field referencing a finer-grained dataset crashes with "table not found"; D-015 is the contract that makes the OSI semantic layer behave like SQL with implicit grain hand-back. | S-20 |
| I-48 | **S-21** (closes `I-S5-impl` for the simple shape): Nested cross-grain aggregate planner (D-020 + D-024). New module `osi/planning/planner_nested.py` with `is_nested_aggregate`, `parse_nested`, `infer_intermediate_grain`, `insert_nested_aggregate`. Routed from `_build_measure_group` in `planner.py` via `_maybe_build_nested_aggregate`. Single fact + single safe N:1 edge envelope. 15 new unit tests. Tests flipped: `t-005c`. `t-017` (nested-over-bridge) requires bridge × nested integration; carried to S-23 triage. | completed | Without this, every `AVG(AVG(…))` / `SUM(MAX(…))` metric emits invalid nested-aggregate SQL or silently collapses to a single-step aggregate. D-020 is the contract that makes the per-row-first interpretation explicit. | S-21 |
| I-49 | **S-22** (closes `I-S12-impl`): Positive window planner (D-028 + D-030). Removed `exp.Window` from `_DEFERRED_AST_NODES`; resolver admits `<dataset>.<model_metric>` qualification; scalar planner accepts windowed metrics in `Fields`, materialises them via an `ADD_COLUMNS` step with kind `DIMENSION`, and partitions row-level WHERE into pre-window vs post-window batches (D-030 QUALIFY pattern). 11 new unit tests; 2 legacy "reject window" tests rewritten as positive-contract tests. Tests flipped: `t-027`, `t-031`, `t-032`, `t-036`. | completed | Without a positive window planner the Foundation cannot answer the most common BI question: *"give me the row-level rank / running total / N-th row per group"* — every windowed metric was a parse-time crash. | S-22 |
| I-50 | **S-23** (closes the `I-S5-impl × I-S8-impl` composition): Nested-aggregate-over-bridge planner. New `build_nested_bridge_plan` in `planner_bridge.py` composes the bridge resolver with the nested aggregate planner: source bridge → enrich fact + dim datasets → inner aggregate at `(intermediate_dataset.pk ∪ query_dim_keys)` with the inner fn → outer aggregate at the query grain. Wired through a new `nested_only` precheck in `planner._try_resolve_via_bridge`. Tests flipped: `t-017`. **Drives compliance from 98.5% → 100.0%, no skips.** | completed | Closes the spec's hardest single composition (D-020 × D-022 × D-026). Without this, every M:N model with a per-row-first metric (the natural BI shape for "average per actor of movie revenue") was a parse-time error. | S-23 |
| I-51 | **S-24**: Test review across `tests/`. Tightened 5 broad-catch `pytest.raises(Exception)` to specific exception classes; added 3 new property tests for the positive window planner (round-trip, detector agreement, arithmetic-around-window). Emitted `audit.md` with full per-module coverage matrix and false-positive triage. | completed | Tests that pass when the feature is broken are worse than no test. The sweep found 5 weakly-typed catches that had survived since S-1; tightening them turns "any exception passes" into "the right exception passes". | S-24 |
| I-52 | **S-25 (partial)**: Mutmut 3.x configuration migrated from the obsolete 2.x keys; five fork-safety / pytest / coverage / fixture-copy fixes documented in `pyproject.toml`. Baseline numbers blocked on a macOS-specific fork segfault in mutmut 3.5; resolution path is to run `make mutation` on the existing Linux CI worker and populate `MUTATION_BASELINE.md §1`. | completed (partial) | Without working mutmut config, the load-bearing algebra module would silently lose its mutation-score floor; the §1.1 ratchet keeps every change-set honest about test quality, not just test count. | S-25 |
| I-53 | **S-26**: Maintainability deep review. Shipped `python -m osi explain-code <CODE>` (carry-over from S-11 retro) with name/value lookup, `--list`, `--json`, exhaustiveness test, and 7 new unit tests in `tests/unit/test_cli.py`. Refreshed `ARCHITECTURE.md` §2.3 (parsing exports — `OSI_RESERVED_NAMES`), §3.4 (planning module map covering `planner_scalar.py`, `planner_bridge.py`, `planner_nested.py`, `planner_composites.py`, `planner_mn.py`, `home_grain.py`, `windows.py`, `preprocess.py`, `steps.py`), and §9 canonical entry points (diagnostics CLI + `explain_error`). 600-LOC cap audit performed; carried as I-54 / I-55. | completed | The `explain-code` CLI takes the diagnostics catalogue from a Python-only surface to something CI logs and shell sessions can hit directly — the most user-visible maintainability win of the whole loop. The ARCHITECTURE refresh closes the documentation lag from S-19..S-23. | S-26 |
| I-54 | **Carried from S-26**: Refactor `planner_bridge.py` (currently 656 LOC, over the 600-LOC informal cap). Recommended split into `planning/bridge/{resolve,dedup,nested}.py` corresponding to the three responsibilities that grew during S-19, S-22, and S-23. Pure refactor — no behaviour change, existing compliance and unit suites must pass unchanged. Deferred to post-v0.1 to avoid regression risk on the eve of release. | planned | Restores the 600-LOC cap that the project has held since the start; keeps the bridge resolver readable as new dialects / shapes are added in v0.2+. | — |
| I-55 | **Carried from S-26**: Refactor `planner.py` (currently 605 LOC, over the 600-LOC informal cap). Recommended split into `planner.py` (composer proper — `Planner.plan` + `_build_*` helpers per ARCHITECTURE §3.5) and `planner_dispatch.py` (nested / bridge / composite routing). Pure refactor. Deferred to post-v0.1 alongside I-54. | planned | Same rationale as I-54: protect the 600-LOC cap and keep the composer's "shape" (which the architecture doc points new contributors at) free of routing noise. | — |
| I-56 | **Carried from S-26**: Drop the `(future)` hedge from the `osi.diagnostics.error_catalog` module docstring now that `osi explain-code` ships in v0.1. Trivial, batched into the next docs-touching sprint. | planned | Keeps the catalogue's self-description honest; future readers shouldn't think the CLI surface is still aspirational. | — |
| I-57 | **Spec amendment 2026-05-13**: D-029 `ORDER BY` NULL-placement default flipped from "always `NULLS LAST` regardless of direction" to the **SQL:2003 high-end-NULL convention** — `ASC ⇒ NULLS LAST`, `DESC ⇒ NULLS FIRST`. Restores the symmetry property that flipping `ASC ↔ DESC` flips NULL placement (so a "top-N → bottom-N" UI flip moves the NULL rows as expected). Also collapses Snowflake from a divergence target to "matches the OSI default out-of-the-box", leaving Spark/Databricks as the lone outlier. Touched: `Proposed_OSI_Semantics.md` (§5.1, §6.10.2, §11, Appendix B D-029), `SPEC.md` §1.3 + S-13 sprint row, `specs/SNOWFLAKE_DIVERGENCES.md` SD-2 (rewritten), `src/osi/codegen/transpiler.py` (`nulls_first=o.descending`), gold SQL for `t-027` / `t-032` / `t-036` flipped to `DESC NULLS FIRST`, golden snapshot for `test_sql__order_by_and_limit` regenerated, **new compliance test `t-062-nulls-first-default-on-desc`** locks in the symmetric DESC half (paired with t-026). 100% compliance preserved (67/67). Codegen note: sqlglot's per-dialect elision means the explicit `NULLS …` token is omitted on dialects whose native default already matches the resolved OSI default (e.g. Snowflake `DESC` alone is `NULLS FIRST` natively); D-029's wording was relaxed to allow this since elision and explicit emission produce identical row orders on that dialect. | completed | Fixes a real spec defect found during the v0.1 quality loop: the original "always `NULLS LAST`" rule guaranteed determinism but broke the symmetry property every BI mental model depends on (flip a sort, NULLs should move). The new convention preserves both. Also reduces porting friction against the most-deployed warehouse (Snowflake matches out-of-the-box). | — |

**Status values.** `planned` · `in-progress` · `completed` · `deferred`

---

## §4 Decisions Log

Settled infrastructure decisions. Agents must not relitigate these
without a new log entry and human review.

### [I-DEC-1] Start from the Foundation; defer everything else — 2026-04-25

**Context.** `osi_impl` implemented the full OSI spec feature-by-feature.
The result is a working compiler but a large code surface that mixes
stable features (joins, basic aggregation) with experimental ones
(resettable filters, FIXED/INCLUDE/EXCLUDE). Every new contributor has to
learn which is which.

**Decision.** `osi_python` starts from `specs/Proposed_OSI_Semantics.md`
(the Foundation) and treats every other existing OSI feature as deferred.
Deferred features raise `E1105 RESERVED_FOR_DEFERRED` at parse time; the
codebase contains no plumbing for them.

**Rationale.** A smaller surface is easier to prove correct, faster to
iterate on, and creates clearer extension points when deferred features
are re-introduced.

**Consequences.** Models that work in `osi_impl` may fail to parse in
`osi_python` if they use deferred features. This is intentional and
documented in `specs/deferred/README.md`.

**Alternatives rejected.** Feature-parity with `osi_impl` — rejected
because the combined surface is what we're trying to simplify.

### [I-DEC-2] Mutation testing from day one — 2026-04-25

**Context.** `osi_impl` treats mutation testing as a "planned I-9" item
— the scaffolding never lands because there's always a feature to ship.
The symptoms (silently surviving-mutation code paths) are invisible
until something breaks production.

**Decision.** Mutation testing (`mutmut`) is in from sprint 0. The fast
path runs on `src/osi/planning/algebra/` on every PR; the full run is
nightly. Per-module thresholds are in §1.1 and are ratcheted; sprints
that lower a threshold fail CI.

**Rationale.** The algebra IS the correctness boundary. Adding mutation
testing after the fact means every sprint until then could have
introduced a silent bug. Starting with it builds a culture where
"coverage ≠ correctness" is internalized.

**Consequences.** Sprint 0 spends ~2 days on mutation harness and
baseline. That cost is recovered within 3 sprints by the bugs mutation
testing catches that would otherwise ship.

**Alternatives rejected.** Defer mutation testing to "when the algebra
is stable" — rejected because the algebra is supposed to be stable
*because of* mutation testing, not before it.

### [I-DEC-3] Property-based testing as primary correctness tool — 2026-04-25

**Context.** The algebra has twelve universal laws (`specs/JOIN_ALGEBRA.md §4`).
Each is a statement of the form "for all legal states and all legal op
arguments, X holds." Example-based unit tests can check hundreds of
cases; property tests with Hypothesis can check thousands with
strategically-generated counterexamples.

**Decision.** Every law in `specs/JOIN_ALGEBRA.md §4` has a corresponding
Hypothesis property test under `tests/properties/`. Property tests are
equal-priority with unit tests (not optional). Failure of a property
test blocks merge.

**Rationale.** Laws are the easiest form of correctness to state
formally; Hypothesis is the easiest way to check them at scale in
Python; the combination is as close as Python gets to a machine-checked
proof. The generation strategies become a piece of documentation in
their own right.

**Consequences.** Contributors must learn Hypothesis strategy composition.
The payback is a test suite that catches regressions example-based tests
miss.

**Alternatives rejected.** Paper proofs of the laws — rejected because
they go stale the first time the algebra evolves, and nobody notices.

### [I-DEC-4] One planner, one algebra, one plan type — 2026-04-25

**Context.** `osi_impl` had three planners (`SimplePlanner`,
`MultiDatasetPlanner`, `LODPlanner`) that drifted. I-DEC-3 in `osi_impl`
deleted the first two but only after they had accumulated hundreds of
LOC and test files.

**Decision.** `osi_python` has exactly one `Planner` class. Exactly one
`SemanticQuery` input type. Exactly one `QueryPlan` output type. No
fast-paths, no variants, no "simple" version.

**Rationale.** Two ways to plan the same query is a bug magnet. The
marginal performance of a single-table fast path is dwarfed by the
maintenance cost.

**Consequences.** Any specialization (single-table, multi-fact,
semi-join-only) lives as a branch *inside* `Planner.plan()`, not as a
separate class. If internal complexity grows, we split by helper
function, not by public class.

**Alternatives rejected.** Keeping a `SimplePlanner` for single-table
queries — rejected for the same reason `osi_impl` deleted theirs.

### [I-DEC-5] SQLGlot is the only SQL-manipulation tool — 2026-04-25

**Context.** String concatenation for SQL is always a local-looking
optimization that grows dialect-portability bugs.

**Decision.** All SQL is built via `sqlglot.exp.*` AST nodes. A CI
check greps the source for `f".*SELECT\b"`, `f".*FROM\b"`, etc., and
fails on a match outside allow-listed test-fixture files.

**Rationale.** SQLGlot handles quoting, escaping, operator precedence,
and dialect translation correctly. A single abstraction collapses a
large space of possible bugs.

**Consequences.** Contributors touching codegen need working knowledge
of SQLGlot AST. The small learning curve is offset by uniform handling
of dialect edge cases.

**Alternatives rejected.** Hand-rolled SQL emitter — rejected because
it would require re-implementing most of SQLGlot. Jinja-templated SQL —
rejected because templating combines the bugs of string concatenation
with the opacity of macro expansion.

### [I-DEC-6] Hard cap on source file size — 2026-04-25

**Context.** `osi_impl`'s `planner_lod.py` grew to 4121 LOC and became
un-reviewable. I-8 in `osi_impl` is "physical split of planner_lod.py"
and is still planned.

**Decision.** No file in `src/osi/` exceeds 600 LOC. A CI check audits
file sizes and fails when the cap is crossed. A PR that justifiably
needs a larger module must split it first, in a separate PR that lands
before the feature.

**Rationale.** Reviewability is the second design priority (SPEC §1.1).
A 4000-LOC file cannot be reviewed; the reviewer skims and ships.

**Consequences.** Modules are split earlier than feels necessary. That
is the point: the cost of splitting at 400 LOC is low; the cost of
splitting at 4000 LOC is enormous.

**Alternatives rejected.** "Guideline" soft cap — rejected because
soft caps fail silently every time.

### [I-DEC-7] Strict mypy and `extra="forbid"` pydantic from day one — 2026-04-25

**Context.** `osi_impl` reached "zero mypy errors" as I-3, after the
code was already written. The retrofit took effort and left legacy
`# type: ignore` comments.

**Decision.** `osi_python` starts with `strict = True` mypy and
`extra = "forbid"` on every pydantic model. A `# type: ignore` without
an accompanying `# type: ignore[<error-code>]  # reason: <one-liner>` is
a lint error.

**Rationale.** Types catch a class of bug early. Retrofitting strictness
is painful because it means revisiting every call site; starting strict
keeps the cost incremental.

**Consequences.** Onboarding contributors to mypy strict mode takes
~half a day per person. Pair with PR review.

**Alternatives rejected.** "Gradual typing" — rejected because the
gradient never lands; it leaves permanent legacy.

### [I-DEC-8] AGGREGATE-from-child via `enrich` is allowed when the child is uniquely keyed — 2026-04-28

**Context.** Bridge resolution mid-pipeline (`§6.5.1`, I-17) requires
joining a pre-aggregated fact-side state into a bridge state at a
finer grain. The natural shape is `enrich(parent=bridge, child=preagg,
child_keys=link_keys)`. The original algebra refused unconditionally
to surface AGGREGATE columns through `enrich`, on the (correct)
ground that doing so over a *fan-out* join would silently invalidate
the aggregate.

**Decision.** Relax `enrich` to accept AGGREGATE child columns iff
the existing fan-trap check passes — i.e., the child is unique on
its `child_keys` (its grain or one of its UKs is a subset). When
allowed, the column is reclassified to `FACT` with empty
dependencies, so downstream operators see it as a row-value. The
unsafe case is unchanged: a fan-out join still fails with
`E3011_MN_AGGREGATION_REJECTED` *before* we examine the column kinds.

**Rationale.** The relaxation's precondition is exactly the
algebra's existing safety invariant. There is no mathematical risk:
a fan-trap-safe enrich produces at most one matching child row per
parent row, so the aggregate value is preserved verbatim. Refusing
this case is over-strict — it blocks the entire mid-pipeline bridge
shape that the spec mandates.

**Consequences.** Bridge resolution composes cleanly. The algebra's
column-kind invariant gains a new edge case (AGGREGATE-in-child
becomes FACT-in-result), recorded in `algebra/operations.py::enrich`
with a comment pointing to this entry. The fan-trap test in
`tests/unit/planning/algebra/test_operators.py` was reframed from
"unconditional rejection" to "fan-out rejection + safe-relaxation
acceptance" with paired tests for both branches.

**Alternatives rejected.** A new operator (`seal_aggregates` or
similar) for the AGGREGATE→FACT reclassification — rejected because
it would force every bridge plan to emit an extra step for a
relabeling that's already implicit in the safety check. Keeping the
algebra closed under nine operators is preferable.

### [I-DEC-9] `ORDER BY` NULL placement uses the SQL:2003 high-end-NULL convention — 2026-05-13

**Context.** D-029's original wording (S-13) defaulted `ORDER BY <expr>`
without an explicit `NULLS …` clause to **`NULLS LAST` regardless of
sort direction**. This satisfied D-014 byte-identical determinism but
broke the **symmetry property** that flipping `ASC ↔ DESC` flips NULL
placement. A user inspecting a "top-10 by revenue" report and flipping
to "bottom-10 by revenue" would expect the NULL-revenue rows to move
into view (they *are* the worst values by any reasonable interpretation
of "missing revenue"); under the original rule the NULLs never moved.

**Decision.** Adopt the **SQL:2003 high-end-NULL convention** as the
Foundation default: `ASC ⇒ NULLS LAST`, `DESC ⇒ NULLS FIRST`. NULL is
treated as a high-end value that lands at whichever end the maximum
lands at. Codegen guarantees the resolved row order on every supported
dialect by emitting the explicit `NULLS …` clause whenever the dialect's
native default would produce a different order. When the resolved clause
already matches the dialect's native default (e.g. `DESC NULLS FIRST` on
Snowflake, `ASC NULLS LAST` on DuckDB), the explicit clause MAY be
elided — both forms produce identical row orders, so D-014's
per-`(model, query, dialect)` byte-identical guarantee is preserved.

**Rationale.** Three reasons in priority order:

1. **Symmetry under direction flip.** The single behaviour every BI
   mental model assumes. Pinning NULLs to a fixed end (always LAST or
   always FIRST) loses this and forces every author to write the
   explicit clause to recover the obvious behaviour.
2. **Standards alignment.** SQL:2003 says NULLs compare-greater than
   non-NULLs by default. The new convention follows this and matches
   the out-of-the-box defaults of Snowflake, PostgreSQL, and Oracle.
   Spark/Databricks (low-end NULL) becomes the lone divergence target,
   reducing the SD-2 surface from "two engines disagree with us" to
   "one engine disagrees with us."
3. **Lower compiled-SQL noise on the most-deployed warehouse.** The
   OSI compiler may now elide the `NULLS …` clause entirely when
   compiling for Snowflake (because the dialect default already
   produces the resolved order). This shrinks the diff between
   user-written SQL and OSI-compiled SQL on the warehouse where most
   models actually run.

**Consequences.** D-029 is amended (the wording in
`Proposed_OSI_Semantics.md` §5.1, §6.10.2, §11, and Appendix B reflects
the new rule). `SPEC.md` §1.3 and the §11 sprint table likewise.
`SNOWFLAKE_DIVERGENCES.md` SD-2 is rewritten — Snowflake is no longer
divergent on this rule; Spark/Databricks is. `src/osi/codegen/transpiler.py`
flips `nulls_first=False` to `nulls_first=o.descending`. Three
compliance gold SQL files (`t-027`, `t-032`, `t-036`) flipped from
`DESC NULLS LAST` to `DESC NULLS FIRST`. The compliance suite gains a
new test `t-062-nulls-first-default-on-desc` that locks in the
symmetric DESC counterpart of `t-026`. Golden snapshot for
`test_sql__order_by_and_limit` regenerated to reflect dialect-aware
elision (`DESC NULLS FIRST` on ANSI/DuckDB, `DESC` alone on Snowflake).
**100% compliance preserved** (67/67).

A known limitation: sqlglot's parser conflates `OVER (ORDER BY x DESC)`
and `OVER (ORDER BY x DESC NULLS LAST)` into the same AST shape
(`nulls_first=False`), so a user who writes the explicit `NULLS LAST`
inside a window function cannot have it preserved through round-trip.
This is a tooling limitation, not a spec ambiguity; documented in the
S-D-029-amendment retro and in the SD-2 caveat.

**Alternatives rejected.**

- *Spark/Databricks convention* (`ASC NULLS FIRST` / `DESC NULLS LAST`,
  low-end NULL) — would also restore the symmetry property, but
  requires more compiled-SQL noise (Snowflake/PostgreSQL/Oracle would
  all need the explicit clause emitted) and disagrees with the SQL:2003
  default. Picked the high-end convention because it minimises both
  engine-specific noise and standards friction.
- *"Always NULLS FIRST" or "always NULLS LAST"* — the original rule.
  Fails the symmetry property. The argument that "every BI surface puts
  NULLs last" doesn't hold up: those surfaces are presentation layers
  with well-known UX complaints for exactly this reason.
- *Reject models that omit the explicit clause* — would force every
  user to write the `NULLS …` token everywhere, which is hostile and
  doesn't add safety beyond what dialect-aware emission already gives.
