# Property-based tests

Hypothesis-driven tests that enforce the universal laws of the algebra
stated in [`../../specs/JOIN_ALGEBRA.md §4`](../../specs/JOIN_ALGEBRA.md#4-laws).

See [`../../docs/ALGEBRA_LAWS.md`](../../docs/ALGEBRA_LAWS.md) for the
complete mapping: law → property statement → test file → mutation
target.

**Load-bearing.** Mutation testing on `src/osi/planning/algebra/`
targets ≥ 90% score (see [`../../INFRA.md §1.1.1`](../../INFRA.md)); the
property tests in this directory are what drives that score.

## Layout

- `strategies.py` — Hypothesis strategies for identifiers, schemas,
  states, operator chains, DuckDB fixtures.
- `reference.py` — naive pandas reference interpreter for equivalence
  laws (§4.9, §4.10, §4.11 of JOIN_ALGEBRA.md).
- `test_algebra_*.py` — one test file per law.

## Rule of thumb

A property test that can be made to pass by narrowing the strategy is
not a property test; it's an example. Narrow only when the original
property is genuinely wrong as stated — and if it is, fix
`specs/JOIN_ALGEBRA.md` first, then the test.
