# CONTRIBUTING.md

Thanks for your interest in the OSI Python reference implementation.
This guide is the short, opinionated contributor handbook. For the
full contract, read [`SPEC.md`](SPEC.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md), and [`INFRA.md`](INFRA.md).

---

## 1. Mindset

The project has three commitments that every contribution must honor.

1. **The Foundation stays thin.** If a feature is deferred in
   `Proposed_OSI_Semantics.md §10`, it's out of scope. Propose an
   expansion of the standard before writing code.
2. **The algebra is load-bearing.** Every compiler transformation
   composes operators from
   [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md).
   No bypasses.
3. **Correctness is proved by tests, not wished.** Every feature ships
   with unit + property + golden + E2E tests, and the mutation-testing
   budget ([`INFRA.md §1.1`](INFRA.md)) is not a guideline — it's a gate.

---

## 2. Setting up

```bash
cd impl/python
make install-dev       # creates .venv, installs deps + pre-commit hooks
source .venv/bin/activate
make check             # sanity check; should be green on main
```

Always use the project-local venv inside `impl/python/`.

---

## 3. Making changes

### 3.1 Before coding

- Identify which layer(s) your change touches (parsing / planning /
  codegen / diagnostics).
- Read the relevant `README.md` under `src/osi/<layer>/`.
- Check [`ARCHITECTURE.md §8`](ARCHITECTURE.md) for where-to-add guidance.
- Check [`INFRA.md §3`](INFRA.md) for an in-progress infra item that
  might conflict.

### 3.2 While coding

- Keep the file you are editing under 600 LOC. If your change pushes it
  over, split first in a separate PR.
- Every public function / class gets a one-sentence docstring.
- Every new `ErrorCode` gets an enum value, a catalog row in
  [`docs/ERROR_CODES.md`](docs/ERROR_CODES.md), and at least one unit
  test.
- Every algebra-touching change gets a property test update. If no law
  is affected, explain in the PR why.

### 3.3 Before submitting

```bash
make format       # black + isort
make check        # lint + mypy + tests
make mutation-fast  # algebra mutation; must not regress > 2 pp
```

If you changed a golden file, explain the intent in the PR description:
"Plan now emits a `PROJECT` step after the final merge because <reason>;
I've refreshed `tests/golden/basic/single_table_revenue/expected.plan.json`
to match."

---

## 4. Pull request checklist

Use this as the PR description template:

```
## Summary
<1–2 sentences — what does this change and why>

## Changes
- <file / module>: <what changed>

## Tests
- [ ] Unit tests added / updated
- [ ] Property tests added / updated (if algebra touched)
- [ ] Golden tests refreshed (if plan/SQL shape changed — justify in summary)
- [ ] E2E tests added / updated (if user-visible behavior changed)

## Quality gates
- [ ] `make check` passes locally
- [ ] `make mutation-fast` — no regression > 2 pp
- [ ] Coverage did not drop below `INFRA.md §1.1` minimums

## Invariants touched (see ARCHITECTURE.md §6)
- <invariant number + one-line justification>
```

---

## 5. Review bar

Reviewers will push back on:

- Raw-string SQL anywhere in `src/`.
- Bare `except Exception` in `src/` (catch `OSIError` subclasses).
- Adding a second way to do a thing when one already exists.
- Deferred-feature plumbing.
- Tests that pass by skipping or by narrowing the generation strategy.
- Files > 600 LOC.
- New `ErrorCode` without a test.
- `@pytest.skip` without a platform-specific reason.

---

## 6. Reporting issues

When filing a bug, include:

- The minimal semantic model YAML.
- The semantic query (Python or JSON).
- The expected SQL / rows.
- The actual SQL / rows or the error (with `error.code`).
- The dialect.

For algebra bugs specifically: include the minimal `CalculationState`
that reproduces the issue, if you can. Property-test counterexamples
qualify — paste the seed and the shrunk input.

---

## 7. Where conversation happens

- Spec conversations → GitHub issues tagged `spec`.
- Infra / tooling → GitHub issues tagged `infra`.
- Implementation discussion → PR comments.
- Breaking Foundation scope (adding a deferred feature) → a proposal PR
  against `specs/Proposed_OSI_Semantics.md` before the implementation PR.

---

