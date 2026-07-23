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

# Ossie Compliance

This directory holds the compliance test suites that verify an Ossie
implementation against the semantics of a specification, plus the shared,
engine-agnostic runner that executes them.

An engine proves conformance by running the suite through a small **adapter**:
the harness feeds it a semantic model and a query, then checks the *rows* the
engine's SQL produces (or the *error code* it raises) against a hand-written
reference. It never inspects the engine's SQL text — semantics are portable,
SQL is not.

- **New here?** Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the spec,
  the harness, and adapters fit together.
- **Contents:**
  - [`ADAPTER_INTERFACE.md`](ADAPTER_INTERFACE.md) — the CLI contract an engine
    adapter must satisfy.
  - [`harness/`](harness/) — the shared runner / reporter / DB manager
    (`osi_compliance_harness`).
  - [`foundation/`](foundation/) — the suite for the Foundation spec
    (`osi_version: "0.1"`). See its [README](foundation/README.md) for the
    current bootstrap-slice status.

The Foundation suite targets
[`../core-spec/foundational_semantics.md`](../core-spec/foundational_semantics.md)
(Conformance Decisions `D-NNN` + error-code index) and the expression subset in
[`../core-spec/expression_language.md`](../core-spec/expression_language.md).

---

## Install

`compliance/` is a single [`uv`](https://docs.astral.sh/uv/) workspace: the
shared harness and the Foundation suite are members of one environment, so a
single `uv sync` installs everything — there are no per-package installs.

```bash
cd compliance
uv sync            # builds ./.venv with every workspace member (editable)
```

This installs `osi_compliance_harness` (the runner) and
`osi_compliance_foundation_v0_1` (the suite) plus their dependencies
(`pyyaml`, `duckdb`, `sqlglot`). Use `uv run` to execute commands (below): it
picks up the workspace environment automatically, from any directory under
`compliance/`, with no activation step.

## Run

Run the suite from its root (`compliance/foundation`) so the default
`results/latest/` output lands under that suite. `uv run` finds the workspace
environment automatically — no activation needed:

```bash
cd compliance/foundation
```

**List discovered tests (works today — no engine required).** This exercises
discovery and registry validation without an adapter, so it is the way to sanity
-check the suite while the reference implementation is still a placeholder:

```bash
uv run python -m harness.runner --list --tests tests/ --include-planned
```

**Run the suite against an engine** (once its adapter exists, see
[`ADAPTER_INTERFACE.md`](ADAPTER_INTERFACE.md)):

```bash
uv run python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests/ \
    --datasets datasets/ \
    --include-planned
```

Reports (`failures.csv`, `summary.md`) are written under `--output`
(default `results/latest/`). The exit code is non-zero if any test fails or
errors, so the command drops straight into CI.

### Common flags

| Flag | Effect |
|------|--------|
| `--list` | List discovered tests and exit; no adapter/datasets needed. |
| `--include-planned` | Include tests marked `status: planned` (skipped by default). |
| `--area <name>` | Only tests in an area (e.g. `cross_grain`). |
| `--difficulty <easy\|moderate\|hard\|conversion>` | Filter by difficulty. |
| `--conformance-level <level>` | Only tests at a level declared in `conformance.yaml` (e.g. `foundation_v0_1`). |
| `--proposals <id …>` | Proposal IDs the adapter implements; tests needing others are **SKIP**ped. Alias: `--adapter-features`. |
| `--timeout <seconds>` | Per-test adapter budget (default 60). |
| `--output <dir>` | Where reports go (default `results/latest/`). Don't overwrite the tracked `results/REPORT.md`. |
| `--verbose` | Show error detail for failures/errors. |

## Add a test

A test case is a directory of four files under `tests/<area>/<difficulty>/t-NNN-slug/`:

| File | Purpose |
|------|---------|
| `metadata.yaml` | `test_id`, `decision: D-NNN`, `area`, `difficulty`, `dataset`, `spec_refs`, `conformance_level`, `status`; plus `expected_error` / `expected_error_code` for negative tests. |
| `model.yaml` | the semantic model under test. |
| `query.json` | the semantic query (`dimensions` + `measures`, or `fields`; an `order_by` makes the row comparison order-sensitive). |
| `gold.sql` | reference SQL run against the fixture to produce the expected rows — a **row oracle**, not a string to match. |

Then map the case to its decision in
[`foundation/decisions.yaml`](foundation/decisions.yaml) by adding its folder to
that `D-NNN`'s `tests:` list. If the case depends on a feature, name it in
`required_features` and make sure it exists in
[`foundation/proposals.yaml`](foundation/proposals.yaml) (CI's
`proposals_check` rejects unknown IDs).

## Claim conformance

Conformance levels are defined in
[`foundation/conformance.yaml`](foundation/conformance.yaml):

- **`foundation_v0_1`** — required; every `must_pass` decision must produce the
  expected rows or error code.
- **`foundation_v0_1_strict`** — optional; adds per-engine SQL-determinism
  witnesses (D-014/D-029).

Conformance is judged on **observable behaviour** — rows and error codes — never
on the specific SQL an engine emits.

## Status

The Foundation suite is currently a **bootstrap slice**: it ships the four
`t-005` cross-grain cases (decision **D-020**) and the full registry scaffolding,
but the reference engine (`impl/python/`) is still a placeholder, so only the
`--list` path runs end to end today. See
[`foundation/README.md`](foundation/README.md) for what is ported and what is
still to come.
