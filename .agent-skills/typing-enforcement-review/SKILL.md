---
name: typing-enforcement-review
description: Review or design code so the type system carries as much of the architectural and BI/SQL contract as possible — frozen dataclasses, NewTypes for identifiers and codes, enums for closed sets, Protocols for cross-package contracts, no Any or dict[str, Any] at public boundaries, strict mypy settings. Use when adding new types, signatures, or any code that mypy will see; pairs with code-encourages-correct-use-review for the design-time aspect of "make illegal states unrepresentable."
---

# Typing enforcement review

The strongest deterministic check is the one mypy refuses to type-check.
This skill is the playbook for both reviewing typed code and designing
new types so the type system carries the architectural and BI/SQL
contract — `Identifier` for identifiers, `ErrorCode` for codes,
`Dialect` for dialects, `Protocol` for cross-package interfaces, and
`frozen=True` for everything that travels.

## 1. Purpose

Move as much of the architectural and BI/SQL contract as possible into
the type system. Every `Any` is a future bug; every `dict[str, Any]`
on a public boundary is a future bug; every raw `str` parameter that
should have been an `Identifier` / `Dialect` / `ErrorCode` is a future
bug. This skill finds them and converts them.

## 2. When to use it (Review)

Apply when the change:

- Adds, modifies, or removes a public type annotation.
- Introduces `Any`, `object`, `dict[str, Any]`, `list[Any]`, or
  `tuple[Any, ...]` anywhere in `src/`.
- Accepts a raw `str` for an identifier, dialect, error code, CTE name,
  or grain set.
- Adds a `# type: ignore[...]` comment.
- Adds a `cast(...)` call.
- Modifies `pyproject.toml [tool.mypy]` or adds a new mypy override.
- Adds a `Protocol`, `TypedDict`, or `NewType`.
- Touches `osi.common.identifiers`, `osi.common.types`, or any module
  that defines a cross-layer type.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Designing a new frozen dataclass that will travel between layers.
- Choosing between `str`, `NewType`, `Enum`, and `Literal[...]`.
- Designing a "value object" — an `Identifier`-shaped wrapper for a
  domain concept (`CTEName`, `ExpressionId`, `SourceLocation`).
- Considering a `dict[str, Any]` for a payload — almost always wrong;
  prefer `TypedDict` or a frozen dataclass.
- Defining a cross-package interface (e.g. an adapter contract) —
  prefer `Protocol`.
- Deciding mypy strictness for a new module.

## 4. Methodology

1. **Survey `Any` usage.** `rg "\bAny\b" src/` — every occurrence
   either has a justification next to it or is a bug. Most "framework"
   uses (sqlglot AST traversal, pydantic model dumps) belong inside a
   narrow shim, not on the public boundary.
2. **Survey raw `str` usage at boundaries.** Every public function
   that takes `str` for an identifier should take `Identifier`; for a
   code should take `ErrorCode`; for a dialect should take `Dialect`.
3. **Survey frozen-ness.** Every dataclass that travels between
   layers is `frozen=True`. `eq=True` is implied; `slots=True` is
   recommended.
4. **Survey `dict[str, Any]`.** A `dict[str, Any]` on a public
   boundary is a `TypedDict` or frozen dataclass waiting to happen.
5. **Survey ignores and casts.** Every `# type: ignore` and `cast(...)`
   is reviewed for necessity. Many can be removed by tightening the
   surrounding type; the ones that remain need a one-line comment.
6. **Survey mypy strictness.** New modules join `--strict`. New
   overrides in `pyproject.toml [tool.mypy.overrides]` need a reason
   (third-party stubs missing, test module convention, etc.).

## 5. Checklists

### 5.1 No untyped `Any` at boundaries

- [ ] No `Any` in a public function signature.
- [ ] No `Any` in a layer facade's re-exported type.
- [ ] No `Any` as the value type in a top-level `dict` exposed via
      public API.
- [ ] Internal `Any` is justified by a one-line comment (typically a
      sqlglot AST shim).

### 5.2 `NewType` discipline

- [ ] Identifiers travel as `Identifier`, produced by
      `normalize_identifier(s)`.
- [ ] CTE names travel as `CTEName`, produced by `prefixes.py`.
- [ ] Expression IDs travel as `ExpressionId`.
- [ ] Source locations travel as `SourceLocation`.
- [ ] If a new domain concept needs typed string-ish-ness, add a
      `NewType` in `osi.common.types` and a factory.

### 5.3 Enum discipline

- [ ] Closed sets are enums (`Dialect`, `PlanOperation`, `ErrorCode`,
      `JoinType`, `MetricShape`).
