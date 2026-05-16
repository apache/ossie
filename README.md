# OSI — Open Semantic Interchange

The Open Semantic Interchange (OSI) initiative is a collaborative,
open-source effort to standardise and streamline how semantic models
are exchanged and consumed across the data analytics, AI, and BI
ecosystem. Its goal is a common, vendor-agnostic semantic model
specification so a metric like "Total Revenue" or "Active Users"
means the same thing whether it is queried from an AI agent, a BI
tool, a notebook, or a downstream pipeline — eliminating definition
drift across the stack.

This repository hosts the standard, the reference implementation
that demonstrates how to build a conforming engine, and the
compliance suite that proves an engine actually conforms.

---

## Repository layout

| Directory | What lives here |
|:---|:---|
| [`proposals/`](proposals/) | **Active proposals**, versioned per tier. `proposals/foundation-v0.1/` holds the Foundation tier specification (`Proposed_OSI_Semantics.md`) and its expression-language companion (`SQL_EXPRESSION_SUBSET.md`). |
| [`impl/python/`](impl/python/) | **Reference implementation.** A Python compiler that parses an OSI semantic model + a `LODQuery` and emits dialect-specific SQL. Demonstrates how every normative rule in the proposal lands in code. |
| [`compliance/`](compliance/) | **Compliance test suite.** Concrete test vectors plus a tier-versioned suite (`compliance/foundation-v0.1/`) you point at any engine to certify conformance. Engine-agnostic. |
| [`docs/`](docs/) | High-level docs about the OSI initiative itself. |
| [`core-spec/`](core-spec/) · [`converters/`](converters/) · [`validation/`](validation/) · [`examples/`](examples/) | Legacy and supporting material for the wider OSI body of work. |
| [`ROADMAP.md`](ROADMAP.md) | Where the standard is going. |
| [`.agent-skills/`](.agent-skills/) | Tool-agnostic agent skill files (work with Cursor, Claude Code, and any tool that reads `SKILL.md` front-matter). |

---

## Use the reference implementation

[`impl/python/`](impl/python/) is the canonical reference engine for the
Foundation tier. Use it to:

- See exactly how a Foundation-conformant engine should behave on any
  given model + query.
- Inspect the planner / codegen pipeline as a worked example.
- Smoke-test a model file before plugging it into your own engine.

Quick start:

```bash
cd impl/python
pip install -e .
osi explain examples/models/demo_orders.yaml
osi plan examples/models/demo_orders.yaml examples/queries/revenue_by_region.json
osi compile examples/models/demo_orders.yaml examples/queries/revenue_by_region.json --dialect duckdb
```

Deeper reading:

- [`impl/python/README.md`](impl/python/README.md) — entry point, with
  a worked example end-to-end.
- [`impl/python/ARCHITECTURE.md`](impl/python/ARCHITECTURE.md) — how
  the parse → plan → codegen pipeline is wired.
- [`impl/python/SPEC.md`](impl/python/SPEC.md) — the per-feature
  implementation contract, indexed to the proposal's Conformance
  Decisions (D-NNN).
- [`impl/python/docs/JOIN_ALGEBRA.md`](impl/python/docs/JOIN_ALGEBRA.md)
  — the closed algebra and its laws (the proof surface the planner
  uses to guarantee correctness).
- [`impl/python/INFRA.md`](impl/python/INFRA.md) — quality standards
  and CI gates that keep the reference implementation honest.

---

## Run the compliance suite

[`compliance/foundation-v0.1/`](compliance/foundation-v0.1/) is the
**engine-agnostic** Foundation v0.1 conformance suite. Each test
ships:

- A `metadata.yaml` naming the Conformance Decision (`D-NNN`) it pins,
  the difficulty tier, and any `xfail` reason.
- A `model.yaml` (semantic model) and `query.json` (semantic query).
- A `gold_rows.json` (positive tests) **or** an `expected_error_code`
  (negative tests). Tests assert on observable behaviour — rows or
  error codes — never on SQL strings (per D-014, cross-engine SQL
  determinism is non-normative).

Point it at the reference Python implementation:

```bash
pip install -e compliance/harness
pip install -e compliance/foundation-v0.1
pip install -e impl/python

cd compliance/foundation-v0.1
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests/ \
    --datasets datasets/ \
    --output results/
```

Then read `compliance/foundation-v0.1/results/REPORT.md` for the
overall pass rate and `decisions_coverage.md` for the per-decision
breakdown.

To certify your own engine, implement the
[`compliance/ADAPTER_INTERFACE.md`](compliance/ADAPTER_INTERFACE.md)
contract and rerun the same suite with `--adapter
path/to/your_adapter.py`. The suite never imports the engine; it
only invokes the adapter binary.

Deeper reading:

- [`compliance/foundation-v0.1/README.md`](compliance/foundation-v0.1/README.md)
  — suite layout, fixtures, and triage workflow.
- [`compliance/foundation-v0.1/SPEC.md`](compliance/foundation-v0.1/SPEC.md)
  — what the suite covers and what it intentionally does NOT cover.
- [`compliance/foundation-v0.1/DATA_TESTS.md`](compliance/foundation-v0.1/DATA_TESTS.md)
  — the catalog of concrete test vectors used to populate the suite.

---

## Agent skills (Cursor, Claude Code, …)

If you use Cursor or Claude Code (or any agent that reads `SKILL.md`
front-matter), the skills in [`.agent-skills/`](.agent-skills/) give
the agent first-class entry points for the most common workflows:

- `run-osi-python-tests` — runs the full impl/python test pyramid
  (unit / property / golden / e2e / mutation / coverage / lint /
  typecheck / architecture) and produces a single readable report.
- `run-osi-compliance` — runs the Foundation v0.1 compliance suite
  against the reference implementation and surfaces a per-decision
  coverage report.

See [`.agent-skills/README.md`](.agent-skills/README.md) for how to
wire them into Cursor or Claude Code (one-line symlink each).

---

## License

All code in this repository is licensed under the
[Apache 2.0 license](LICENSE).

The specification and documentation are licensed under the
[Creative Commons Attribution license (CC BY)](LICENSE-Docs).
