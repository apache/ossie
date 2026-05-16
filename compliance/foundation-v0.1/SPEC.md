# OSI Compliance Test Suite — Spec Coverage

**Targets:** Foundation `osi_version: "0.1"`. Authoritative spec
documents:

- [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  — the semantic, query, join, M:N, and window contracts; Appendix B
  (`D-001` … `D-033`) and Appendix C (error code index) are the
  specific anchors this suite exercises.
- [`../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)
  — the `OSI_SQL_2026` default expression dialect.
- [`../../proposals/foundation-v0.1/DATA_TESTS.md`](../../proposals/foundation-v0.1/DATA_TESTS.md)
  — the normative test catalog (`T-001` … `T-NNN`) this suite encodes
  as runnable cases.

This suite is the runnable witness layer for the spec. Every
conformance decision in Appendix B has at least one `T-NNN` test here;
every error code in Appendix C has at least one negative test here.

## What this suite does NOT cover

- OSI features outside the Foundation surface (LOD grain modes, filter
  context, named filters, semi-join filter form, per-metric
  `joins.{type, using_relationships}`, non-equijoins, etc.). These
  features are deferred per §10 of the Foundation proposal and are
  exercised here only as negative cases that expect
  `E_DEFERRED_KEY_REJECTED` (D-009). Future per-version compliance
  suites under `compliance/` will cover them positively when their
  proposals land.
- Engine-internal plan shape (CTE count, join order, alias names,
  generated SQL string). The Foundation defines per-engine determinism
  only (D-014); cross-engine SQL determinism is not required, so tests
  assert on rows or `error.code` only.
- Performance characteristics. The suite is a correctness signal; any
  performance regression checks live in the implementation's own
  benchmark harness.

## Compliance levels

Defined in [`conformance.yaml`](conformance.yaml):

| Level | Description |
|:---|:---|
| **`foundation_v0_1`** | Required for every Foundation-claiming engine. Every D-NNN in Appendix B must produce the expected outcome. |
| **`foundation_v0_1_strict`** | Adds determinism witnesses (D-014, D-029) — same `(model, query, dialect)` produces byte-identical SQL across runs. Optional. |

Cross-engine portability is observable behaviour (rows / error codes),
not SQL text. `foundation_v0_1_strict` is per-engine determinism.

## Decision coverage

The mapping `D-NNN ↔ T-NNN` lives in [`decisions.yaml`](decisions.yaml).
The runner emits `results/decisions_coverage.md` after every run so
gaps are visible in PR review.

## Adapter contract

Adapters live under [`adapters/`](adapters/). The CLI contract is the
one published at
[`../ADAPTER_INTERFACE.md`](../ADAPTER_INTERFACE.md):

```
<adapter> sql --model <model.yaml> --query-file <query.json> --dialect <dialect>
```

stdout = generated SQL; stderr = `<ERROR_CODE>: <message>` on
failure; exit code = 0 on success, non-zero on error. The bundled
`osi_python_adapter.py` delegates to
[`../../impl/python/conformance/adapter.py`](../../impl/python/conformance/adapter.py)
so the existing CLI contract stays the source of truth — this suite
adds no new translation logic.
