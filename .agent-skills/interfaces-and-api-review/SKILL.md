---
name: interfaces-and-api-review
description: Review or design the public API surface and interfaces of the OSI reference implementation — the per-layer facades (osi.parsing, osi.planning, osi.codegen, osi.diagnostics, osi.errors), the CLI, and the contracts between sub-packages. Use when adding a public function, exposing a new type at a layer boundary, designing the shape of a new module's exports, or evaluating whether an API is small, total, and hard to misuse.
---

# Interfaces and API review

A reference implementation lives or dies on the *shape* of its API. The
right interface makes incorrect use impossible. This skill is the
playbook for designing and reviewing public surfaces — what gets
exported from each `__init__.py`, what signatures look like, and where
the seams between sub-packages sit.

## 1. Purpose

Keep every public surface small, total, and *hard to misuse*. Each
layer facade exposes the minimum set of types and functions a caller
needs, with signatures that fail at type-check time when used wrong,
and with clear ownership of every error path.

## 2. When to use it (Review)

Apply this skill when the change:

- Adds, removes, or renames a symbol in any layer `__init__.py`
  (`osi.parsing`, `osi.planning`, `osi.codegen`, `osi.diagnostics`,
  `osi.common`, `osi.errors`).
- Adds a new public function or class anywhere under `src/osi/`.
- Changes the signature of an existing public function — argument
  types, defaults, return type, exception set.
- Adds a new CLI command or flag in `src/osi/cli.py`.
- Introduces a new sub-package or splits an existing one.

## 3. When to use it (Design)

Apply this skill *before* writing the implementation when you are:

- Designing a new entry point users will call directly.
- Sketching how an internal helper becomes "public" (used by another
  layer or surfaced to CLI / SDK consumers).
- Deciding what data lives on a `PlanStep`, a `CalculationState`, or a
  diagnostic record.
- Choosing between "one big function with options" and "several small
  functions with one job each."
- Deciding which exceptions a function may raise — and how callers
  discriminate them.

At design time the goal is: *write the signature first*. If you can
write a clean, type-checked signature, the implementation has a chance.
If you can't, the abstraction is wrong.

## 4. Methodology

1. **Enumerate the surface.** List every public symbol the change adds
   or modifies. Public = re-exported from a layer `__init__.py`, named
   in `ARCHITECTURE.md`, or referenced by an external test / example /
   compliance adapter.
2. **Check the layer facade contract.** Each layer's `__init__.py`
   re-exports a curated set; any new public symbol must be added to the
   facade in the same PR (`ARCHITECTURE.md §6.8`). A public symbol that
   is not re-exported is a documentation bug.
3. **Audit the signature.** Verify:
   - All arguments are typed; no `Any`, no untyped `**kwargs`.
   - Frozen-by-default inputs (`SemanticModel`, `PlannerContext`,
     `QueryPlan`) come first, mutable / per-call inputs last.
   - Return type is a concrete frozen value, not `tuple[Any, ...]` or
     `dict[str, Object]`.
   - Optional arguments use `Optional[T] = None`, not `T = object()` /
     sentinel hacks.
4. **Audit the exceptions.** The function's docstring lists every
   `ErrorCode` it raises. Each code is in the public Appendix C set or
   in `_IMPLEMENTATION_EXTENSIONS`. No bare `Exception`, no `RuntimeError`.
5. **Audit name + place.** The function lives in the right sub-package
   (`ARCHITECTURE.md §8` rules). The function name reflects its return
   type and side effects (e.g. `parse_*` returns a parsed value, never
   `None` on failure — it raises).
6. **Audit reachability.** Is the new public surface reachable through
   the CLI? Through an example? Through at least one unit test? An
   unused public symbol is dead weight; remove it or wire it up.

## 5. Checklists

### 5.1 Facade hygiene

- [ ] Every new public symbol is re-exported in the relevant
      `__init__.py`.
- [ ] `__init__.py` has no logic — only imports and `__all__`.
- [ ] No symbol is exposed at two layers (e.g. `Identifier` lives only
      in `osi.common.identifiers`).
- [ ] No private symbol (`_underscore`) is imported across a layer
      boundary.

### 5.2 Signature shape

- [ ] All arguments and return are typed.
- [ ] No `Any`, no `object`, no untyped `dict[str, Any]` in public API.
- [ ] Frozen dataclasses for any structured argument or return.
- [ ] `NewType`s used for identifiers (`Identifier`, `CTEName`,
      `ExpressionId`).
