---
name: run-osi-compliance
description: Run the OSI Foundation v0.1 compliance suite against the Python reference implementation and produce a readable Markdown report with per-decision coverage. Use when the user asks to run the compliance suite, validate OSI conformance, check D-NNN coverage, or assess Foundation conformance of impl/python.
---

# Run the OSI compliance suite

Execute the compliance suite at `compliance/foundation-v0.1/` against the
Python reference implementation, then surface a readable coverage report
indexed by Conformance Decision (D-001 … D-033).

## Instructions

### 1. One-time setup (skip if `.venv` already has the harness)

```bash
pip install -e compliance/harness
pip install -e compliance/foundation-v0.1
pip install -e impl/python
```

This installs three editable packages:

- `osi_compliance_harness` — the engine-agnostic runner / reporter.
- `osi_compliance_foundation_v0_1` — the per-version test suite metadata.
- `osi-python` — the reference Python implementation under test.

### 2. Run the suite

```bash
cd compliance/foundation-v0.1
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests/ \
    --datasets datasets/ \
    --output results/
```

To scope to a single area:

```bash
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests/bridge/ \
    --datasets datasets/ \
    --output results/bridge/
```

### 3. Read the report and triage

After a full run, surface:

1. **Overall pass rate** (e.g. "57 / 64 must-pass, 89.1%").
2. **Per-decision coverage** from `results/decisions_coverage.md` —
   list every D-NNN with at least one failing test.
3. For each failing test, classify the failure:
   - **impl bug** — the implementation is wrong vs the spec. Open a
     ticket / fix using the `debug-planner-output` skill (carried separately in willtown)
     skill if present, or directly inspect `impl/python/src/osi/`.
   - **test bug** — the test asserts something the spec doesn't say. Fix
     in `compliance/foundation-v0.1/tests/<area>/<case>/`.
   - **spec ambiguity** — neither side is unambiguously correct; the
     proposal text needs sharpening. Log the case for a proposal patch.
   - **xfail** — the test is intentionally `xfail` in `decisions.yaml`
     pending a future sprint; surface but do not flag as a regression.

### 4. Reference: contract

Every test under `tests/<area>/<difficulty>/<t-nnn-slug>/` has:

| File | Purpose |
|:--|:--|
| `metadata.yaml` | `id: T-NNN`, `decision: D-NNN`, `required_features`, `expected_error_code`, `xfail_reason` (if any). |
| `model.yaml` | Semantic model — usually a thin wrapper around a fixture in `datasets/f_*`. |
| `query.json` | Semantic query in the two-shape format (Aggregation or Scalar). |
| `gold_rows.json` | Expected row set for positive tests. |
| `gold.sql` | Reference SQL (illustrative only; tests assert on rows or `error.code`, never on SQL string per D-014). |

Tests assert on observable behaviour only:

- `expected_error_code: E_<NAME>` → adapter must surface that code in stderr.
- `gold_rows.json` → adapter SQL execution against the fixture data
  returns this exact multiset (order-insensitive unless `Order By` is set).

### 5. Do NOT

- Do not assert on the planner's generated SQL string. Cross-engine SQL
  determinism is **not** required (D-014 is per-engine only).
- Do not flip a `must_pass` decision to `xfail` to make CI green — that
  must go through `decisions.yaml` review with an `xfail_pinned_to`
  sprint anchor.
- Do not "fix" a test by changing its `expected_error_code` to whatever
  the implementation emits; first decide which side matches the spec.

## See also

- [`compliance/foundation-v0.1/README.md`](../../compliance/foundation-v0.1/README.md) —
  suite layout and quick start.
- [`compliance/foundation-v0.1/SPEC.md`](../../compliance/foundation-v0.1/SPEC.md) —
  what this suite targets and what it does NOT cover.
- [`compliance/ADAPTER_INTERFACE.md`](../../compliance/ADAPTER_INTERFACE.md) —
  the CLI contract adapters implement.
- [`proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  Appendix B (decisions) and Appendix C (error codes).
