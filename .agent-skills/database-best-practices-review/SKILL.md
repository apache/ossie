---
name: database-best-practices-review
description: Review or design code against database/SQL engineering best practices — SQL emission via AST not strings, identifier quoting and case-folding, NULL ordering, multiset vs set semantics in compliance assertions, dialect adapter design, predicate/projection pushdown surface, FrozenSQL canonical-form discipline. Use when adding or touching codegen, dialect adapters, the compliance harness's row-comparison logic, or any code that emits, parses, or compares SQL.
---

# Database best-practices review

A semantic-layer compiler that emits SQL must respect what real
databases actually *do* — not the textbook SQL semantics. This skill
is the playbook for verifying that emitted SQL is correct,
deterministic, and safely portable across engines, and for designing
codegen so the dialect surface area is explicit rather than implicit.

## 1. Purpose

Ensure every emitted SQL string is built via SQLGlot AST (never
f-strings or concatenation), every identifier is properly quoted and
case-folded, every test that compares output uses multiset semantics
where the spec demands it, and every dialect-specific quirk is named
and isolated.

## 2. When to use it (Review)

Apply when the change:

- Touches `codegen/transpiler.py`, `codegen/cte_optimizer.py`,
  `codegen/dialect.py`, or `codegen/types.py`.
- Adds or modifies a dialect (`Dialect.DUCKDB`, `Dialect.SNOWFLAKE`,
  `Dialect.ANSI`).
- Adds or modifies a compliance-harness assertion (multiset, ordering,
  NULL semantics).
- Uses any `str.format`, `+`, or f-string to construct anything
  SQL-shaped in `src/`.
- Touches `osi.common.identifiers` or `osi.common.sql_expr`
  (`FrozenSQL`).
- Adds a new compliance adapter, or modifies the existing
  `osi_python_adapter.py`.
- Introduces a new error code from a SQL execution failure.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Designing a new dialect adapter.
- Designing how a SQL feature (CTE chain, window frame, set op) will be
  emitted across dialects.
- Designing how compliance rows are compared against `gold_rows.json`.
- Choosing how to represent NULL behaviour in tests
  (`NULLS FIRST` / `NULLS LAST` / `NULLS IGNORED`).
- Designing the predicate/projection-pushdown surface that planner
  emits to codegen.
- Considering an "optimisation" at codegen time that requires the
  rendered SQL string to be re-parsed and rewritten.

## 4. Methodology

1. **Audit SQL construction.** Verify every SQL fragment goes through
   `sqlglot.exp.*` nodes, `FrozenSQL.of(...)`, or
   `parse_sql_expr(...)`. No f-string SQL, no `+ ' WHERE ' +`, no
   `.format(...)` with SQL keywords.
2. **Audit identifier handling.** Every identifier passes through
   `normalize_identifier(...)` (case folding) before comparison.
   Every identifier rendered into SQL is quoted by SQLGlot's
   dialect-aware quoter, not by hand.
3. **Audit dialect divergence.** Every dialect-specific behaviour is
   isolated in `codegen/dialect.py`. The planner has no knowledge of
   dialect; codegen only branches on `Dialect` at the dialect layer,
   never deep in transpilation.
4. **Audit NULL semantics.** Every aggregate over `NULL`s declares its
   behaviour (`SUM(NULL)`, `COUNT(*)` vs `COUNT(col)`, `MAX` over
   all-NULL groups). Every `ORDER BY` declares `NULLS FIRST` or
   `NULLS LAST` if results matter.
5. **Audit comparison semantics.** Compliance assertions over result
   sets use *multiset* (bag) equality by default. Set equality is wrong
   (`SELECT 1 UNION ALL SELECT 1` is two rows, not one). Order equality
   is only correct when the spec mandates an `ORDER BY`.
6. **Audit pushdown surface.** Every predicate/projection that the
   planner intends codegen to push down is declared on the plan step
   (a `predicate_pushdown` annotation, a `projection_set`); codegen
   reads it, never re-derives it.

## 5. Checklists

### 5.1 SQL construction discipline

- [ ] No f-string in `src/` that contains a SQL keyword
      (`SELECT`, `FROM`, `WHERE`, `JOIN`, `GROUP BY`, `HAVING`,
      `ORDER BY`, `UNION`, `WITH`).
- [ ] No `+ "..."` or `.join(...)` building a SQL clause.
- [ ] Every SQL expression is built via `sqlglot.exp.*` nodes or
      goes through `parse_sql_expr(...)` and ends as a `FrozenSQL`.
- [ ] CTE chains are constructed via `sqlglot.exp.CTE`, not by string
      templates.

### 5.2 Identifier safety

- [ ] All identifier comparisons go through
      `osi.common.identifiers.identifiers_equal(...)` (or implicitly
      via `Identifier` `NewType` equality after `normalize_identifier`).
- [ ] No raw `==` on identifier strings (`flake8` should catch this;
      treat any survivor as a review issue).
- [ ] Quoting at SQL emission is delegated to
      `sqlglot.exp.to_identifier(...)` with the dialect set.
- [ ] Reserved-name collisions are caught at parse time (D-019,
      `E_RESERVED_NAME`); codegen does not re-check.

### 5.3 Dialect isolation

- [ ] `Dialect` is an enum with explicit named members; no `str`
      dialect in public APIs (`compile_plan(dialect=Dialect.DUCKDB)`,
      never `dialect="duckdb"`).
- [ ] Dialect-specific AST transforms live in `codegen/dialect.py`.
- [ ] Tests that pin SQL output do so per-dialect (golden files named
      `expected.<dialect>.sql`).
- [ ] No "if dialect == X" inside `transpiler.py`; route through the
      dialect module.

### 5.4 NULL handling

