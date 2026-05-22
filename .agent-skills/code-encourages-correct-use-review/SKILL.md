---
name: code-encourages-correct-use-review
description: Review or design code so it pushes correctness into the type system rather than relying on caller discipline. Use when adding new state types, factory functions, constructors, or any code where a misuse would compile silently. Sister skill to interfaces-and-api-review and typing-enforcement-review; focused on construction-time invariants and "make illegal states unrepresentable" patterns.
---

# Code that encourages correct use

The cheapest bug is the one mypy or the constructor refuses. This skill
is the playbook for designing and reviewing code so that the type
system, constructor preconditions, and frozen-by-default values rule
out misuse — and so a contributor who tries the obvious wrong thing
gets a typed error, not silently wrong SQL.

## 1. Purpose

Ensure every new construct makes the *right* use easy and the *wrong*
use either uncompileable or fail-fast. The architecture's correctness
guarantees only hold if every value is constructed through the right
gate — this skill is the gate inspection.

## 2. When to use it (Review)

Apply when the change:

- Adds a frozen dataclass that will travel between layers.
- Adds a factory function or constructor — anything that materialises
  a "blessed" value (`CalculationState`, `QueryPlan`, `PlanStep`,
  `Identifier`, `Dialect`, `FrozenSQL`).
- Accepts a raw `str` where a typed identifier / code / dialect is
  meant.
- Uses a sentinel value (`None`, `-1`, `""`) to mean "unset" or
  "missing."
