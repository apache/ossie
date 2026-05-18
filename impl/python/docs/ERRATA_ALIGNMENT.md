# ERRATA_ALIGNMENT.md — How `osi_python` Handles Known BI-Engine Surprises

The Foundation (see `../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md §12`) was
explicitly designed to be free of the surprising behaviors catalogued
in Snowflake Semantic Views' `ERRATA.md`. This document:

1. Lists each errata item that is relevant to our Foundation.
2. States our compliance target: **resolved**, **deferred**, or **inherited**.
3. Points to the test(s) that prove the outcome.

Use this as the seed catalog when writing new test scenarios — every
errata item should either have at least one test that exercises it or
a documented reason it doesn't apply.

---

## Legend

- **Resolved.** The Foundation spec defines the correct behavior and
  `osi_python` implements it; the surprising Snowflake behavior does not
  occur.
- **Deferred.** The surprising behavior arises from a feature that is
  out of scope for the Foundation; we are not affected because we don't
  support that feature yet.
- **Inherited.** The behavior is a property of the underlying SQL dialect
  and `osi_python` surfaces it rather than papering over it. Documented
  for caller awareness.

---

## Catalog

References to errata numbers match `snowflake-prod-docs/tests/semantic-views/ERRATA.md`
as summarized in `Proposed_OSI_Semantics.md §12.2` / `§12.3`.

### Cardinality & aggregation

| # | Summary | Disposition | Spec anchor | Test |
|:--|:---|:---|:---|:---|
| 1 | Different aggregation granularities in a single SELECT. | **Resolved** — all measures in one query resolve at the query grain; bare-view cross-grain aggregates raise `E1209`. | `Proposed_OSI_Semantics.md §5.2`, future SQL_INTERFACE proposal §6.3 | `tests/e2e/test_single_grain_per_query.py` |
| 2 | Dimension-only queries produce different cardinalities on different interfaces. | **Resolved** — both the clause form and bare view apply `DISTINCT(Dimensions)`. | `Proposed_OSI_Semantics.md §5.2`, future SQL_INTERFACE proposal §5.4 | `tests/properties/test_dimension_only_cardinality.py` |
| 3 | Implicit `DISTINCT` behavior divergence. | **Resolved** — same rule as #2; explicit projection, explicit aggregation. | `Proposed_OSI_Semantics.md §5`, future SQL_INTERFACE proposal §5.4 | `tests/golden/basic/dimension_only/` |
| 4 | `COUNT(*)` not supported. | **Resolved** — `COUNT(*)` is REQUIRED; ambiguous usage raises `E1212`. | `Proposed_OSI_Semantics.md §7`, future SQL_INTERFACE proposal §5.2, §6.2 | `tests/unit/planning/test_count_star.py` |
| 5 | `SELECT *` on a bare-view query fails with metadata-internals error. | **Resolved** — `SELECT *` is rejected with a clear `E1208` pointing to `DIMENSIONS dataset.*`. | Future SQL_INTERFACE proposal §6.1 | `tests/unit/sql/test_reject_select_star.py` |
| 8 | `FACTS` + `METRICS` cannot coexist. | **Resolved** — kept as `E1207` with an explanatory message. | Future SQL_INTERFACE proposal §5.3 | `tests/unit/sql/test_facts_metrics_exclusive.py` |
| 9 | Inner `LIMIT` is a cryptic syntax error. | **Resolved** — `E1211` with an outer-`LIMIT` suggestion. | Future SQL_INTERFACE proposal §3.2 | `tests/unit/sql/test_clause_only_outer.py` |
| 10 | Outer `WHERE` must use result aliases, not semantic names. | **Resolved** — formalised as output-column scoping rule. | Future SQL_INTERFACE proposal §4.3 | `tests/unit/sql/test_outer_scope.py` |

### Naming & ambiguity