- [ ] `SUM` over an empty group is `NULL` in SQL — confirm test rows
      expect `NULL`, not `0`, unless the metric body wraps in
      `COALESCE`.
- [ ] `COUNT(*)` and `COUNT(col)` are distinct; the metric body
      determines which.
- [ ] `ORDER BY` that matters for test row order declares
      `NULLS FIRST` / `NULLS LAST` per-dialect — defaults differ
      (Postgres = `NULLS LAST`, others vary).
- [ ] `MIN` / `MAX` over an all-NULL group returns `NULL`, not an
      error.

### 5.5 Result-set comparison

- [ ] Compliance harness compares `gold_rows.json` against the engine
      output as a *multiset* unless `order_sensitive: true` on the
      test metadata.
- [ ] Float comparison uses an explicit tolerance for non-integer
      metrics; `assert == 3.14` is a flaky test.
- [ ] Comparisons over date / timestamp normalise time zone explicitly.

### 5.6 Pushdown surface (planner → codegen)

- [ ] Every predicate / projection codegen renders is declared on a
      `PlanStep` field; codegen does not re-classify filters or
      re-resolve names.
- [ ] No codegen path opens the `SemanticModel`, the `Namespace`, or
      the `RelationshipGraph`.
- [ ] CTE inlining / chaining / folding in `cte_optimizer.py` operates
      on a fully-typed AST; it does not invent new column names.

### 5.7 FrozenSQL discipline

- [ ] `FrozenSQL.of(...)` is the only constructor; raw
      `FrozenSQL(...)` calls outside `osi.common.sql_expr` are bugs.
- [ ] `sql_expr_equal(a, b)` is the only correct equality on SQL
      expressions; `==` on raw `sqlglot.Expression` is not stable.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — a custom flake8 / `rg` lint
   for banned tokens, a unit test that asserts no f-string SQL, a
   golden test that pins per-dialect emission, a property test on
   identifier quoting. Preferred. Never regresses.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / a README /
   `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing code that
establishes a boundary or invariant, ask "can I add a deterministic
check that locks this in before the code lands?" If yes, write the
check in the same PR.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| Banned f-string SQL grep | `f"...SELECT..."` and similar in `src/` | `INFRA.md §1.2` ("Ban raw-string SQL" + custom flake8 check) |
| `tests/properties/test_frozensql_canonical.py` | `FrozenSQL` equality is canonical-form-based | source |
| `tests/unit/test_common_identifiers.py` | Identifier comparisons go through `normalize_identifier` | source |
| `tests/unit/test_common_sql_expr.py` | `FrozenSQL.of` and `parse_sql_expr` agree on equality | source |
| `tests/golden/` (per-dialect) | Same plan → byte-identical SQL per dialect | source |
| `tests/e2e/` (DuckDB execution) | Emitted SQL actually runs and returns expected rows | source |
| `compliance/harness/src/harness/runner.py` row comparison | Multiset semantics by default | source |
| `import-linter` codegen ← parsing forbidden | Codegen does not reach back into the model | `pyproject.toml` |
| `mutmut` on `src/osi/codegen/` ≥ 75% | Dialect emission is tested against its laws | `INFRA.md §1.1` |

## 8. Example output format

```markdown
## DB-001 CTE optimiser builds `WHERE` clause by f-string

- **Severity**: P0 (banned construction; correctness risk + injection
  surface even in a generator)
- **Location**: `impl/python/src/osi/codegen/cte_optimizer.py:288`
- **Finding**: `_collapse_filter_chain` constructs the merged
  predicate as `f"({lhs}) AND ({rhs})"`. Loses identifier quoting and
  breaks if either side contains the dialect's escape character.
- **Triage**:
  1. (Deterministic) — Add a banned-pattern check to the `rg` lint:
     `"AND.*\\{` and similar f-string SQL fingerprints inside
     `src/osi/codegen/`. Lands in this PR.
  2. (Skill) — Add an explicit "CTE merge" bullet to §5.1.
  3. (Code) — Replace with
     `sqlglot.exp.and_(parse_one(lhs), parse_one(rhs))`; if the
     fragments are already `FrozenSQL`, use
     `sqlglot.exp.and_(lhs.expression, rhs.expression)`.
- **Invariants touched**: ARCHITECTURE.md §6.10 (SQL composition via
  AST only).
```

## 9. Anti-patterns

- "I'll just `'{table}'.format(table=name)` for a quick fix" — quoting,
  case, and reserved words go wrong. Use SQLGlot exp nodes.
- A codegen path that branches on `dialect == "snowflake"` inside the
  transpiler. Push the branch into `dialect.py`.
- A compliance test that asserts on result rows by *list* equality
  when the spec didn't mandate order. Use a multiset comparison.
- Comparing `FrozenSQL` instances with `==` on the underlying
  `sqlglot.Expression`. Use `sql_expr_equal`.
- Pinning a SQL string in a unit test for the planner — planner tests
  assert plan shapes, not SQL. SQL pinning is for codegen goldens.
- A new error code for a SQL execution failure ("E_NULL_IN_GROUP_BY").
  Codegen does not own runtime errors; the planner rejects the query
  at plan time, or the test must accept the engine's native error.

## See also

- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) §4 (codegen) and §6.10 / §6.11 (SQL / identifier invariants).
- [`../../impl/python/INFRA.md`](../../impl/python/INFRA.md) §1.2 (banned tokens), §1.3 (SQL correctness).
- [`../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md) — supported SQL subset.
- [`bi-best-practices-review/SKILL.md`](../bi-best-practices-review/SKILL.md) — sister skill on grain / fan-out at the BI / planner level.
- [`compiler-best-practices-review/SKILL.md`](../compiler-best-practices-review/SKILL.md) — sister skill on phase ordering / IR purity at the planner level.
