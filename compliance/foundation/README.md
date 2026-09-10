<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# OSI Compliance Test Suite — Foundation

**Targets the
[Ossie foundational semantics](../../core-spec/foundational_semantics.md) spec
and its SQL expression subset
[`expression_language.md`](../../core-spec/expression_language.md)**

The Foundation is the deliberately narrow first-cut of Ossie semantics:
two query shapes, implicit home-grain aggregation, window functions,
and a small deferred-features list — pinned by numbered Conformance
Decisions (D-NNN) throughout the Foundation spec, and an error code
index in its appendix.

The runner harness lives at [`../harness/`](../harness/) and is
shared with every future per-version suite under `compliance/`.

## Status of this slice

This is a **bootstrap slice**, not the full suite. It ports over just
enough of the compliance suite to make the mechanism (test discovery,
metadata schema, decision-coverage tracking, DuckDB-backed row
comparison, reporting) runnable and testable end to end:

- **Tests present:** `tests/cross_grain/moderate/` only — 4 cases
  (`t-005a`, `t-005b`, `t-005d`, `t-005e`) covering D-020 (single-step
  cross-grain aggregation over a `1 : N` edge). Every other area listed
  under `Layout` below (query shape, bridge/M:N, windows, namespace,
  etc.) is the *target* design carried over from the source proposal
  and will land — with its own tests — in follow-up PRs.
- **No adapter yet.** `adapters/osi_python_adapter.py` delegates to
  `impl/python/conformance/adapter.py`, which does not exist in this
  repo yet (`impl/python/` is still a placeholder). Until an
  implementation lands, `uv run python -m harness.runner --list` is the way
  to exercise this suite — it discovers and validates the test corpus
  without needing an engine. The 4 included tests carry
  `status: planned` in their `metadata.yaml` (matching their status in
  the source proposal — the semantics they pin are accepted but not
  yet required-to-pass), so a full `harness.runner` run against a
  partial adapter will correctly skip them unless invoked with
  `--include-planned`.
- **`decisions.yaml`** keeps every `D-NNN` row from the source
  proposal (so `harness`'s own registry-consistency tests still pass),
  but only `D-020` has a populated `tests:` list — every other
  decision's `tests:` is intentionally empty pending its witness
  tests landing in a later PR.
- **Dropped `T-024`.** The source proposal's compliance suite also
  shipped `tests/cross_grain/moderate/t-024-boolean-home-grain-scalar-in-where/`.
  It was excluded from this slice: its `gold.sql` filters on
  `customers.segment = 'PREMIUM'`, a value that does not exist in the
  `f_prelude` fixture (segments are `retail` / `wholesale`), so the
  query returns zero rows — and the test as implemented doesn't
  exercise the aggregate-derived boolean-field case that
  `DATA_TESTS.md` §4.G documents for T-024 (`has_completed_orders`,
  an aggregate `COUNT(...) > 0` field routed through `Where`). See
  the PR description for the full writeup; worth fixing at the
  source before it's re-added here.

## Layout

```
compliance/foundation/
  README.md                     # this file
  SPEC.md                       # the specs this suite targets (Foundation v0.1)
  pyproject.toml                # workspace member; depends on ../harness (osi_compliance_harness)
  conformance.yaml              # test conformance levels; "foundation_v0_1" = required
  proposals.yaml                # mirrors §3 (deferred features) of the Foundation spec; every deferred is a proposal
  decisions.yaml                # D-NNN registry with anchor + status; tests: populated only for D-020 in this slice
  adapters/
    osi_python_adapter.py       # delegates to impl/python/conformance/adapter.py (not yet present)
  datasets/
    f_prelude/                  # mirrors DATA_TESTS.md §3.1 — the only fixture this slice needs
  tests/
    cross_grain/                # T-005a/b/d/e (D-020) — the only populated area in this slice
  results/                      # created by the runner (gitignored); no committed baseline yet
```

The full target layout — `query_shape/`, `scalar_query/`, `bridge/`,
`windows/`, `namespace/`, etc. — is documented in `DATA_TESTS.md` and
`decisions.yaml`; those directories don't exist on disk yet.

## Quick start

```bash
# One uv sync at the compliance workspace root installs everything
# (this suite + the shared harness) into a single environment.
cd compliance
uv sync

cd foundation

# List discovered tests without running them (no adapter needed).
# The 4 cross_grain/moderate cases show status "planned" — they're
# skipped by a real run until an adapter lands, unless you pass
# --include-planned. `uv run` uses the workspace env automatically.
uv run python -m harness.runner --list --tests tests/ --include-planned

# Once an OSI implementation adapter exists (see ../ADAPTER_INTERFACE.md):
uv run python -m harness.runner \
  --adapter adapters/osi_python_adapter.py \
  --tests tests/ \
  --datasets datasets/ \
  --include-planned
```

The harness ships under [`../harness/`](../harness/) and is installed
as the `osi_compliance_harness` package — every per-version suite under
`compliance/` shares this one runner / reporter / DB manager. The
harness's own mechanics (discovery, adapter invocation, row
comparison, reporting) are covered by its unit tests — see
[`../harness/README.md`](../harness/README.md).

## Per-test layout

Each test is a folder containing:

| File | Purpose |
|:---|:---|
| `metadata.yaml` | `id: T-NNN`, `decision: D-NNN`, `spec_refs`, `required_features`, `expected_error_code`, `xfail_reason` (if applicable). |
| `model.yaml` | The semantic model (typically a thin wrapper around a fixture from `datasets/f_*`). |
| `query.json` | The semantic query, in the new two-shape format (`dimensions` + `measures` for aggregation queries; `fields` for scalar queries). |
| `gold.sql` | A hand-written reference SQL query the harness executes against the fixture data to produce the expected row multiset. Treated by the harness as a row oracle, not as a SQL-string comparison — D-014 is per-engine, not cross-engine. |

Tests assert on observable behaviour only:

- `expected_error_code: E_<NAME>` ⇒ adapter must surface that code in
  stderr (substring match — see `compliance/harness/src/harness/runner.py`).
- The harness runs both `gold.sql` and the adapter's emitted SQL against
  the shared fixture and compares the resulting row multisets
  (order-insensitive unless the query has an `Order By`).

This means a `gold.sql` is a *witness* of the answer's shape and the
fixture data; the harness never compares SQL strings byte-for-byte
(per D-014, that's a per-engine concern).

## Decision coverage

Every `D-NNN` row in the Foundation spec should eventually have at
least one runnable case here. The mapping lives in `decisions.yaml`;
in this slice only `D-020` has one.

## See also

- `SPEC.md` — what the suite targets and why.
- `decisions.yaml` — the D-NNN status board.
- `proposals.yaml` — §10 deferred-features registry.
- `../../impl/python/` — where the upstream engine and its
  `conformance/adapter.py` will land.
- `../ADAPTER_INTERFACE.md` — the CLI contract every
  adapter satisfies.
