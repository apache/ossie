# Code Review — Phase 10a — Standards Expert

Audit target: `/Users/wpugh/projects/OSI_will/` — does `impl/python/` exactly realise the normative text in `proposals/foundation-v0.1/`?

## Summary table (verdict per angle)

| Angle | Verdict | Notes |
|------|---------|--------|
| 1. Appendix C vs `ErrorCode` + drift test | **PARTIAL — spec/contract gap** | Enum + drift test align with each other; normative Appendix C omits at least one actively raised code (`E_UNKNOWN_FUNCTION`); drift test's "verbatim" claim is inaccurate. |
| 2. Decision pinning (D-001..D-033) | **WEAK** | `decisions.yaml` paths/names often do not match the tests tree on disk; several suite tests are `planned`/xfail; D-022 pinning is explicitly incomplete in YAML. |
| 3. Dialect vs `SNOWFLAKE_DIVERGENCES.md` | **MOSTLY ALIGNED** | Core NULL-order behaviour is implemented in codegen transpiler, not `dialect.py`; Snowflake-specific divergence is mostly "no special-case needed" per SD-2. |
| 4. `osi_version` | **NOT ALIGNED** | No `osi_version` field on `SemanticModel`; with `extra="forbid"`, a spec-allowed key is treated as forbidden extra → wrong failure mode vs §opening. |
| 5. SQL_EXPRESSION_SUBSET vs whitelist | **MOSTLY ALIGNED** | Whitelist tracks the subset structure; **normative tension**: §10 defers ordered-set `WITHIN GROUP` including `PERCENTILE_CONT` while the subset still lists `PERCENTILE_CONT` as required. |
| 6. Deferred §10 vs `deferred.py` + tests | **PARTIAL** | Many keys/ASTs covered; not every §10 row maps 1:1 to a **must_pass** negative test; several deferred tests are `planned` with xfail. |
| 7. Error catalog actionability | **GOOD with doc bugs** | Entries generally cite spec areas and fixes; multiple stale `SQL_EXPRESSION_SUBSET_updated.md` references. |
| 8. `make check` / `pyproject.toml` | **STRONG** | Strict mypy, import-linter contracts, black/isort/flake8, LOC cap via Makefile + pre-commit. |

---

## Blocking findings `[10a]`

### B1 `E_UNKNOWN_FUNCTION` vs Appendix C exhaustiveness

Appendix C declares the index exhaustive and forbids undocumented codes
(`Proposed_OSI_Semantics.md` ~L2047). `E_UNKNOWN_FUNCTION` is raised by
the implementation (`src/osi/errors.py` L117–L122; emitted by
`function_whitelist.py`) and documented in the catalog
(`error_catalog.py` L254–L262) but **does not appear in Appendix C**.

Either add `E_UNKNOWN_FUNCTION` (and its D-021 / subset pointer) to
Appendix C, or fold unknown-function rejections into an existing
Appendix C code. Until then, "Appendix C is exhaustive" and "the
implementation is conforming" cannot both be true.

### B2 `osi_version` is forbidden by the parser

The spec opening (`Proposed_OSI_Semantics.md` L8) admits an optional
`osi_version: "0.1"` field on `SemanticModel`. The Pydantic model
`SemanticModel` (`src/osi/parsing/models.py` L350–L360) has no such
field; the strict base uses `extra="forbid"` (L143–L151), so an author
who follows the spec gets an `E1001_YAML_SYNTAX` "extra inputs not
permitted" error instead of "osi_version is supported" or
"unsupported osi_version". Add the field, validate the value, and
surface a clear error for `0.2+`.

---

## Important findings `[10a]`

### I1 Appendix C drift test claim vs its own contents

The drift test (`tests/unit/test_appendix_c_drift.py` L28–L31) says
codes are "extracted verbatim" from Appendix C, but includes
`E_UNKNOWN_FUNCTION` (L59) which is **not** in the spec's normative
table. Either remove the code from `_APPENDIX_C_CODES` or update the
test preamble to acknowledge it's a working-set, not a verbatim
extract.

### I2 `decisions.yaml` test paths do not match the suite on disk

