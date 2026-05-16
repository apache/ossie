# Foundation v0.1 Compliance Report — `osi_python` adapter

Generated during Phase 7 of the OSI_will migration-and-polish plan.

## Default suite (every `status: active` test)

| Metric | Value |
|:---|---:|
| Active tests discovered | 7 |
| Passed | 7 |
| Failed | 0 |
| Errors | 0 |
| Skipped | 0 |
| **Compliance** | **100.0%** |

The default invocation —
```bash
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests \
    --datasets datasets \
    --output results
```
runs every test whose `metadata.yaml` carries `status: active` and is the
gate that defines Foundation v0.1 conformance for the bundled adapter. All
seven active cases pass against the in-tree `impl/python` reference
implementation:

| Test | Area | Decision | Shape |
|:---|:---|:---|:---|
| `t-005c-nested-avg` | cross_grain | D-027 | negative — `E_NESTED_AGGREGATION_DEFERRED` |
| `t-046-field-references-field-chain` | field_metric_grain | D-018 | positive |
| `t-047-field-chain-reverse-declared` | field_metric_grain | D-018 | positive |
| `t-048-windowed-field-referenced` | field_metric_grain | D-028 | positive |
| `t-049-field-chain-cross-dataset-enrich` | field_metric_grain | D-018 | positive |
| `t-017-nested-aggregation-over-bridge` | nested_aggregates | D-027 | negative — `E_NESTED_AGGREGATION_DEFERRED` |
| `t-050-field-dependency-cycle` | validation_errors | (structural) | negative — `E_FIELD_DEPENDENCY_CYCLE` |

## Extended suite (`--include-planned`)

The `tests/` tree also ships 79 cases with `status: planned`. They are
shipped today so future sprints have a concrete witness to flip from
planned → active. Running them through the bundled adapter currently
yields:

| Metric | Value |
|:---|---:|
| Total tests | 86 |
| Passed | 80 |
| Failed | 4 |
| Errors | 2 |
| **Conformance against planned tier** | **93.0%** |

The six non-passing planned cases are intentional gaps documented in
detail below. Each is either an impl gap pinned to a future sprint or a
test that exercises a Foundation rejection along a different code path.

### Planned-tier failures and root-cause classification

#### G-1 `t-014-mn-no-bridge` — adapter emits `E_NO_PATH`, spec wants `E3013`

**Class:** impl gap. The `f_bridge_none` fixture declares two
unrelated fact tables (`actors`, `movies`) with `relationships: []`.
A query that references both has no path through the graph, so the
planner raises `E2004_UNREACHABLE_DATASET` which the adapter maps to
`E_NO_PATH`. Per Appendix C this case is
`E3013_NO_STITCHING_DIMENSION` — two unrelated facts referenced
together with no shared stitch dimension would yield a Cartesian
product.

**Root cause:** the adapter's `_LEGACY_CODE_MAP` maps every
`E2004_UNREACHABLE_DATASET` to `E_NO_PATH`. A future sprint (S-10)
should narrow this to `E3013_NO_STITCHING_DIMENSION` when the
unreachable dataset is the home of a measure (or, equivalently,
when two distinct root datasets are referenced).

#### G-2 / G-3 `t-016-non-distributive-over-bridge-accepted`, `t-051-holistic-over-bridge-accepted`

**Class:** impl gap (D-027 bridge for non-distributive aggregates).
The Foundation spec accepts `AVG`, `MEDIAN`, and other non-distributive
aggregates over an N:N bridge in a single pass (D-027). The
reference impl today resolves the bridge for distributive aggregates
(SUM, COUNT) but raises `E_UNSAFE_REAGGREGATION` (t-016) or
`E1206` "composite metric body has a raw fact" (t-051) for the
non-distributive cases.

**Root cause:** documented in
`impl/python/src/osi/planning/planner_bridge.py` module docstring.
The current two-stage bridge resolution doesn't survive
non-distributive aggregates; rewriting the bridge planner to a
single-pass dedup form is the open work item.

**Pinned to:** the next bridge-resolution sprint after Phase 11.

#### G-4 `t-042v-ordered-set-aggregates`

**Class:** test design / impl reach. The test declares a metric
`median_amount = PERCENTILE_CONT(0.5) WITHIN GROUP (...)`. The
adapter's composite-metric machinery sees `orders.amount` as a raw
fact reference inside a metric body and raises `E1206` (raw facts
may only appear via top-level metrics or aggregations). That's a
valid Foundation rejection — just a different code than the
spec-mandated `E_DEFERRED_KEY_REJECTED`.

A future sprint that adds parse-level detection of `WITHIN GROUP`
ordered-set aggregates can surface the spec-mandated code; today
the engine rejects the construct through a different path.

#### G-5 `t-042x-multi-hop-bridge`

**Class:** test design / impl reach. The test declares two
relationships from `returns` to `customers` with identical
`from_columns`/`to_columns`. The path planner detects this as
`E_AMBIGUOUS_PATH` ("multiple relationships reach 'customers'").
Like G-4, this is a valid Foundation rejection along a different
code path than `E_DEFERRED_KEY_REJECTED`.

#### G-6 `t-042y-snowflake-partition-by-excluding`

**Class:** parser limitation. The metric body uses Snowflake's
`PARTITION BY ... EXCLUDING TIES ORDER BY ...` window clause.
`sqlglot` cannot parse this with its default dialect and the
adapter surfaces `E1001: could not parse metric expression`. This
is again a valid rejection — the Foundation engine refuses to
compile the model — just under a more generic code than the
spec-mandated deferred-key code.

A future sprint that recognises Snowflake-only window syntax in
the parser can promote this to `E_DEFERRED_KEY_REJECTED`.

## Methodology

Reports under `results/` are regenerated from disk by
`harness.runner`:

- `results/summary.md` — pass/fail per test (auto-generated).
- `results/failures.csv` — failed cases with classification info
  (auto-generated).
- `results/REPORT.md` — *this file*; written by Phase 7 of the
  OSI_will migration-and-polish plan. Kept in version control so
  the conformance baseline is reviewable in PR diffs.

To reproduce locally:

```bash
cd /path/to/OSI_will
pip install -e impl/python
pip install -e compliance/harness
cd compliance/foundation-v0.1
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests \
    --datasets datasets \
    --output results            # active tests only
python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests \
    --datasets datasets \
    --output results \
    --include-planned          # 86-test extended view
```
