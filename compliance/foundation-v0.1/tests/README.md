# Foundation v0.1 compliance tests

Each test is a folder containing:

| File | Purpose |
|:---|:---|
| `metadata.yaml` | `id: T-NNN`, `decision: D-NNN`, `dataset: f_*`, `spec_refs`, `required_features`, `expected_error_code` (negative cases), `xfail_reason` (if pinned to a sprint). |
| `model.yaml` | The semantic model the test runs against. |
| `query.json` | The semantic query, in the new two-shape format (`dimensions` + `measures` for aggregation queries; `fields` for scalar queries). |
| `gold.sql` | Hand-written reference SQL the harness executes against the fixture data to produce the expected row multiset. Positive cases only; negative cases keep a stub `gold.sql` so the harness can locate the directory. |

Tests assert on observable behaviour only. The harness executes both
`gold.sql` and the adapter's emitted SQL against the shared fixture
and compares the resulting row multisets — order-insensitive unless
the query has an `Order By`. SQL strings are never compared
byte-for-byte (per D-014 that's a per-engine concern).

## Layout

| Area | What it covers | Anchor decisions |
|:---|:---|:---|
| `query_shape/` | Aggregation vs Scalar shape, mixing rejection, COUNT(*), expression-form dialects | D-001, D-010, D-002, D-016, D-021 |
| `scalar_query/` | Scalar query specifics: bare metric in Fields, fan-out rejection | D-011, D-023 |
| `field_metric_grain/` | Field expressions with implicit home-grain aggregation | D-003, D-015 |
| `cross_grain/` | Single-step cross-grain aggregates (1:N) | D-020, D-024 |
| `nested_aggregates/` | Nested aggregates over M:N (inner-grain inference) | D-022, D-027 |
| `bridge/` | M:N bridge resolution + de-duplication (Semantic 2) | D-026 (T-015 flagship) |
| `joins_default/` | LEFT for N:1, FULL OUTER stitch, CROSS JOIN of scalars | D-001, D-004, D-008 |
| `predicate_routing/` | Where vs Having shape errors | D-005, D-012 |
| `namespace/` | Global / dataset-scoped resolution, ambiguous / no path, reserved names | D-006, D-018, D-019 |
| `windows/` | Window placement, pre-fan-out, deferred frame modes, composition | D-028, D-030, D-031, D-032 |
| `null_ordering/` | NULLS LAST default emission, per-engine determinism witnesses | D-014, D-029 |
| `empty_inputs/` | Standard SQL empty / NULL aggregate behaviour | D-033 |
| `deferred/` | One negative test per `E_DEFERRED_KEY_REJECTED` family | D-009 |
| `validation_errors/` | Structural validation errors that are NOT deferred features (e.g. `E_FIELD_DEPENDENCY_CYCLE` — fields must form a DAG). | Appendix C |
| `error_taxonomy/` | Remaining Appendix C codes (E_PRIMARY_KEY_REQUIRED, E_AMBIGUOUS_MEASURE_GRAIN, etc.) | Appendix C |

## Sprint timeline

- **S-B**: this scaffold + `tests/README.md` (no test cases yet).
- **S-C**: lands `T-001 … T-033` plus the `E_DEFERRED_KEY_REJECTED`
  negative tests, derived from `DATA_TESTS.md §4`.
- **S-E**: lands additional `T-NNN` cases from the differential /
  edge-case audit before any S-1..S-17 sprint runs.

## Negative tests

A test is negative when its `metadata.yaml` carries
`expected_error_code: E_<NAME>` (no `gold_rows.json`). The runner
asserts that the adapter exited non-zero AND that stderr contains the
named error code. Negative tests do NOT carry `required_features:`
unless the rejection itself depends on a Foundation-level feature; the
goal is for every adapter (Foundation or extended) to produce the same
error.

## xfail policy

A case marked `xfail_reason: <text>` is shipped but expected to fail
until the named sprint flips it to `must_pass`. Every `xfail_reason`
MUST cite the sprint ID (e.g., `xfail_reason: "Sprint S-7 — default
join shape rewrite (D-004)"`) so the rollout can find every red row
when the implementation lands.