`decisions.yaml` for D-029 cites tests like
`tests/null_ordering/easy/T-029a_outer_order_by_nulls_last/`, but the
filesystem has `t-026-nulls-last-default` and `t-062-nulls-first-
default-on-desc`. Metadata still pins D-029/D-014. The YAML registry
is not a reliable index of the filesystem tests.

The new Phase 6 / Phase 7 invariant tests in
`compliance/harness/.../test_registry_yaml.py` catch this — confirm
they fire on this drift, or extend them.

### I3 D-022 coverage gap is admitted in `decisions.yaml`

`decisions.yaml` (L172–L179) flags a missing positive
`E_UNSAFE_REAGGREGATION` witness for the holistic-over-chasm/stitch
shape. Currently only `t-021-count-distinct-fanout` is pinned.

### I4 Normative tension: §10 vs `SQL_EXPRESSION_SUBSET.md` on `PERCENTILE_CONT` / `WITHIN GROUP`

- §10 deferred table lists ordered-set `WITHIN GROUP` including
  `PERCENTILE_CONT` (`Proposed_OSI_Semantics.md` ~L1321).
- `SQL_EXPRESSION_SUBSET.md` (L201–L203) still marks
  `PERCENTILE_CONT … WITHIN GROUP (ORDER BY expr)` as required.
- The function whitelist (`function_whitelist.py` L54–L57) includes it.

Resolve in spec text: either `WITHIN GROUP` semantics are deferred or
they are part of Foundation; the subset and §10 must not disagree.

### I5 `parser.py` module docstring still cites obsolete `E1105`

`src/osi/parsing/parser.py` L11–L16. Replace with the modern
`E_DEFERRED_*` codes.

### I6 `error_catalog.py` cites a non-existent `SQL_EXPRESSION_SUBSET_updated.md`

`src/osi/diagnostics/error_catalog.py` L54–L57 and L345–L348. The
file is `SQL_EXPRESSION_SUBSET.md` (no suffix).

### I7 Deferred compliance tests often `planned` / xfail

The canonical deferred test (`t-042-deferred-key-rejection`,
metadata L14–L15) and ordered-set (`t-042v`, metadata L16–L17) are
`planned`. "Every §10 deferred feature has a must_pass negative test"
is not yet true; the default suite is 100% because these are
filtered out.

---

## Nits `[10a]`

### N1 Appendix C code column uses Python-style suffix

Appendix C lists `E3011_MN_AGGREGATION_REJECTED` while runtime
`error.code` is the string `"E3011"`. Add a one-sentence note that
`error.code` is the string value, not the Python identifier suffix.

### N2 D-032 wording allows either `E_DEFERRED_KEY_REJECTED` or `E_DEFERRED_FRAME_MODE`

`Proposed_OSI_Semantics.md` ~L1994 admits both. The impl consistently
uses `E_DEFERRED_FRAME_MODE` for `GROUPS`. Acceptable as long as
adopters know to match either.

---

## Phase 10 prioritization

- **P0** — **B1**: reconcile `E_UNKNOWN_FUNCTION` with Appendix C.
- **P0** — **B2**: accept `osi_version` per spec; reject unsupported
  versions with a precise error.
- **P1** — **I2**: bring `decisions.yaml` test paths into sync with
  the filesystem.
- **P1** — **I3**: add a `E_UNSAFE_REAGGREGATION` witness for the
  holistic-over-chasm shape (active test, not planned).
- **P1** — **I4**: resolve §10 vs subset on `PERCENTILE_CONT`.
- **P2** — **I5/I6/N1/N2**: doc/comment hygiene.

---

**Honest closing.** Where the artefacts line up, they are in good
shape: M:N numeric codes are pinned in the drift test
(`test_appendix_c_drift.py` L145–L167); D-029 NULL ordering is
implemented in the transpiler with an explicit spec comment
(`codegen/transpiler.py` L483–L500); `dialect.py` is intentionally
thin and delegates to SQLGlot (L42–L81), matching SD-1..SD-5. The
blocking issues are **Appendix C completeness**, **`osi_version`
schema**, and **registry/tests-path drift**, not a wholesale mismatch
of the architecture to the proposal.
