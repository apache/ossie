# Specs

This folder holds the **authoritative semantic standard** that `osi_python`
implements. Everything in `src/osi/` must conform to what is written here;
anything not written here is not part of the standard and is out of scope.

The standard is deliberately narrower than the full OSI body of work. It is
a **Foundation** designed to be implementable, testable, and easy to reach
consensus on. Deferred proposals are preserved under [`deferred/`](deferred/)
for reference — they are NOT in scope for this implementation.

---

## Authoritative specs (in scope)

Read in this order when onboarding.

| # | Doc | What it covers |
|:--:|:---|:---|
| 1 | [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md) | **The Foundation (`osi_version: "0.1"`).** Semantic model, query model, two query shapes (`Aggregation` / `Scalar`), join semantics, M:N resolution, window functions in scope, SQL subset, compliance levels, alignment with Snowflake / Databricks / Looker, plus the normative Conformance Decisions (Appendix B, `D-001` … `D-033`) and Error Code Index (Appendix C). This is the top-level contract. |
| 2 | [`OSI_core_file_format.md`](OSI_core_file_format.md) | YAML file format for semantic models. Used by `osi.parsing` as the schema source. |
| 3 | [`SQL_EXPRESSION_SUBSET.md`](SQL_EXPRESSION_SUBSET.md) | The SQL subset allowed inside metric / field / filter / where / having expressions. |
| 4 | [`JOIN_ALGEBRA.md`](JOIN_ALGEBRA.md) | **The closed algebra.** Formal operations, state invariants, and laws — the proof surface the compiler uses to guarantee correctness. |
| 5 | [`SQL_INTERFACE.md`](SQL_INTERFACE.md) | **The SQL surface.** `SEMANTIC_VIEW(...)` clause grammar, bare-view SQL, reference resolution, error taxonomy, and the alignment/divergence map against Snowflake Semantic Views. |
| 6 | [`SQL_Caller_Examples.md`](SQL_Caller_Examples.md) | Worked examples from the perspective of a caller issuing semantic queries. |

When a conflict arises between two authoritative specs, the order above is
the tie-breaker: `Proposed_OSI_Semantics.md` > `OSI_core_file_format.md` >
`SQL_EXPRESSION_SUBSET.md` > `JOIN_ALGEBRA.md` > `SQL_INTERFACE.md` >
`SQL_Caller_Examples.md`.

## Vendor alignment catalogs (non-normative)

These are reference catalogs that document intentional Foundation design
divergences from specific vendors. They are non-normative — they record
*why* the Foundation chose a particular rule when a vendor handles the
same situation differently — and they cross-reference the normative spec
sections that pin the rule.

| Doc | Vendor | What it tracks |
|:---|:---|:---|
| [`SNOWFLAKE_DIVERGENCES.md`](SNOWFLAKE_DIVERGENCES.md) | Snowflake Semantic Views | `SD-NNN` entries for stable design divergences (cross-grain nesting, window NULL ordering, `QUALIFY`, frame modes, etc.). Snowflake **bugs** the Foundation resolves are in `Proposed_OSI_Semantics.md §12.A.2` and `docs/ERRATA_ALIGNMENT.md` instead. |

## Proposed extensions (out of scope for the Foundation, but actively drafted)

These are additive proposals layered on top of the Foundation. Each is
self-contained, names the Foundation contracts it interacts with, and
catalogues the conformance decisions it would add. They are not adopted
yet — Foundation engines MUST reject models that use any of these
features per the Foundation's deferred-key contract (D-009).

| Proposal | What it adds | Status |
|:---|:---|:---|
| [`Proposed_OSI_Natural_Grain.md`](Proposed_OSI_Natural_Grain.md) | Optional top-level `natural_grain:` declaration that pins one dataset as the implicit anchor for every query against the model (Tableau-extract-style / Looker-fact-rooted-explore-style behaviour). | Drafted; not adopted. Reserves the `natural_grain` key. |

## Deferred proposals (out of scope)

Under [`deferred/`](deferred/). These are the existing OSI proposals that
the Foundation intentionally does **not** adopt. The full catalog of
deferred features is the normative §10 of `Proposed_OSI_Semantics.md`;
this folder is the design archive. Each item is an additive layer that
can be designed once the Foundation is implemented and ratified. The
implementation MUST reject models that rely on any deferred feature
with `E_DEFERRED_KEY_REJECTED` per Appendix C / D-009.

See [`deferred/README.md`](deferred/README.md) for the full catalog.

## Where the implementation lives

- `src/osi/` — implementation (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the pipeline)
- `docs/` — deep-dive design notes (algebra laws, testing strategy, error catalog)
- `tests/` — unit, property-based, golden, E2E