| # | Summary | Disposition | Spec anchor | Test |
|:--|:---|:---|:---|:---|
| 16 | Same-named expressions across datasets unreachable in standard SQL. | **Resolved** — `dataset.field` qualification MUST be accepted; duplicate unqualified output columns raise `E1205`; ambiguous bare references raise `E1204`. | `Proposed_OSI_Semantics.md §4.7`, future SQL_INTERFACE proposal §4.1, §4.2 | `tests/unit/parsing/test_namespace_collisions.py`, `tests/unit/sql/test_duplicate_output_columns.py` |
| 17 | Same-named metrics across tables hit the same trap. | **Resolved** — same rule as #16; aliasing required. | Future SQL_INTERFACE proposal §4.2 | `tests/unit/sql/test_duplicate_metric_names.py` |

### Window functions

| # | Summary | Disposition | Spec anchor |
|:--|:---|:---|:---|
| 18–23 | Window-function complexities (`QUALIFY`, framing, cardinality with OVER). | **Deferred** — windows are not in the Foundation. | `Proposed_OSI_Semantics.md §10` |

Window-related tests do not exist in `osi_python`. Attempts to use window
functions in a metric expression raise `E1105 RESERVED_FOR_DEFERRED`.

### Grain / filter features

| # | Summary | Disposition | Spec anchor |
|:--|:---|:---|:---|
| 6–10 | FIXED / INCLUDE / EXCLUDE edge cases. | **Deferred** — LOD is not in the Foundation. | `Proposed_OSI_Semantics.md §10` |
| 11–13 | Filter context propagation surprises. | **Deferred** — filter context is not in the Foundation. | `Proposed_OSI_Semantics.md §10` |

Metric and query definitions that reference these features raise `E1105`
at parse time.

### Join & relationship behavior

| # | Summary | Disposition | Spec anchor | Test |
|:--|:---|:---|:---|:---|
| 14 | Ambiguous join path silently picks one. | **Resolved** — ambiguous paths raise `E3001` unless `using_relationships` disambiguates. | `Proposed_OSI_Semantics.md §6.6` | `tests/unit/planning/test_ambiguous_path.py` |
| 15 | Fan-out produces silently wrong sums. | **Resolved** — algebra refuses unsafe aggregations, raises `E4001`. | `JOIN_ALGEBRA.md §5.1`, `JOIN_SAFETY.md` | `tests/properties/test_explosion_safety.py` |
| 24 | Chasm trap: two facts joined through a shared dim double-count. | **Resolved** — planner decomposes to per-fact states + `merge` at shared grain; otherwise raises `E3010`. | `JOIN_ALGEBRA.md §5.2` | `tests/properties/test_chasm_safety.py`, `tests/e2e/test_chasm_trap.py` |

### Dialect-inherited behaviors

| # | Summary | Disposition | Spec anchor | Notes |
|:--|:---|:---|:---|:---|
| 25 | NULL handling in `NOT IN` subquery on nullable column. | **Inherited** — Foundation uses `EXISTS_IN` / `NOT EXISTS_IN` which are NULL-safe by construction, but if a caller drops to raw SQL via `WHERE` string, dialect NULL semantics apply. | `Proposed_OSI_Semantics.md §7` | Document in caller guide. |

---

## Adding a New Errata Item

When an upstream BI engine publishes a new surprising-behavior item, or
we discover one on our own:

1. Add a row in the appropriate section with the summary and disposition.
2. If the disposition is **Resolved**, add at least one test and link it.
3. If the disposition is **Deferred**, confirm that `E1105 RESERVED_FOR_DEFERRED`
   fires for models/queries that trigger the feature.
4. If the disposition is **Inherited**, capture the note locally (the SQL_Caller_Examples surface will land with the future SQL_INTERFACE proposal)
   so callers are aware.

An errata item we cannot map to one of the three dispositions is a
**hole in the Foundation**, not an implementation bug. Escalate to a
spec update before writing code.
