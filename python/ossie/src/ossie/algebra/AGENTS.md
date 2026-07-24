<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# AGENTS.md — `ossie.algebra`

Rules for editing the **closed algebra** (this directory). The full contract —
the operator table, the state invariants, and how each rule is enforced — is in
[`ARCHITECTURE.md`](ARCHITECTURE.md); read it before changing an operator. The
normative semantics live in `core-spec/foundational_semantics.md` §6.

## Hard rules

1. **Compose the nine operators** — `source`, `filter_`, `enrich`, `aggregate`,
   `project` (`operations.py`); `add_columns`, `broadcast` (`composition.py`);
   `merge`, `filtering_join` (`joins.py`). Add capability by composing them.
   Adding a **tenth** operator is a reviewed spec change: a new allow-list entry
   in `../../../tests/test_algebra_closure.py`, a full set of property tests, and
   an `ARCHITECTURE.md` update.
2. **Never construct `CalculationState` (or `Column`) by hand** outside the
   operator modules `operations.py` / `joins.py` / `composition.py`. Callers get
   states by composing operators. Enforced by `test_algebra_closure.py`.
3. **Immutable · pure · total · deterministic.** State/columns are
   `frozen=True, slots=True`; operators return fresh values with no mutation,
   I/O, time, or randomness, and either return a valid state or raise.
4. **Typed errors only.** Raise `AlgebraError(ErrorCode..., ...)` from
   `ossie.errors` — never `assert`, a bare `Exception`, or a string. Use the
   `E4xxx` algebra-safety family for algebra-only failures; do **not** reuse the
   spec-reserved `E3011` (the engine-wide M:N opt-out).
5. **No upward imports.** Import only `ossie.common`, `ossie.errors`, the stdlib,
   and `sqlglot`. Never `ossie.models` / `parsing` / `planning` / `codegen`.
   Enforced by `import-linter`.

The algebra is **necessary, not sufficient** for the spec: the planner owns plan
shape — default join direction, chasm/stitch decomposition, and Semantic 4
beyond the local holistic guard. See `ARCHITECTURE.md` → "Scope".

## Verify (from `python/ossie`)

```bash
make test            # pytest: property + law + closure tests
make lint-imports    # import-linter closure contract
make typecheck       # mypy --strict on src
make mutation        # mutmut >= 90% over the algebra (slow; opt-in)
```