- [ ] Enums for closed sets (`Dialect`, `ErrorCode`, `PlanOperation`).
- [ ] Mutable defaults are forbidden (`def f(x: list = [])` — never).

### 5.3 Total functions

- [ ] The function either returns its declared type or raises an
      `OSIError`. It never returns `None`-meaning-failure.
- [ ] All exception paths are typed `OSIError` subclasses with codes.
- [ ] Docstring lists each raised `ErrorCode`.

### 5.4 Discoverability

- [ ] At least one unit test imports the symbol from the public facade.
- [ ] If the symbol is user-facing, it appears in
      `impl/python/README.md` or an `examples/` script.
- [ ] If the symbol is in the CLI, `cli.py` documents the flag in its
      help string and there's an integration test under
      `tests/integration/cli/`.

### 5.5 Hard-to-misuse construction

- [ ] Construction goes through a typed entry point
      (`parse_semantic_model`, `Planner.plan`, `compile_plan`,
      `source(...)`, etc.). Direct dataclass construction is reserved
      for inside the layer that owns the type.
- [ ] Cross-cutting types (`Identifier`, `Dialect`) are produced by
      named factories (`normalize_identifier(s)`,
      `Dialect.from_string(s)`), not by raw strings reaching the API.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — drift test, arch-test,
   import-linter contract, mypy rule, lint rule. Preferred. Never
   regresses; applies to every future change automatically.
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
| `mypy --strict` | Typed signatures, no `Any` leakage at public surface | `pyproject.toml [tool.mypy]` |
| `import-linter` contracts | Layers see only their declared dependencies | `pyproject.toml [tool.importlinter]` |
| `tests/unit/test_appendix_c_drift.py` | Every public error code is in the spec or documented | source |
| `tests/properties/test_error_taxonomy.py` | Public surfaces raise only `OSIError` | source |
| `tests/unit/test_synthetic_naming_invariants.py` | Synthetic names come from `prefixes.py`, not the API | source |
| `tests/unit/test_common_identifiers.py` | Identifier construction goes through `normalize_identifier` | source |
| `flake8-docstrings` | Public symbols have docstrings | `pyproject.toml` |
| Phase C `c4-layer-readme` (added by this audit) | Layer READMEs list every public symbol | `tests/unit/test_layer_readme_drift.py` |

## 8. Example output format

```markdown
## I-001 `Planner.plan` returns a tuple instead of a typed QueryPlan

- **Severity**: P1 (callers must destructure, easy to misuse)
- **Location**: `impl/python/src/osi/planning/planner.py:412`
- **Finding**: `Planner.plan` returns `tuple[QueryPlan, dict[str, Any]]`
  where the second element is an "annotations" payload used only by
  diagnostics. Callers that don't need annotations destructure and
  discard, but the dict is `dict[str, Any]` — defeating the typed-API
  intent.
- **Triage**:
  1. (Deterministic) — Add an arch-test that asserts no public function
     returns a `dict[str, Any]`. Lands in this PR (small).
  2. (Skill) — Add `dict[str, Any] in return` to §5.2 checklist.
  3. (Code) — Define `PlanAnnotations` (frozen dataclass), change the
     return to `tuple[QueryPlan, PlanAnnotations]`, or — better —
     attach annotations to `PlanStep`. Out of scope for this PR; queue.
```

## 9. Anti-patterns

- A new public function that returns `Optional[T]` to signal failure.
  Callers won't check; raise an `OSIError` instead.
- An "options" dict (`options: dict[str, Any]`) on a public signature.
  Make it a frozen dataclass with typed fields.
- A new symbol exposed at the layer's public facade but not added to
  `__all__`. Stale facades drift; the symbol becomes "semi-public,"
  with no test guard.
- A public function that takes a raw `str` where an `Identifier` /
  `Dialect` / `ErrorCode` is meant. Push the conversion into a factory
  and accept the typed value.
- A second function with the same job ("`compile_plan_v2`,
  `compile_fast`, `compile_safe`"). One canonical API per concept;
  variants belong as flags on the canonical signature or as separate
  named entry points with distinct purposes.

## See also

- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) §1.1, §6.8, §9.
- [`code-encourages-correct-use-review/SKILL.md`](../code-encourages-correct-use-review/SKILL.md) — sister skill focused on hard-to-misuse construction.
- [`typing-enforcement-review/SKILL.md`](../typing-enforcement-review/SKILL.md) — sister skill focused on mypy strictness and `NewType` discipline.
