# Specs

This folder is the **design archive** for the OSI Python reference
implementation. It previously held deferred-feature proposal files, which
have since been removed. The authoritative deferred-features list is
in `Proposed_OSI_Semantics.md §10`.

The **authoritative semantic standard** that `osi_python` implements lives
under [`../../../proposals/foundation-v0.1/`](../../../proposals/foundation-v0.1/),
not here. Anything in this folder is non-normative.

---

## Authoritative specs (in scope) — live in `proposals/foundation-v0.1/`

Read in this order when onboarding:

| # | Doc | What it covers |
|:--:|:---|:---|
| 1 | [`Proposed_OSI_Semantics.md`](../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) | **The Foundation (`osi_version: "0.1"`).** Semantic model, query model, two query shapes (`Aggregation` / `Scalar`), join semantics, M:N resolution, window functions in scope, SQL subset, compliance levels, vendor alignment summary, plus the normative Conformance Decisions (Appendix B, `D-001` … `D-033`) and Error Code Index (Appendix C). This is the top-level contract. |
| 2 | [`SQL_EXPRESSION_SUBSET.md`](../../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md) | The SQL subset allowed inside metric / field / filter / where / having expressions (OSI_SQL_2026 dialect grammar). |

When a conflict arises between the two authoritative specs,
`Proposed_OSI_Semantics.md` wins.

## Implementation-side supporting docs

Non-normative reference material maintained alongside the implementation:

| Doc | Where | What it covers |
|:---|:---|:---|
| [`JOIN_ALGEBRA.md`](../docs/JOIN_ALGEBRA.md) | `impl/python/docs/` | **The closed algebra.** Formal operations, state invariants, and laws — the proof surface the compiler uses to guarantee correctness. |
| [`ERRATA_ALIGNMENT.md`](../docs/ERRATA_ALIGNMENT.md) | `impl/python/docs/` | Catalog of vendor errata the Foundation resolves and how the impl honors each resolution. |
| [`ERROR_CODES.md`](../docs/ERROR_CODES.md) | `impl/python/docs/` | The error code catalog and its mapping to Appendix C. |
| [`ALGEBRA_LAWS.md`](../docs/ALGEBRA_LAWS.md) | `impl/python/docs/` | Hypothesis strategies and property tests that enforce each algebra law. |

## Future proposals (not yet drafted as separate documents)

These are deferred features summarised in §10 of `Proposed_OSI_Semantics.md`.
Each will land as its own follow-up proposal in
[`../../../proposals/`](../../../proposals/) when adopted:

- `natural_grain` (top-level dataset pin)
- `SQL_INTERFACE` (`SEMANTIC_VIEW(...)` clause grammar + bare-view SQL)
- Filter context propagation, metric composition, LOD grain modes, etc.

## Deferred features

The individual deferred-feature spec files have been removed from this
directory. The authoritative catalog of deferred features lives in
[`Proposed_OSI_Semantics.md §10`](../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md).

The implementation rejects any model that uses a deferred feature with
`E_DEFERRED_KEY_REJECTED` per Appendix C / D-009.

## Where the implementation lives

- [`../src/osi/`](../src/osi/) — implementation (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the pipeline)
- [`../docs/`](../docs/) — deep-dive design notes (algebra laws, testing strategy, error catalog)
- [`../tests/`](../tests/) — unit, property-based, golden, E2E
