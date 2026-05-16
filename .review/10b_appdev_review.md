# Code Review — Phase 10b — Application Developer

Audit target: `/Users/wpugh/projects/OSI_will/impl/python/` — adopter ergonomics.

## Summary table (verdict per angle)

| Angle | Verdict | Notes |
|------|---------|--------|
| 1. Five-minute onramp | **Mixed [10b]** | Strong inlined Python path to SQL; front-loaded dev tooling (`make install-dev` / `make check`), no `pip install -e .`-first story or CLI one-liner in "Quick start". |
| 2. CLI UX | **Good structure, gaps [10b]** | Subcommands match mental model; no guaranteed `osi` on `PATH`; failures omit structured `context`. |
| 3. API ergonomics | **Subpackage-first [10b]** | `osi/__init__.py` does not expose `parse_model` / `plan` / `compile_to_sql`; `planning/__init__.py` re-exports a very wide surface. |
| 4. Error catalog | **Strong [10b]** | Explanations are spec-cited and mostly actionable. |
| 5. Examples | **Thin [10b]** | `examples/models/` is mostly YAML; no paired query + expected SQL walkthrough. |
| 6. Extension points | **Dialect: documented [10b]** | Clear steps in `dialect.py`; `ARCHITECTURE.md` §9 has factual errors and phantom files. |
| 7. `RUNNING_TESTS.md` | **Excellent [10b]** | Makefile + focused pytest + report script documented clearly. |
| 8. Top-level imports / autocomplete | **Minimal package root [10b]** | `import osi` exposes essentially `__version__` only. |
| 9. Tracing / explainability | **Solid for plans [10b]** | `explain` traces steps, grain, payload summaries. |
| 10. Versioning / feature flags | **Code-documented, README-quiet [10b]** | `FoundationFlags` is well documented in `config.py` / `parser.py`; no adopters' story in README. |

---

## Blocking findings `[10b]`

### B1 `ARCHITECTURE.md` §9 documents wrong `plan()` signature and phantom files

`ARCHITECTURE.md` L409–L415 says:

- `osi.planning.plan(ctx, query)` — but the real signature is
  `plan(query, context)` (`src/osi/planning/planner.py` L125).
- `osi_cli.py` — does not exist; the CLI lives at `src/osi/cli.py`
  and is invoked via `python -m osi`.
- `examples/run_example.py` — does not exist under
  `impl/python/examples/`.

A newcomer following the architecture doc loses time chasing missing
files and wrong argument orders.

### B2 README "Scope" disagrees with the shipped models

`README.md` L31–L42 lists named filters, `referential_integrity`, and
several other features as "out of scope (deferred) — raises
`E_DEFERRED_KEY_REJECTED` at parse time". The example model
`examples/models/demo_orders.yaml` and the sibling `models/README.md`
contradict that by declaring top-level `filters:` and
`referential_integrity`. Adopters must guess which sentence is stale.

### B3 No `[project.scripts]` entry; docs assume `osi` shell command

`pyproject.toml` does not register an `osi` console script. The
supported invocation is `python -m osi …` (per
`src/osi/__main__.py` L1–L7). Docs that say `osi explain-code` (e.g.
`ARCHITECTURE.md` §9) point at a binary that does not exist after
`pip install -e .`.

Fix: either add `[project.scripts] osi = "osi.cli:main"`, or update
every reference to use `python -m osi …`.

---

## Important findings `[10b]`

### I1 CLI prints only the message, not `OSIError.context`

`OSIError` carries rich `context: dict[str, object]` (`src/osi/errors.py`
L255–L273). The CLI handler (`src/osi/cli.py` L274–L282) writes only
`f"{err.code.value}: {err}\n"` to stderr — the actionable hints in
`context` (suggestions, candidate names, dataset / field / grain) are
silently dropped for users not running Python.

### I2 `osi/__init__.py` is not a happy-path façade

`src/osi/__init__.py` L1–L16 documents imports in a docstring but does
not re-export them. Adopters cannot write
`from osi import parse_semantic_model, plan, compile_plan`; they must
dig through subpackages.

### I3 `planning/__init__.py` re-exports a large surface

`src/osi/planning/__init__.py` L71–L126 lists ~50 names including
algebra ops, plan-builder internals, and resolve helpers. Fine for
power users; noisy for "I just want `plan` + types". Consider
splitting between top-level (the user API) and
`osi.planning.internals` (planner extension surface).

### I4 Examples are not end-to-end runnable

`examples/` ships YAML models only; no committed `queries/` JSON, no
README walkthrough showing `python -m osi compile examples/models/
demo_orders.yaml examples/queries/<query>.json --dialect duckdb`.
The common BI demos (fan-trap, bridge, composite metric) are
exercised through `tests/`, not adopter-facing examples.

### I5 `FoundationFlags` is absent from the README Quick Start

Legitimate adopters hitting `E_AGGREGATE_IN_FIELD` /
`E_NESTED_AGGREGATION_DEFERRED` need
`parse_semantic_model(..., flags=...)`. The contract is documented in
`config.py` and `parser.py` (L54–L71) but not surfaced in the landing
README.

### I6 No forward-version story for `osi_version` in README

When Foundation v0.2 ships, what changes for an adopter? The README
has no "upgrading" section. Ties to Phase 10a B2.

---

## Nits `[10b]`

### N1 Stale CLI comment in `error_catalog.py`

`src/osi/diagnostics/error_catalog.py` L10–L15 says
"CLI ``osi explain <code>`` (future)"; the command exists as
`explain-code`.

### N2 README Quick Start front-loads contributor steps

`README.md` L68–L75 leads with `make install-dev`, `make check`,
`make test` before the Python SQL example. For an "application
developer" landing on this README, the Python-to-SQL example should
come first.

### N3 `ARCHITECTURE.md` wording vs `dialect.py`

Minor alignment between "dialect.py + transpiler variant" and the
single-file enum + `_DIALECT_NAMES` map that `dialect.py` actually
implements.

---

## Phase 10 prioritization

- **P0** — **B1**: fix `ARCHITECTURE.md` §9 (signature, paths,
  scripts).
- **P0** — **B2**: reconcile README Scope with the example models;
  remove stale "deferred" bullets that the parser actually accepts
  via `FoundationFlags`.
- **P0** — **B3**: add `[project.scripts] osi = "osi.cli:main"` so
  the documented `osi …` command works after `pip install -e .`.
- **P1** — **I1**: surface `error.context` in CLI output (always when
  non-empty, or behind `--verbose`).
- **P1** — **I2**: re-export the documented happy-path symbols from
  `osi/__init__.py`.
- **P1** — **I4**: ship at least one runnable example (model + query
  + expected SQL).
- **P1** — **I5**: README cross-link to `FoundationFlags`.
- **P2** — **I3 / I6 / N1–N3**: docs hygiene + planning surface split.

---

**Honest closing.** The implementation reads like a serious reference
compiler with excellent testing docs and diagnostics internals. The
adopter friction is mostly **packaging/docs drift** (missing scripts,
wrong argument order, phantom files, contradictory scope notes) and
**CLI error ergonomics** (dropping structured context). Fixing the P0
items would materially lower time-to-first-SQL for a BI engineer
cloning cold.
