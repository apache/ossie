# Implementation Docs

Deep-dive design notes and correctness arguments for `osi_python`. These
docs explain *how* the compiler upholds the Foundation spec and are
written for contributors touching any layer of the compiler. Read
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) first for the invariant catalog.

For the standard itself, see [`../specs/`](../specs/).

---

## Reference

| Doc | What it covers |
|:---|:---|
| [`ERROR_CODES.md`](ERROR_CODES.md) | Every `OSIError` code (`E1xxx`–`E4xxx`, `W5xxx`): meaning, range, typical cause, suggested remediation. |

## Correctness

| Doc | What it covers |
|:---|:---|
| [`ALGEBRA_LAWS.md`](ALGEBRA_LAWS.md) | The companion to [`../specs/JOIN_ALGEBRA.md`](../specs/JOIN_ALGEBRA.md): concrete Hypothesis strategies, property tests, and mutation-testing targets that enforce each algebra law. |
| [`JOIN_SAFETY.md`](JOIN_SAFETY.md) | Worked examples of fan-trap / chasm-trap detection and the safe rewrites the planner emits. |

## Testing

| Doc | What it covers |
|:---|:---|
| [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md) | The four-layer test pyramid (unit / property / golden / E2E), mutation-testing approach, and what each layer must prove. |

## Alignment

| Doc | What it covers |
|:---|:---|
| [`ERRATA_ALIGNMENT.md`](ERRATA_ALIGNMENT.md) | The 25 behaviors catalogued in Snowflake Semantic Views' ERRATA and how `osi_python` handles each: implemented, deferred, or resolved-away. Seeds test scenarios. |
| [`mapping_bi_models_to_core_osi_abstractions.md`](mapping_bi_models_to_core_osi_abstractions.md) | End-to-end mapping of Power BI / Tableau / Looker / ThoughtSpot concepts onto the Foundation. |