- [ ] Enum imports are by `Enum.MEMBER`, not by `.value` string.
- [ ] Exhaustive pattern matches on enums end with
      `case _: raise OSIError(ErrorCode.E_INTERNAL_INVARIANT, ...)`.

### 5.4 Frozen-by-default

- [ ] Every IR type (`CalculationState`, `Column`, `PlanStep`,
      `QueryPlan`, `PlanPayload` subclasses) has `frozen=True`.
- [ ] Every public model type (`SemanticModel`, `Dataset`, `Metric`,
      `Relationship`) is a frozen pydantic model or frozen dataclass.
- [ ] No `field(default_factory=list)` on a frozen IR type unless the
      list itself is intended to be re-bound (rare).

### 5.5 Protocols and TypedDicts

- [ ] Cross-package interfaces are `Protocol`s, not abstract base
      classes.
- [ ] Structured payloads exposed across packages are `TypedDict`s or
      frozen dataclasses.
- [ ] `dict[str, Any]` does not appear on any cross-package surface.

### 5.6 mypy hygiene

- [ ] No new `# type: ignore` without a comment explaining why.
- [ ] No new module excluded from strict mode without a roadmap entry.
- [ ] `disallow_any_generics`, `disallow_untyped_defs`,
      `warn_return_any` stay enabled.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — a stricter mypy setting, a
   new arch-test that bans `Any` in certain modules, a lint rule.
   Preferred. Never regresses; applies to every future change
   automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / `INFRA.md` and
   add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing code that
establishes a boundary or invariant, ask "can I add a deterministic
check that locks this in before the code lands?" If yes, write the
check in the same PR.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| `mypy --strict` | Most of the rules in §5 by construction | `pyproject.toml [tool.mypy]` |
| `mypy [warn_unreachable]` | Dead code in conditionals | `pyproject.toml` |
| `mypy [warn_redundant_casts]` | `cast(...)` is necessary | `pyproject.toml` |
| `mypy [disallow_any_generics]` | No bare `list` / `dict` / `tuple` | `pyproject.toml` |
| `mypy [disallow_untyped_defs]` | All defs in `src/` are typed | `pyproject.toml` |
| `mypy [strict_equality]` | No `int == str`-style silent comparisons | `pyproject.toml` |
| `tests/unit/test_common_identifiers.py` | Identifier construction goes through `normalize_identifier` | source |
| `tests/unit/test_operator_enum_sync.py` | Enum-driven dispatch is exhaustive | source |
| Phase D `d1-osierror-arch` (added) | Every `Exception` subclass in `osi.*` is `OSIError` | `tests/unit/test_every_exception_is_osierror.py` |

## 8. Example output format

```markdown
## T-001 `Planner.plan` takes a raw `str` dialect

- **Severity**: P1 (typo at user boundary becomes silent fallback)
- **Location**: `impl/python/src/osi/planning/planner.py:412`
- **Finding**: `Planner.plan(query, dialect: str = "ansi")` accepts
  any string. A typo (`"snowfalke"`) currently silently falls back to
  ANSI.
- **Triage**:
  1. (Deterministic) — Replace `str` with `Dialect = Dialect.ANSI`.
     mypy now rejects every typo at the call site. Lands in this PR.
  2. (Skill) — Add `Planner.plan` to §5.2 example list.
  3. (Code) — Migrate downstream config-driven call sites; queue.
```

## 9. Anti-patterns

- A new `Any` to "make this generic." Generic the right way (`T`
  bound to a `Protocol`, or `TypeVar` with concrete uses) keeps the
  type information.
- `dict[str, Any]` as a payload type for a new feature. Either it's a
  `TypedDict` (when the shape is open-ended) or a frozen dataclass
  (when the shape is fixed); never `Any`.
- A `str` parameter for an identifier "to keep the API ergonomic." The
  factory function (`normalize_identifier`) keeps it ergonomic; the
  type makes it correct.
- A `cast(...)` to satisfy mypy without justifying why the cast is
  safe. Every cast is a TODO; either remove it or comment it.
- A new module excluded from `--strict` "for now." Strict-from-day-one
  is the policy (`INFRA.md [I-DEC-7]`).

## See also

- [`../../impl/python/INFRA.md`](../../impl/python/INFRA.md) [I-DEC-7] — strict mypy from day one.
- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) §6.11 — identifier safety.
- [`code-encourages-correct-use-review/SKILL.md`](../code-encourages-correct-use-review/SKILL.md) — design-time sister skill on "make illegal states unrepresentable."
- [`interfaces-and-api-review/SKILL.md`](../interfaces-and-api-review/SKILL.md) — sister skill on facade hygiene and signature shape.
