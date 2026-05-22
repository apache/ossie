# OSI — Python Reference Implementation

The reference implementation of the [Open Semantic Interchange](https://opensemanticinterchange.com)
**Foundation** proposal (`osi_version: "0.1"`). It implements a
deliberately narrow first-cut of OSI semantics — small enough to be
provably correct, with the goal of building consensus on fundamentals
before layering richer features back on top.

> **One-line summary.** Parse a YAML semantic model, plan a semantic
> query via a closed algebra over immutable states, render
> dialect-specific SQL via SQLGlot — with mutation-tested
> property-based tests of the algebra laws as the correctness boundary.

The authoritative spec the implementation conforms to lives in
[`../../proposals/foundation-v0.1/`](../../proposals/foundation-v0.1/).
The runnable compliance suite lives in
[`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/).

---

## Scope

- **In scope:** core semantics (datasets, relationships, fields,
  metrics, parameters), two query shapes (`Aggregation` + `Scalar`
  with `Fields`), joins including chasm-trap and fan-out safety, M:N
  resolution via bridge or shared-dim stitch, a SQL subset
  (`OSI_SQL_2026` default dialect), and standard SQL window functions.
  See
  [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  (`osi_version: "0.1"`).
- **Out of scope (deferred):** LOD grain modes, filter context / reset,
  semi-join filter form (`EXISTS_IN`), per-metric
  `joins.{type, using_relationships}`, `referential_integrity`,
  grouping sets, pivot, non-equijoins, ASOF, semi-additive measures,
  dataset-level filters with scope propagation, parameterized window
  frame bounds, `GROUPS` frame mode, windowed-metric composition. The
  full normative list is §10 of the Foundation spec; the design archive
  is described in `Proposed_OSI_Semantics.md §10`.
  *Model-scoped named filters* (top-level `filters:`) and
  *parameters* (top-level `parameters:`) are part of the Foundation
  and accepted by the parser today — see
  [`examples/models/demo_orders.yaml`](examples/models/demo_orders.yaml).

We keep deferred features out of the code entirely — they raise
`E_DEFERRED_KEY_REJECTED` at parse time — so the Foundation surface
stays thin.

---

## Documents

Read in this order:

| # | Document | What it is |
|:--:|:---|:---|
| 1 | [`SPEC.md`](SPEC.md) | Project goals, feature scope, expression handling, and error discipline reference. |
| 2 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | The three-layer pipeline, architectural invariants, where-to-add-things decision tree. |
| 3 | [`INFRA.md`](INFRA.md) | Quality standards, toolchain, infrastructure roadmap, decisions log. |
| 4 | [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) | The Foundation — authoritative standard. |
| 5 | [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md) | The closed algebra — operators, preconditions, grain contracts, laws. |
| 6 | [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md) | How each algebra law is property-tested and mutation-guarded. |
| 7 | [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md) | The four-layer test pyramid + mutation testing. |
| 8 | [`RUNNING_TESTS.md`](RUNNING_TESTS.md) | Running the test suite end-to-end, including mutation testing and the readable report. |

Features not in Foundation v0.1 are listed in `Proposed_OSI_Semantics.md §10`.
For the error code catalog see [`docs/ERROR_CODES.md`](docs/ERROR_CODES.md).

---

## Quick start

> **Five-minute adopter path.** Parse a YAML model, plan a semantic
> query, render dialect-specific SQL. The contributor `make` targets
> live below.

```python
import sqlglot
from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.parsing.parser import parse_semantic_model
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.planner_context import PlannerContext

MODEL = """
semantic_model:
  - name: sales
    dialect: ANSI_SQL
    datasets:
      - name: orders
        source: sales.orders
        primary_key: [order_id]
        fields:
          - {name: order_id,    expression: order_id,    role: dimension}
          - {name: customer_id, expression: customer_id, role: dimension}
          - {name: status,      expression: status,      role: dimension}
          - {name: amount,      expression: amount,      role: fact}
      - name: customers
        source: sales.customers
        primary_key: [id]
        fields:
          - {name: id,     expression: id,     role: dimension}
          - {name: region, expression: region, role: dimension}
    metrics:
      - {name: total_revenue, expression: SUM(orders.amount)}
    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
"""

parsed = parse_semantic_model(MODEL)
context = PlannerContext(
    model=parsed.model,
    namespace=parsed.namespace,
    graph=parsed.graph,
)

query = SemanticQuery(
    dimensions=(Reference(dataset=normalize_identifier("customers"),
                          name=normalize_identifier("region")),),
    measures=(Reference(dataset=normalize_identifier("orders"),
                        name=normalize_identifier("total_revenue")),),
    where=FrozenSQL.of(sqlglot.parse_one("orders.status = 'completed'")),
)

sql = compile_plan(plan(query, context), dialect=Dialect.DUCKDB)
print(sql)
```

For runnable end-to-end scenarios (a model + a query + a `osi compile`
command), see [`examples/`](examples/README.md).

## Opting back into deferred features

A few decisions in `Proposed_OSI_Semantics.md` are deferred but the
implementation keeps the *opt-back-in* path behind a feature-flag
object. Use it when migrating an existing model that still relies on
the legacy form:

```python illustrative
from osi.config import FoundationFlags
from osi.parsing.parser import parse_semantic_model

result = parse_semantic_model(
    "model.yaml",
    flags=FoundationFlags(allow_aggregate_in_field=True),
)
```

(The `illustrative` directive tells the README drift test in
`tests/integration/readme/` to compile-only the block — `model.yaml` is
caller-supplied and the snippet would otherwise fail at runtime.)

The flags are documented in [`src/osi/config.py`](src/osi/config.py).
Models that turn flags on are no longer portable — the canonical
Foundation v0.1 stance is `flags=None` (the default).

## Contributor setup

The `make` targets cover every stage CI runs:

```bash
cd impl/python
make install-dev          # creates .venv, installs deps and pre-commit hooks
make check                # lint + type + unit + property + golden + E2E
make test                 # the full test suite
make mutation-fast        # mutation testing on the algebra module (~5 min)
make mutation             # mutation testing on everything (~30 min)
```

See [`RUNNING_TESTS.md`](RUNNING_TESTS.md) for the readable per-stage
report.

---

## Repository layout

```
impl/python/                     (this directory)
  README.md                      # this file
  RUNNING_TESTS.md               # how to run every test category + the report
  SPEC.md                        # what to build
  ARCHITECTURE.md                # how the layers fit
  INFRA.md                       # quality gates & toolchain
  AGENTS.md · CONTRIBUTING.md

  src/osi/                       # the implementation
    parsing/
    planning/
      algebra/
    codegen/
    diagnostics/
    common/
    errors.py

  conformance/                   # CLI adapter used by the compliance suite
    adapter.py
    enabled_proposals.yaml

  specs/                         # design context for THIS implementation
    README.md
    deferred/                    # out-of-scope proposal design archive (reference only)

  docs/                          # implementation deep dives
    README.md
    ALGEBRA_LAWS.md
    TESTING_STRATEGY.md
    ERROR_CODES.md
    ERRATA_ALIGNMENT.md
    JOIN_SAFETY.md
    mapping_bi_models_to_core_osi_abstractions.md

  scripts/                       # test runner + report writer
  tests/
    unit/
    properties/                  # Hypothesis-based algebra laws
    golden/                      # snapshot plan + SQL golden files
    e2e/                         # DuckDB-executed row-level tests

  examples/
    models/                      # example YAML models

../../proposals/foundation-v0.1/ # the authoritative spec this impl conforms to
../../compliance/foundation-v0.1/# the runnable compliance suite
../../compliance/harness/        # the suite runner harness
```

---

## Status

**Phase 3 — Foundation in flight.** The three pipeline layers (parsing,
planning, codegen), the closed algebra, diagnostics, CLI, and the four-layer
test pyramid (unit + Hypothesis property laws + plan/SQL goldens + DuckDB
e2e) are all landed. `make check` is green, ~93 % line coverage, all 12
algebra laws in [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md) have property
tests.

Recently completed Foundation work (see `INFRA.md §3`):

- Multi-hop N:1 enrichment chains (`A → B → C`). Compliance cases
  `C-1.7` / `C-1.9` now pass.
- Model-scoped composite metrics (`ratio = a / NULLIF(b, 0)`), lowered
  via a post-`AGGREGATE` `ADD_COLUMNS` step
  (`Proposed_OSI_Semantics.md §5.4`).
- `SemanticQuery.parameters` and named-filter references inside `where`
  (`§4.6` / `§5.1`).
- Cross-dataset dimension-only queries pick a unique safe anchor
  instead of silently falling back to the first-declared dimension.
- M:N resolution per `§6.5`: bridge anchor discovery for dim-only
  queries, multi-fact stitch validation, and the spec-mandated
  `E3012_MN_NO_STITCH_PATH` / `E3013_NO_STITCHING_DIMENSION` errors
  for per-query failures. (`E3011_MN_AGGREGATION_REJECTED` is
  reserved for engines that opt out of M:N support entirely;
  `osi_python` supports M:N and never raises `E3011` at the
  user-facing surface.)
- Per-metric `joins.using_relationships` path disambiguation
  (`§6.7`), threaded through every enrichment-chain BFS as a
  whitelist.
- `EXISTS_IN` codegen now compiles to correlated `EXISTS (SELECT 1
  ...)` per `§7.4 + §11 #8`; the previous `IN (SELECT keys)` shape
  was both spec-incorrect (NULL semantics) and dialect-fragile.
- `unique_keys` honored end-to-end: the algebra's fan-trap check
  accepts join keys that match the PK *or any UK* (not just the PK),
  so models that mark a 1:N relationship as M:N can be recovered by
  declaring the appropriate UK. New invariant `I-9` in
  `algebra/state.py`.
- Mid-pipeline bridge resolution (`§6.5.1`, mid-pipeline form). The
  planner pre-aggregates the measure to the bridge's link-key grain
  when an `N : N` edge sits between fact and target, sources the
  bridge as a fresh root, and re-aggregates at the query grain. Every
  well-defined single-bridge query plans regardless of where the
  bridge sits in the chain. Compliance case `C-3.5` flipped from
  `xfail` to passing; the genuinely-deferred multi-bridge case (query
  references both outer endpoints A and B in `A↔Br1↔X↔Br2↔B`) is
  pinned as `C-3.5b xfail` against the narrowed `§10` deferred entry.

Remaining Foundation gaps:

- `SEMANTIC_VIEW(...)` SQL surface — deferred to the future
  SQL_INTERFACE proposal; parser not yet implemented. Tracked as
  `INFRA.md §3 I-12`. Error codes `E1201`–`E1213` are carved out in
  `osi.errors` with `RESERVED` annotations so the eventual parser
  lands with stable codes.
- Per-metric `joins.type` override (`§6.7`) — schema accepts the
  field, planner doesn't yet thread it through to the join-type
  picker. `using_relationships` is fully wired.

Explicitly deferred (`Proposed_OSI_Semantics.md §10`) and tracked as
`xfail` compliance cases:

- Multi-hop bridge resolution `A → Br1 → Br2 → B` (`C-3.5`). The
  Foundation resolves M:N through a single bridge dataset; chained
  bridges are §10-deferred. Models that need it can compose two
  single-bridge resolutions by introducing an intermediate dataset
  modelled as a fact.

See [`INFRA.md §3`](INFRA.md) for the infrastructure roadmap.

---

## License

TBD — intended to match the OSI standard's license when published.
