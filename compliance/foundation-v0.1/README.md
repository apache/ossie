# OSI Compliance Test Suite — Foundation (`osi_version: "0.1"`)

**Targets only the Foundation proposal at
[`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
(`osi_version: "0.1"`) and its companion
[`SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)
(`OSI_SQL_2026` default dialect).**

The Foundation is the deliberately narrow first-cut of OSI semantics:
two query shapes, implicit home-grain aggregation, window functions,
and a small deferred-features list — pinned by 33 numbered Conformance
Decisions (D-001 … D-033) in Appendix B of the Foundation spec, and
the error code index in Appendix C.

Features outside the Foundation surface (LOD grain modes, filter
context, named filters, semi-join filter form, per-metric joins,
non-equijoins, etc.) are listed in §10 of the proposal and exercised
here **only** as negative tests that expect
`E_DEFERRED_KEY_REJECTED` (D-009).

The runner harness lives at [`../harness/`](../harness/) and is
shared with every future per-version suite under `compliance/`.

## Layout

```
compliance/foundation-v0.1/
  README.md                     # this file
  SPEC.md                       # the specs this suite targets (Foundation v0.1)
  pyproject.toml                # editable install; depends on ../harness (osi_compliance_harness)
  conformance.yaml              # test conformance levels; "foundation_v0_1" = required
  proposals.yaml                # mirrors §10 of the Foundation spec; every deferred is a proposal
  decisions.yaml                # D-001 … D-033 with anchor + status (must_pass | xfail)
  adapters/
    osi_python_adapter.py       # delegates to impl/python/conformance/adapter.py
  datasets/
    f_prelude/                  # mirrors DATA_TESTS.md §3.1
    f_bridge/                   # §3.2 — actor↔movie M:N
    f_bridge_none/              # §3.3 — M:N with no bridge, no shared dim
    f_ambig/                    # §3.4 — two paths between the same datasets
    f_nopath/                   # §3.5 — disconnected datasets
  tests/
    query_shape/                # T-001..T-003 (D-001, D-010, D-011)
    scalar_query/               # T-002, T-012, T-023 — Fields + scalar grain
    field_metric_grain/         # T-004..T-005x (D-003, D-015)
    cross_grain/                # T-005, T-020, T-024 (D-020, D-024)
    nested_aggregates/          # T-027 (D-022, D-027, §4.5.1)
    bridge/                     # T-015 (D-026 flagship — actor↔movie)
    joins_default/              # T-006, T-011, T-045, T-047, T-053 (D-001, D-004)
    predicate_routing/          # D-005, D-012 — Where vs Having shape errors
    namespace/                  # D-006, D-018, D-019
    windows/                    # D-028..D-032
    null_ordering/              # D-029
    empty_inputs/               # D-033
    deferred/                   # one negative test per E_DEFERRED_KEY_REJECTED case
    error_taxonomy/             # negative cases for the rest of Appendix C
  results/                      # gitignored
```

## Quick start

```bash
# From the repo root
pip install -e compliance/harness
pip install -e compliance/foundation-v0.1
pip install -e impl/python

cd compliance/foundation-v0.1

# Run the full suite against the bundled osi_python adapter
python -m harness.runner \
  --adapter adapters/osi_python_adapter.py \
  --tests tests/ \
  --datasets datasets/

# Run a single area
python -m harness.runner \
  --adapter adapters/osi_python_adapter.py \
  --tests tests/bridge/ \
  --datasets datasets/

# List discovered tests without running
python -m harness.runner --list --tests tests/
```

The harness ships under [`../harness/`](../harness/) and is installed
as the `osi_compliance_harness` package — every per-version suite under
`compliance/` shares this one runner / reporter / DB manager.

## Per-test layout

Each test is a folder containing:

| File | Purpose |
|:---|:---|
| `metadata.yaml` | `id: T-NNN`, `decision: D-NNN`, `spec_refs`, `required_features`, `expected_error_code`, `xfail_reason` (if applicable). |
| `model.yaml` | The semantic model (typically a thin wrapper around a fixture from `datasets/f_*`). |
| `query.json` | The semantic query, in the new two-shape format (`Aggregation` clauses or `Fields` for scalar). |
| `gold_rows.json` | The expected row set, in the column order of the query's projection. *Not authored*: `gold.sql` — the Foundation tests row sets only, not SQL strings (D-014 is per-engine, not cross-engine). |

Tests assert on observable behaviour only:

- `expected_error_code: E_<NAME>` ⇒ adapter must surface that code in stderr.
- `gold_rows.json` ⇒ adapter must emit SQL whose execution against the
  fixture data returns this exact row multiset (order-insensitive
  unless the query has an `Order By`).

## Decision coverage

Every `D-NNN` row in Appendix B of the Foundation spec MUST have at
least one runnable case here. The mapping lives in `decisions.yaml`,
and the runner emits `results/decisions_coverage.md` after every run.

## See also

- `SPEC.md` — what the suite targets and why.
- `decisions.yaml` — D-001..D-033 status board.
- `proposals.yaml` — §10 deferred-features registry.
- `../../impl/python/conformance/adapter.py` — the upstream adapter our
  adapter delegates to.
- `../ADAPTER_INTERFACE.md` — the CLI contract every
  adapter satisfies.