- Has a boolean parameter that toggles meaning ("if True, do X else
  do Y").
- Introduces a new union of result shapes (`Result[T, E]`,
  `Union[A, B]`) at a public API.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Sketching a new value type for the pipeline.
- Deciding whether to expose a constructor or hide it behind a factory.
- Choosing between a `bool` flag, an `Enum`, or separate functions.
- Choosing between `Optional[T]` and a typed "absent" value.
- Wondering whether a check belongs at parse time, plan time, or
  codegen time — usually the earliest layer that has enough information
  is the right answer.

At design time, write the *worst* code a contributor could plausibly
write against your new API. If your design lets it compile, redesign.

## 4. Methodology

1. **Identify the gate.** For each new type or value, identify the
   single function that mints it. If there are two, that's already a
   smell — `CalculationState` only comes from `source(...)` or an
   algebra operator (`ARCHITECTURE.md` invariant 1); `Identifier` only
   comes from `normalize_identifier(...)`; etc.
2. **Make the gate the only door.** Verify the type's `__init__` is
   either private (`_State.__init__`) or has preconditions that no
   raw caller could plausibly satisfy. Check that the gate is exported
   and the raw `__init__` is not.
3. **Push checks earlier.** A check at parse time beats a check at plan
   time beats a check at codegen time. If a runtime check could have
   been a type, replace it.
4. **Audit the bool flags.** Every `bool` parameter is a request for a
   future bug. Prefer a 2-variant `Enum`; prefer separate functions
   when the bodies share <50% of the code.
5. **Audit the sentinels.** No `None`-meaning-error, no `""`-meaning-
   absent, no `-1`-meaning-index-not-found. Raise instead.
6. **Audit the `Any` and `dict`.** Any time `Any` or `dict[str, Any]`
   appears on a public boundary, ask: can this be a `Protocol`, a
   `TypedDict`, or a frozen dataclass?

## 5. Checklists

### 5.1 Construction discipline

- [ ] No new "blessed" type is constructed outside its owning module
      *except* through a public factory.
- [ ] The factory is the only export in the module's `__init__.py`
      (the raw type stays for type annotations, the factory for
      construction).
- [ ] All factories validate inputs before returning; an invalid input
      raises `OSIError`, never returns `None`.

### 5.2 Make illegal states unrepresentable

- [ ] Closed sets are enums, not strings. `Dialect`, `PlanOperation`,
      `ErrorCode`, `JoinType` are enums; new closed sets follow the
      pattern.
- [ ] Identifiers travel as `Identifier` (the `NewType`), not as `str`.
- [ ] Synthetic names come from `prefixes.py`, not from arbitrary
      f-strings.
- [ ] "Optional with a default" uses `Optional[T] = None`, not a
      sentinel of `T`.
- [ ] "Present but absent" (e.g. a SQL `NULL`) has a dedicated value,
      not a missing key.

### 5.3 Push checks earlier

- [ ] Schema validity is checked in `parsing/` (pydantic + `validation.py`).
- [ ] Cross-reference validity is checked in `parsing/` (`Namespace` /
      `RelationshipGraph` construction).
- [ ] Cardinality and grain are checked in `planning/`, before codegen
      runs.
- [ ] Dialect-specific behaviour is checked in `codegen/dialect.py`,
      before SQL is emitted.

### 5.4 Boolean discipline

- [ ] No `do_X: bool = False` parameter. Use an enum or split the
      function.
- [ ] `if` ladders over a `bool` enum-equivalent are replaced with a
      dispatch.

### 5.5 Failure modes

- [ ] No `Optional[T]` return type used to signal failure.
- [ ] No `Result[T, E]` /  `Union[Success, Failure]` at public APIs —
      raise typed `OSIError` instead.
- [ ] Every `assert` in `src/` is justified (only valid for "compiler-
      bug, cannot continue" cases; prefer
      `raise OSIError(ErrorCode.E_INTERNAL_INVARIANT, ...)`).

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
| `mypy --strict` | No `Any`, no untyped `dict`, frozen requires explicit `frozen=True` | `pyproject.toml [tool.mypy]` |
| `tests/properties/test_algebra_purity.py` | No mutation of inputs by any operator | source |
| `tests/properties/test_algebra_determinism.py` | Same `(model, query)` ⇒ identical `QueryPlan` | source |
| `tests/properties/test_error_taxonomy.py` | Failures raise typed `OSIError` (mutation-protected on the algebra) | source |
| `tests/unit/test_common_identifiers.py` | All identifier comparisons go through `normalize_identifier` | source |
| `tests/unit/test_synthetic_naming_invariants.py` | Synthetic names come from `prefixes.py` | source |
| `tests/unit/test_operator_enum_sync.py` | `PlanOperation` and operator dispatch stay in sync | source |
| Phase D `d1-osierror-arch` (added by this audit) | Every `Exception` subclass in `osi.*` is an `OSIError` | `tests/unit/test_every_exception_is_osierror.py` |

## 8. Example output format

```markdown
## C-001 `compile_plan` takes a `str` for dialect

- **Severity**: P1 (silent typo at user boundary)
- **Location**: `impl/python/src/osi/codegen/__init__.py:compile_plan`
- **Finding**: `compile_plan(plan, dialect: str = "ansi")` accepts any
  string. A typo (`"snowfalke"`) currently emits ANSI SQL silently.
- **Triage**:
  1. (Deterministic) — Change the signature to
     `dialect: Dialect = Dialect.ANSI`. mypy will reject every typo at
     call site. Lands in this PR.
  2. (Skill) — Add `compile_plan` to §5.2 example list.
  3. (Code) — Migrate one downstream call site that passes a raw
     string from a config file; queue the migration sprint.
- **Invariants touched**: ARCHITECTURE.md §6.11.
```

## 9. Anti-patterns

- "I'll accept `str` and convert internally" — the conversion is
  always lossy (case, whitespace, unknown values). Push the conversion
  to a typed factory at the boundary.
- "I'll return `None` if the lookup fails" — callers won't check.
  Raise typed `OSIError`.
- "It's just a flag" — every `bool` parameter is a future
  `if-elif-else` ladder that becomes a feature flag that becomes a
  config knob. Start with an enum.
- A constructor that "validates lazily" (i.e. invariants are checked
  in `__post_init__` only sometimes). Validate at construction or
  don't expose the constructor.
- `__init__` with optional arguments to switch construction modes
  (`Source(a=1)` vs `Source(b=2)`). Use named factories
  (`Source.from_a(...)`, `Source.from_b(...)`).

## See also

- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) §5 (algebra closure), §6 (invariants 1, 2, 11).
- [`interfaces-and-api-review/SKILL.md`](../interfaces-and-api-review/SKILL.md) — sister skill on facade hygiene and signature shape.
- [`typing-enforcement-review/SKILL.md`](../typing-enforcement-review/SKILL.md) — sister skill on mypy strictness, `NewType` migration catalog.
