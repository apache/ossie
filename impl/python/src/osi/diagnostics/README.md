# `osi.diagnostics` — read-only introspection over model and plan

The diagnostics layer is the answer to *"what just happened?"* It
projects an already-parsed `SemanticModel` or an already-built
`QueryPlan` into human-readable text or JSON. It is intentionally
side-effect-free, model- and plan-only — it does not parse, plan, or
generate SQL, and it never touches the physical data the model
describes.

Diagnostics matter twice as much in a reference implementation:
every conformance test, every spec ambiguity, and every "why did
this query route this way?" question is answered through these
entry points. If you change a planner decision, the corresponding
diagnostic must move in lockstep.

## Modules

| Module | Entry point | Purpose |
| --- | --- | --- |
| `describe.py` | `describe(model)` / `describe_json(model)` | Render a `SemanticModel` as a grouped, table-like summary — datasets, fields, metrics, parameters, relationships, and dialect. Audiences: humans browsing a YAML model; CI checks that a parsed model has the expected shape. |
| `explain.py` | `explain(plan)` / `explain_json(plan)` | Render a `QueryPlan` as a per-step trace: alias, operation, inputs, grain, column list, and a one-line summary of the operator payload. Aliases match the CTE names that codegen emits, so trace lines line up with the generated SQL. |
| `resolve.py` | `resolve(query, context)` / `resolve_json(query, context)` | Show which datasets, fields, metrics, and relationships will be touched by a `SemanticQuery` against a `PlannerContext` — *without* running the planner. Lets users (and CI) confirm that the relationship-graph picks the path they expected. |
| `error_catalog.py` | `explain_error(code)` / `all_explanations()` | The prose explanation table for every `ErrorCode`. The test under `tests/unit/diagnostics/test_error_catalog.py` enforces that every enum member has a non-empty entry. |

## Invariants

1. **Read-only.** Diagnostics never mutate their inputs. Inputs are
   frozen dataclasses; outputs are new strings / dicts.
2. **No re-planning.** `resolve` and `explain` describe what the
   parser / planner produced, not what they could produce — they
   never re-run the planner.
3. **Every failure carries a code.** Diagnostics raise
   `OSIError(E_INTERNAL_INVARIANT, …)` (never `TypeError` /
   `ValueError`) when the IR is out of sync, so the
   tests/properties/test_error_taxonomy.py invariant still holds
   here. The Phase 3 review I3 finding pinned this rule.
4. **Exhaustive over IR variants.** `explain._payload_summary` must
   have a case for every concrete `PlanPayload` subclass. The
   exhaustive test in `tests/unit/diagnostics/test_explain.py`
   guards this.

## When to add a new entry point

Add a new diagnostic when:

* the planner gains a new piece of state worth surfacing (a new
  payload kind, a new resolution rule) — extend `explain` /
  `resolve` first;
* a recurrent debugging workflow needs more than five lines of
  ad-hoc Python — promote it from a notebook into a module here.

Never reach across to `osi.codegen` from a diagnostic — diagnostics
describe the plan; codegen renders it. Mixing the two muddies the
phase boundaries in `ARCHITECTURE.md §1.1`.
