# Deferred Proposals

These are existing OSI proposals that the Foundation ([`../Proposed_OSI_Semantics.md`](../Proposed_OSI_Semantics.md)) intentionally does **not** adopt. The normative deferred-features list lives in §10 of the Foundation spec; this folder is the design archive that lets us see the future shape of each feature.

They are preserved here as reference material for the design conversation
that will eventually layer them back on top of the Foundation. They are
**NOT** part of the `osi_python` standard, and the compiler MUST reject YAML
that uses any of the features described here with `E_DEFERRED_KEY_REJECTED`
(Appendix C of the Foundation spec / D-009).

---

## Why deferred

The Foundation exists to get consensus on a minimal, provably-correct core.
Every item below adds power but also adds design surface that is either
(a) still being debated, or (b) requires the Foundation as a foundation
before it can be specified cleanly.

When you see a feature described below and wonder "why didn't `osi_python`
do this?", the answer is always: we will, once the Foundation is stable.

---

## Catalog

### Grain and filter semantics (the biggest deferral)

| Doc | What it adds |
|:---|:---|
| [`OSI_Core_Abstractions.md`](OSI_Core_Abstractions.md) | Full OSI core spec, including `FIXED` / `INCLUDE` / `EXCLUDE` / `TABLE` grain modes, filter context propagation (`reset`, `filter.expression`), metric composition with grain inheritance, parameters with typed defaults. |
| [`OSI_Calc_Model_Semantics.md`](OSI_Calc_Model_Semantics.md) | The calculation-model semantics that underpin full grain handling: multi-stage accumulation, distributive / algebraic / holistic classification, explosion safety. |
| [`OSI_query_generation_algorithm.md`](OSI_query_generation_algorithm.md) | End-to-end algorithm for turning a full-spec semantic query into SQL. |
| [`OSI_Proposal_Resettable_Filters.md`](OSI_Proposal_Resettable_Filters.md) | Resettable filters — lexical reset semantics, scopes, propagation rules. |

### Joins beyond equijoin

| Doc | What it adds |
|:---|:---|
| [`OSI_Proposal_Non_Equijoins.md`](OSI_Proposal_Non_Equijoins.md) | `condition` + `cardinality` on relationships for non-equijoin joins (range, inequality). |
| [`OSI_Proposal_ASOF_and_Range_Joins.md`](OSI_Proposal_ASOF_and_Range_Joins.md) | Structured ASOF and Range join types for temporal patterns. |
| [`OSI_Proposal_Referential_Integrity.md`](OSI_Proposal_Referential_Integrity.md) | Full RI proposal. **Note:** the Foundation no longer adopts any RI surface — `referential_integrity`, `from_all_rows_match`, and `to_all_rows_match` are all deferred (§10). The Foundation's join defaults are §6.6 (`LEFT` for `N : 1` enrichment, `FULL OUTER` stitch for incompatible-root multi-fact queries); RI-driven `INNER` promotion is part of this proposal. |
| [`OSI_Proposal_Relationship_Enhancements.md`](OSI_Proposal_Relationship_Enhancements.md) | Pointer file — superseded by the two proposals above. |

### Extended SQL and operators

| Doc | What it adds |
|:---|:---|
| [`OSI_Proposal_Grouping_Sets.md`](OSI_Proposal_Grouping_Sets.md) | `GROUPING SETS` / `ROLLUP` / `CUBE` at the semantic layer. |
| [`OSI_Proposal_Pivot_Operator.md`](OSI_Proposal_Pivot_Operator.md) | `PIVOT` / `UNPIVOT` as a semantic operator. |
| [`OSI_Proposal_Semi_Additive.md`](OSI_Proposal_Semi_Additive.md) | Semi-additive measures over snapshot facts. |

### Window-function extensions deferred from the Foundation

Standard SQL window functions (ranking, navigation, aggregate-windows;
`ROWS` and `RANGE` frame modes; integer-literal frame bounds) are part
of the Foundation (§6.10 of `../Proposed_OSI_Semantics.md`). The
following window-related extensions are deferred and rejected with
`E_DEFERRED_KEY_REJECTED` / `E_DEFERRED_FRAME_MODE` /
`E_WINDOWED_METRIC_COMPOSITION`:

- Parameterized window frame bounds (`ROWS BETWEEN :n PRECEDING ...`).
- `GROUPS` frame mode.
- Ordered-set aggregates with `WITHIN GROUP (ORDER BY ...)` (e.g.
  `LISTAGG`, `PERCENTILE_CONT`).
- Windowed-metric composition (a metric that references another metric
  whose body contains an `OVER (...)` clause).

### Semi-join filter form

`EXISTS_IN` / `NOT EXISTS_IN` and any other semi-join filter form is
deferred from the Foundation. The Foundation's M:N resolution menu
(§6.8 of `../Proposed_OSI_Semantics.md`) is limited to bridge resolution
(§6.8.1) and shared-dimension stitch (§6.8.2). A separate proposal will
pin the semi-join surface (keyword, NULL-safety, `NOT`-form, and
compilation contract).

### Dataset-level filtering

| Doc | What it adds |
|:---|:---|
| [`OSI_Proposal_Dataset_Filters.md`](OSI_Proposal_Dataset_Filters.md) | Dataset-level filters with scope-based propagation. |

---

## Rule of thumb for contributors

If a PR is about implementing something that appears **only** in this
directory and not in the authoritative specs, stop and ask:

1. Is the Foundation stable and complete?
2. Is there a formal proposal to add this feature that has been accepted?

If either answer is "no", the PR belongs in a different sprint. Adding
speculative deferred-feature plumbing to the Foundation compiler defeats
the purpose of having a Foundation.
