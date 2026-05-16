# Phase 10 — Standards Expert + Application Developer Review

This is the consolidated Phase 10 review referenced in the OSI_will
migration plan. It merges two parallel passes:

- [`10a_standards_review.md`](./10a_standards_review.md) — does the
  implementation exactly realise the proposal's normative text?
- [`10b_appdev_review.md`](./10b_appdev_review.md) — what does the
  adopter onramp feel like for a BI/data engineer cloning the repo?

Read the two sub-reports for full findings, severities, and file/line
citations. This document is the executive view used to drive Phase 10
fix-groups.

---

## Top-level verdict

| Dimension | Status | Phase 10 work? |
|:--|:--|:--|
| Pipeline boundaries / IR / typed errors | Sound (Phase 9 closed the remaining gaps) | No |
| Appendix C ↔ ErrorCode enum (drift test) | Drift test claims "verbatim" but includes a code (`E_UNKNOWN_FUNCTION`) that is not in Appendix C | **Yes — P0** |
| `osi_version` spec field | Spec admits it; parser rejects it as `extra_forbidden` | **Yes — P0** |
| `decisions.yaml` test paths | Several point at filesystem locations that do not exist | **Yes — P1** |
| §10 vs `SQL_EXPRESSION_SUBSET.md` (ordered-set / `PERCENTILE_CONT`) | Spec contradicts itself | **Yes — P1** |
| `ARCHITECTURE.md` §9 (signatures + phantom files) | Wrong `plan()` arg order; phantom `osi_cli.py` / `examples/run_example.py` | **Yes — P0** |
| README Scope vs example models | README says "deferred" for keys the examples use | **Yes — P0** |
| CLI entry-point packaging | No `[project.scripts]`; docs assume `osi …` shell command | **Yes — P0** |
| CLI error output | Drops `OSIError.context` — actionable hints invisible to terminal users | **Yes — P1** |
| `osi/__init__.py` façade | Documents imports in a docstring but does not re-export them | **Yes — P1** |
| Examples runnable end-to-end | `examples/` has YAML only; no query JSON; no `compile` walkthrough | **Yes — P1** |
| Catalog hygiene (stale file paths, stale CLI references) | Multiple stale references to `SQL_EXPRESSION_SUBSET_updated.md`, `E1105`, `osi explain` (future) | **Yes — P2** |

---

## Phase 10 fix queue

### P0 — Adopter-blocking + spec/impl coherence

1. **Accept `osi_version` per spec** (10a B2). Add the optional field
   to `SemanticModel`, validate `"0.1"`, raise a precise error for
   unsupported versions.
2. **Reconcile `E_UNKNOWN_FUNCTION` vs Appendix C** (10a B1). Add the
   code to Appendix C (with D-021 pointer), and remove the "verbatim"
   claim from the drift-test preamble.
3. **Fix `ARCHITECTURE.md` §9** (10b B1). Correct `plan()` arg order,
   remove phantom file references (`osi_cli.py`,
   `examples/run_example.py`), align CLI invocation with
   `python -m osi …` (or with the new console script — see P0/3).
4. **Reconcile README Scope with examples** (10b B2). Either remove
   the "deferred" bullets that no longer hold (because
   `FoundationFlags` admits them) or remove the offending content
   from the example models. Pick one source of truth.
5. **Register the `osi` console script** (10b B3). Add
   `[project.scripts] osi = "osi.cli:main"` to `pyproject.toml`, so
   `osi explain-code E_NO_PATH` works after `pip install -e .`.

### P1 — Adopter-friction + spec governance

6. **CLI prints `error.context`** (10b I1). When `OSIError.context`
   is non-empty, emit it as a structured indent under the code/message
   line (always or behind `--verbose`).
7. **`osi/__init__.py` re-exports the happy path** (10b I2).
   `from osi import parse_semantic_model, plan, compile_plan,
   Dialect, SemanticQuery, Reference, PlannerContext` should work.
8. **Bring `decisions.yaml` paths into sync with the filesystem**
   (10a I2). Run the harness drift test added in Phase 6; fix
   reported mismatches.
9. **Resolve §10 vs `SQL_EXPRESSION_SUBSET.md` for `PERCENTILE_CONT` /
   ordered-set aggregates** (10a I4). Decide whether the
   `WITHIN GROUP` form is Foundation or deferred and align all three
   docs.
10. **Ship at least one runnable example** (10b I4). Commit
    `examples/queries/<scenario>.json` + an `examples/README.md`
    that walks `python -m osi compile examples/models/<m>.yaml
    examples/queries/<q>.json --dialect duckdb`.
11. **README → `FoundationFlags`** (10b I5). Add a "Common errors"
    section linking to the flag mechanism for opt-out scenarios.

### P2 — Hygiene

12. **Fix stale file references in `error_catalog.py`** (10a I6) —
    `SQL_EXPRESSION_SUBSET_updated.md` → `SQL_EXPRESSION_SUBSET.md`.
13. **Replace `E1105` in `parser.py` docstring** (10a I5) — use the
    modern `E_DEFERRED_*` named codes.
14. **Remove "future" tag from `osi explain` reference in
    `error_catalog.py`** (10b N1) — the command exists as
    `explain-code`.
15. **README Quick Start ordering** (10b N2) — Python example before
    contributor `make` commands.
16. **Drift test preamble accuracy** (10a I1) — either the codes are
    in Appendix C verbatim, or the preamble says "working-set".
17. **Trim `planning/__init__.py` `__all__`** (10b I3) — split the
    surface between user API and `osi.planning.internals`.

---

## How Phase 10 will run

Each numbered item is its own commit (or one cohesive commit per
fix-group when items share files). Every commit runs the unit suite
plus the default compliance run. When Phase 10 is complete the user
will be asked whether to run Phase 11 (remove originals from
willtown).