## 8. Proposal ratification lifecycle

The Foundation is a *subset* of the full OSI standard. New semantic
concerns — filter context, grain modes, window functions, semi-joins,
parameters, etc. — are deferred in `Proposed_OSI_Semantics.md §10` until
they have been **ratified, implemented, and conformance-tested**.

A proposal moves through five stages. Nothing ships without all five.

### Stage 1 — Draft (in `proposals/foundation-v0.1/Proposed_OSI_Semantics.md`)

Open a PR that appends (or amends) a section in
[`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
with:

- A short rationale (what BI idiom does this enable? Which tools
  already offer it?).
- The *semantic* contract (operator shape, grain behaviour, null
  semantics, error conditions).
- A pointer to at least one real-world source (LookML / DAX / Tableau /
  dbt / Malloy snippet).
- The proposal **ID** you intend to register. Use snake_case, e.g.
  `filter_context`, `grain_modes`, `semi_join`, `param_named_filter`.

Stage 1 PRs change docs only. They do **not** touch `src/` or
`tests/golden/`.

### Stage 2 — Register the ID in `proposals.yaml`

In the same PR (or an immediate follow-up), add the proposal to
[`../../compliance/foundation-v0.1/proposals.yaml`](../../compliance/foundation-v0.1/proposals.yaml)
with `status: proposed`:

```yaml
- id: filter_context
  status: proposed
  description: Per-metric filter context overrides (CALCULATE-like).
  spec_refs:
    - "proposals/foundation-v0.1/Proposed_OSI_Semantics.md#filter-context"
```

The CI check (`harness/proposals_check.py`) now allows test metadata
to cite this ID under `required_features`.

### Stage 3 — Add conformance tests under the new ID

Tests that exercise the feature go anywhere in
[`../../compliance/foundation-v0.1/tests/`](../../compliance/foundation-v0.1/tests/)
and **must** include `required_features: [<proposal_id>]` in their
`metadata.yaml`. This keeps them skipped by every adapter that has not
opted in.

Every proposal needs at least one positive test (happy path) *and* one
negative test (confirms the rejection error when the proposal is
disabled — typically `E_DEFERRED_KEY_REJECTED`).

### Stage 4 — Implement behind the proposal flag

1. Move the spec text from
   `../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md` into the
   relevant main spec section in `SPEC.md`.
2. Build the feature in `src/osi/<layer>/`. Delete the rejection that
   previously raised `E_DEFERRED_KEY_REJECTED` for the now-allowed
   construct.
3. Add golden plan + golden SQL + E2E tests under `tests/`.
4. Update `docs/ERROR_CODES.md` for any new error codes.
5. Flip the `proposals.yaml` entry from `status: proposed` to
   `status: foundation` (or a new named slice level once we grow one).

### Stage 5 — Opt the adapter in

Finally, add the proposal ID to
[`conformance/enabled_proposals.yaml`](conformance/enabled_proposals.yaml):

```yaml
enabled:
  - filter_context
```

and run the full suite via `make conformance` to confirm the new
positive tests pass and the legacy `E1105` negative tests now *fail*
as expected — because they're only valid when the feature is off. Tag
those negative tests with an `excluded_when_feature` marker (or simply
retire them) as part of Stage 4.

### Required PR checklist for each stage

| Stage | `specs/` | `proposals.yaml` | Suite tests | `src/` | `enabled_proposals.yaml` | CI gates |
|-------|----------|-------------------|-------------|--------|--------------------------|-----------|
| 1 — Draft           | ✅ | — | — | — | — | `make check` docs only |
| 2 — Register        | —  | ✅ (`proposed`) | — | — | — | `proposals_check.py` |
| 3 — Tests           | —  | — | ✅ (tagged) | — | — | `make conformance-all` |
| 4 — Implement       | ✅ (promote) | ✅ (`foundation`) | ✅ (goldens + E2E) | ✅ | — | `make check` + `make mutation-fast` |
| 5 — Adapter opt-in  | — | — | — | — | ✅ | `make conformance` |

Skipping a stage is grounds for a reviewer to block the PR. The
proposal ID is the thread that stitches all five stages together — it
appears in the spec doc, the registry, every test's
`required_features`, the adapter manifest, and every PR description
that touches the feature.
