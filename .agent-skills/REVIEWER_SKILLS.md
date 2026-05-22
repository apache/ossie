# Reviewer skills index

This file is the index for the nine dual-purpose reviewer skills under
`.agent-skills/`. Each skill is usable at **review time** (auditing
existing code) *and* at **design time** (locking in boundaries when
writing new code), and each one carries the same triage rule —
*prefer deterministic enforcement first* — so findings flow into the
test suite, not just review reports.

## The nine skills

| Skill | Angle | Primary deterministic checks it leverages |
|:--|:--|:--|
| [`architectural-review/`](architectural-review/SKILL.md) | Three-layer pipeline, one-way information flow, closed algebra, the numbered invariants in `impl/python/ARCHITECTURE.md §6`. | `import-linter`, `tests/properties/test_algebra_*`, `tests/unit/test_appendix_c_drift.py` |
| [`interfaces-and-api-review/`](interfaces-and-api-review/SKILL.md) | Public facades, signature shape, total functions, "exceptions in the docstring." | `mypy --strict`, `flake8-docstrings`, layer `__init__.py` re-exports |
| [`code-encourages-correct-use-review/`](code-encourages-correct-use-review/SKILL.md) | "Make illegal states unrepresentable." Construction-time invariants, no sentinels, no `bool` flags. | `mypy --strict`, `tests/properties/test_algebra_purity.py`, `tests/unit/test_common_identifiers.py` |
| [`bi-best-practices-review/`](bi-best-practices-review/SKILL.md) | Grain awareness, fan-out / chasm trap, bridge dedup, conformed dims, semi-additive measures, holistic-over-fan-out rejection. | `tests/properties/test_grain_closure.py`, `tests/properties/test_chasm_safety.py`, the `compliance/foundation-v0.1/tests/bridge/` and `cross_grain/` suites |
| [`compiler-best-practices-review/`](compiler-best-practices-review/SKILL.md) | Phase boundaries, IR purity, totality, deterministic codegen, error taxonomy, pass ordering. | `tests/properties/test_algebra_*`, `tests/unit/test_operator_enum_sync.py`, `mutmut` on `planning/algebra/` ≥ 90% |
| [`database-best-practices-review/`](database-best-practices-review/SKILL.md) | SQL emission via AST not strings, identifier quoting, NULL ordering, multiset vs set semantics, dialect adapter isolation, FrozenSQL discipline. | banned-f-string-SQL grep, `tests/properties/test_frozensql_canonical.py`, per-dialect golden tests, `tests/e2e/` against DuckDB |
| [`doc-as-enforcement-review/`](doc-as-enforcement-review/SKILL.md) | Doc claims backed by drift tests; layer READMEs vs filesystem; citation conventions; example-runnable READMEs. | `tests/unit/test_appendix_c_drift.py`, the four Phase C drift tests added by the audit. |
| [`typing-enforcement-review/`](typing-enforcement-review/SKILL.md) | `Any` and `dict[str, Any]` audit, `NewType` discipline, `Protocol` and `TypedDict`, frozen-by-default, mypy strictness. | `mypy --strict`, `tests/unit/test_common_identifiers.py`, the every-`Exception`-is-`OSIError` arch-test added in Phase D |
| [`spec-coherence-review/`](spec-coherence-review/SKILL.md) | Spec ↔ Appendix C ↔ `ErrorCode` ↔ compliance tests stay coherent; deferred-feature gate; `decisions.yaml` ↔ tests. | `tests/unit/test_appendix_c_drift.py`, `test_registry_yaml.py`, the Phase C spec-section-refs drift test |

## The triage rule (encoded in every SKILL.md)

Every skill above carries this rule verbatim. Apply it whether you're
using the skill at review time or at design time:

1. **Convert to a deterministic check** — drift test, arch-test,
   import-linter contract, mypy rule, lint rule. Preferred. Never
   regresses; applies to every future change automatically.
2. **Sharpen the skill's checklist** — if the finding revealed a
   missing angle, update the `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / a README /
   `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

Design-time framing: before writing code that establishes a boundary
or invariant, ask "can I add a deterministic check that locks this
in before the code lands?" If yes, write the check in the same PR.

## Cadence

The cadence rule lives in `CONTRIBUTING.md` at the repo root. Summary:

- **Any architectural change** runs the BI, Compiler, and Database
  best-practices skills at design time *and* at review time. These
  three are the non-negotiable triad for behavioural changes.
- **Any new public API** runs the interfaces, code-encourages-correct-use,
  and typing-enforcement skills.
- **Any new spec section, error code, or compliance test** runs the
  spec-coherence and doc-as-enforcement skills.
- **Any new doc that codifies a rule** runs the doc-as-enforcement skill.

## Recommended sweep order (when running all nine against a corpus)

1. `architectural-review` — establishes which layer each change touches;
   surfaces structural issues that downstream skills will keep tripping
   on if not fixed first.
2. `spec-coherence-review` — confirms the spec ↔ code ↔ tests baseline
   before behavioural skills assume it.
3. `bi-best-practices-review` — behavioural correctness at the planner
   level (grain, fan-out).
4. `compiler-best-practices-review` — engineering correctness inside
   the planner (phases, IR purity).
5. `database-best-practices-review` — emission correctness in codegen.
6. `interfaces-and-api-review` — public surface hygiene.
7. `code-encourages-correct-use-review` — construction discipline.
8. `typing-enforcement-review` — mypy / `NewType` / `Protocol`
   refinements.
9. `doc-as-enforcement-review` — convert documented invariants from
   the first eight passes into drift tests.

## Existing companion skills (run by these reviewer skills)

These skills are referenced inside the nine SKILL.md files; they are
the "doing" skills (run something, navigate the BI mapping, debug an
output) that the reviewer skills point to as ground truth.

| Skill | Purpose |
|:--|:--|
| [`run-osi-python-tests/`](run-osi-python-tests/SKILL.md) | Run the full Python test pyramid (unit + property + golden + e2e + mutation + lint + typecheck + arch). |
| [`run-osi-compliance/`](run-osi-compliance/SKILL.md) | Run the Foundation v0.1 compliance suite and produce a per-decision coverage report. |
| `bi-concepts-to-osi` (carried in `willtown/.cursor/skills/`) | Map a BI analytical concept to an OSI model + query. |
| `convert-to-osi` (willtown) | Convert a DAX / LookML / Tableau LOD / dbt metric to an OSI metric. |
| `sql-to-semantic` (willtown) | Turn a SQL query / schema into an OSI semantic model + query. |
| `debug-planner-output` (willtown) | Root-cause an unexpected query plan or SQL output. |
| `diagnose-failing-test` (willtown) | Classify a failing pytest output by failure type. |
| `add-new-filter-or-operator` (willtown) | Add a new filter shape, SQL operator, or BI idiom end-to-end. |
| `osi-impl-add-planner-feature` (willtown) | Extend the OSI planner with a new BI analytical concern. |
