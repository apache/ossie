# Readability & Correctness Review — impl/python/src/osi

## Executive summary

The codebase is generally strong on layering, frozen value types, and attaching **`ErrorCode`** to **`OSIError`** subclasses. The largest correctness tension is **surface area shipped as "Foundation" in code (notably `EXISTS_IN` semi-joins and partial M:N bridge support) while `Proposed_OSI_Semantics.md` still lists semi-joins as deferred and mandates full M:N aggregate families via §6.8.1 / D-027.** Documentation drift is frequent (**wrong decision IDs in user-facing strings**, stale **`E1105`** references, module docs that contradict behaviour). **`E_WINDOW_OVER_FANOUT_REWRITE` has no emit path in `src/`** (tracked as roadmap in `INFRA.md`), so D-030 fan-out safety for windows is not enforced in the compiler. Strengths: **`errors.py` + `error_catalog.py`** as a deliberate contract, **`planning/planner_bridge.py`** honesty about D-027 gaps, **`algebra/operations.py`** clear operator contracts, and **dense unit tests** under `tests/unit/` for parsing, classify, joins, and codegen.

## Findings by module

### `errors.py` and `diagnostics/`

**F-1**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/errors.py:164-254`  
- **Observation:** Enum mixes **named** Foundation codes (**`E_DEFERRED_KEY_REJECTED`**) with **legacy numeric** **`E1xxx`/`E2xxx`/`E3xxx`** retained for pinning.  
- **Impact:** New readers must learn two conventions; grep for "Foundation Appendix C" sometimes finds only the named slice.  
- **Suggested fix:** Keep as-is for compatibility but add a one-paragraph "naming policy" in `docs/ERROR_CODES.md` pointing to migration status (already partly in docstrings).

**F-2**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/diagnostics/error_catalog.py:27-450` (large `_EXPLANATIONS` dict)  
- **Observation:** Single massive mapping; no per-code helpers.  
- **Impact:** Harder to navigate than small grouped modules; still acceptable because tests enforce completeness.  
- **Suggested fix:** Optional split by family (`catalog_parse.py`, `catalog_planning.py`) only if file exceeds maintenance comfort.

**F-3**  
- **Severity:** P1  
- **Category:** Correctness / Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/errors.py:116` vs `rg E_WINDOW_OVER_FANOUT` under `impl/python/src/` → **no matches outside `errors.py`**  
- **Observation:** **`E_WINDOW_OVER_FANOUT_REWRITE`** is defined and documented but **never raised** from planning/codegen.  
- **Impact:** Spec §6.2 step 10 and **D-030** (*Proposed_OSI_Semantics.md* §6.2 / Appendix B) require this failure mode when a safe pre-fan-out rewrite is unavailable; absence is a **spec gap**.  
- **Suggested fix:** Implement fan-out detection in the window plan path or explicitly document engine deviation; `INFRA.md` I-43 already hints this is unfinished.

**Spec cross-ref (F-3):** *"Windows whose home dataset would be fanned out by the plan raise `E_WINDOW_OVER_FANOUT_REWRITE` (D-030) unless the engine materialised the home grain before applying the window."* (§6.2 compilation algorithm, ~line 671 in spec file searched).

---

### `common/`

**F-4**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/common/windows.py:8-11`  
- **Observation:** Module doc says **`first_nested_window` … (`D-031`)**; **D-031** in the spec is **`E_WINDOWED_METRIC_COMPOSITION`**, while **nested windows** are **D-028(c)** / **`E_NESTED_WINDOW`**.  
- **Impact:** Mis-trains readers and contradicts Appendix B.  
- **Suggested fix:** Replace **D-031** with **D-028(c)** in this docstring.

**Spec cross-ref (F-4):** Appendix B row **D-028** includes *(c)* nested-window parse-level rejection; **D-031** is metric referencing windowed metric (*Proposed_OSI_Semantics.md* ~1987-1990).

**F-5**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/common/windows.py:17-22`  
- **Observation:** Says positive planner lives in **`planner_windows.py`** — **no such file** under `planning/`.  
- **Impact:** Dead reference; confusion when searching the tree.  
- **Suggested fix:** Point to actual modules (`planner_scalar.py` window splitting, etc.) or `INFRA.md` I-43.

**F-6**  
- **Severity:** P2  
- **Category:** Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/common/sql_expr.py:89-92`  
- **Observation:** **`noqa: D105`** on magic methods — consistent with "short dunder doc" style.  
- **Impact:** Minor; public surface still discoverable.  
- **Suggested fix:** None required.

---

### `parsing/`

**F-7**  
- **Severity:** P2  
- **Category:** Readability / Correctness (docs)  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/parsing/models.py:10-13`  
- **Observation:** Docstring says deferred features produce **`E1105`** / **`E_DEFERRED_KEY_REJECTED`** mixture; **`E1105`** is not an **`ErrorCode`** in `errors.py` (implementation uses **`E_DEFERRED_KEY_REJECTED`**).  
- **Impact:** Stale spec reference; confusing for auditors.  
- **Suggested fix:** Delete **`E1105`** mention; align with `ErrorCode.E_DEFERRED_KEY_REJECTED`.

**F-8**  
- **Severity:** P2  
- **Category:** Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/parsing/models.py:341`  
- **Observation:** **`default: Any = None`** on a Pydantic field.  
- **Impact:** Weakens precision; may be intentional for free-form `parameters`.  
- **Suggested fix:** Narrow to `object` or a `TypedDict` if structure is known.

**F-9**  
- **Severity:** P1  
- **Category:** Types / Correctness  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/parsing/deferred.py:192-200`, `348-372`  
- **Observation:** **`check_yaml_deferred`** and helpers take **`Any`**; **`_unwrap_walk_item`** returns **`exp.Expression()`** on unexpected walk shapes (`361-372`).  
- **Impact:** Silent "benign" node might **skip** deferred AST detection for odd SQLGlot walk tuples.  
- **Suggested fix:** Log/internal-invariant assert or **`E_INTERNAL_INVARIANT`** when walk shape is unknown.

**F-10**  
- **Severity:** P2  
- **Category:** Single-responsibility  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/parsing/deferred.py:1-384`  
- **Observation:** Single module owns YAML key sets, SQL AST bans, and window pre-rules — coherent "deferred gate" but large.  
- **Impact:** Acceptable; boundary is clear.  
- **Suggested fix:** None unless file grows further.

**F-11**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/parsing/parser.py:56-90`  
- **Observation:** Clear ordered pipeline with explicit error codes in docstring — matches `ARCHITECTURE.md`.  
- **Impact:** Positive.  
- **Suggested fix:** Preserve.

**F-12**  
- **Severity:** P2  
- **Category:** Test-coverage  
- **Location:** `tests/unit/parsing/test_deferred.py` vs `deferred.py`  
- **Observation:** Deferred YAML paths are covered; expression paths exercised via other tests.  
- **Impact:** Reasonable signal.  
- **Suggested fix:** Add explicit test for `_unwrap_walk_item` tuple vs non-tuple if SQLGlot upgrades.

---

### `planning/`

**F-13**  
- **Severity:** P0  
- **Category:** Correctness vs spec  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/classify.py:366-386`; tests `tests/unit/planning/test_classify.py:75-123`, `tests/unit/planning/test_planner.py:142-157`; `tests/e2e/test_cardinality_safety.py:382+`  
- **Observation:** **`EXISTS_IN` / `NOT_EXISTS_IN`** are **first-class** (semi-join predicates + **`FILTERING_JOIN`**).  
- **Impact:** **Contradicts Foundation spec** — *"Semi-join filtering (`EXISTS_IN` / `NOT_EXISTS_IN`) is deferred… not part of the Foundation today"* (conformance checklist **§6.12** ~line **1348**); **D-017** marked deferred (~**1976**). Codebase treats EXISTS_IN as a supported M:N escape hatch (e.g. `test_joins.py` docstring).  
- **Suggested fix:** Either update **normative spec / decision archive** to "in scope for this implementation" or reject **`EXISTS_IN`** at query classification with **`E_DEFERRED_KEY_REJECTED`** for strict Foundation mode.

**Spec cross-ref (F-13):** *"12. (Reserved — see deferred EXISTS_IN proposal.) Semi-join filtering … is deferred …"* (`Proposed_OSI_Semantics.md` ~1348).

**F-14**  
- **Severity:** P1  
- **Category:** Correctness (diagnostics)  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/classify.py:123-141`  
- **Observation:** **`E_WINDOW_IN_WHERE`** message cites **"(D-030)"**; **D-030** is **`E_WINDOW_OVER_FANOUT_REWRITE`**, not window-in-where. **E_WINDOW_IN_WHERE** maps to **D-028** in Appendix C (~2041).  
- **Impact:** Users following decision IDs get wrong normative reference.  
- **Suggested fix:** Replace **D-030** → **D-028** in docstring and error string.

**F-15**  
- **Severity:** P1  
- **Category:** Readability (docs contradict code)  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/planner.py:36-40`  
- **Observation:** States **window functions** are out-of-scope / deferred in planner; parser and **`planner_scalar`** paths **accept** windowed metrics/fields; **`test_window_planner.py`** documents D-028/D-030 behaviour.  
- **Impact:** Module contract at top is **stale / misleading**.  
- **Suggested fix:** Reword: windows are partially supported (parse + scalar branch); aggregation measures with window roots still misaligned with §6.10 unless separately documented (see F-16).

**F-16**  
- **Severity:** P0  
- **Category:** Correctness vs spec  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/metric_shape.py:136-158`, `77-101`; `tests/unit/planning/test_window_planner.py:132-150`  
- **Observation:** **`classify_metric`** recognises only **AggFunc** roots or **composite** metric refs. A metric whose body is **`ROW_NUMBER() OVER (...)`** (**`exp.Window`**) is **not** an aggregate at root → composite path → **`E1206`** unless it only references other metrics. Parser **accepts** windowed metric YAML (`test_model_with_windowed_metric_parses`).  
- **Impact:** **§6.10** expects windows in **`Measures`**; **§5.4** / metric classifier does not model **window-as-measure** for aggregation queries.  
- **Suggested fix:** Extend **`MetricShape`** with a windowed branch or reject windowed metrics at parse with a dedicated code until planner supports them.

**Spec cross-ref (F-16):** *"Support standard SQL window functions … in `Measures`, `Fields`, `Order By`, and `Having`"* (conformance §6.12 ~1347).

**F-17**  
- **Severity:** P0  
- **Category:** Correctness vs spec (known gap, well-documented)  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/planner_bridge.py:20-29`, `207-214`, `237-251`  
- **Observation:** **AVG / holistic** over M:N bridge **`E_UNSAFE_REAGGREGATION`** / "pending"; spec **§6.8.1** / **D-027** says **every aggregate category** is well-defined on deduped `(measure-home-row, group-key)` set (~line **280**).  
- **Impact:** Reference implementation **under-ships** normative M:N behaviour; errors are honest.  
- **Suggested fix:** Complete bridge lowering for **`AVG`** / holistic consistent with D-027 or obtain explicit decision variance in Appendix B.

**Spec cross-ref (F-17):** *"`M : N` cross-grain references … accepted for every aggregate category … The contract is set-theoretic"* (§4.5 / §6.8 context ~**280**).

**F-18**  
- **Severity:** P1  
- **Category:** Correctness (misleading remediation)  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/planner_bridge.py:225-231`  
- **Observation:** Ambiguous bridge error tells users **`joins.using_relationships`** — that key is **`DEFERRED_METRIC_KEYS`** in **`deferred.py:59-63`**.  
- **Impact:** Suggest API that **`check_yaml_deferred`** rejects.  
- **Suggested fix:** Point to supported disambiguation (model structure / bridge dataset / future flag) per actual Foundation surface.

**F-19**  
- **Severity:** P1  
- **Category:** Single-responsibility  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/planner_bridge.py:314-410`  
- **Observation:** **`build_bridge_plan`** fuses graph logic, grain choice, aggregate column construction, and algebra calls (~**100+** LOC in one function).  
- **Impact:** Harder to test in isolation; still readable due to numbered steps.  
- **Suggested fix:** Extract "pre-agg grain / key validation" helpers (already partly separate).

**F-20**  
- **Severity:** P2  
- **Category:** Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/algebra/state.py:181` — **`type: ignore[func-returns-value]`**  
- **Observation:** Workaround for `seen.add` in a comprehension.  
- **Impact:** Minor smell.  
- **Suggested fix:** Replace with a small explicit loop.

**F-21**  
- **Severity:** P1  
- **Category:** Readability / contract  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/resolve.py:7-9`, `70-71`  
- **Observation:** Header claims failures **`E2xxx`** only; **codes include `E1206`, `E1207`**, **`E2002`**, etc.  
- **Impact:** Misleads maintainers.  
- **Suggested fix:** Say "raises **`OSIPlanningError`** with **`ErrorCode`** from parse/planning families".

**F-22**  
- **Severity:** P2  
- **Category:** Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/resolve.py:183-185`  
- **Observation:** **`assert`** for qualified ref invariant → **`AssertionError`** if violated (no **`ErrorCode`**).  
- **Impact:** Should be unreachable; if hit, breaks "all failures are **`OSIError`**" story.  
- **Suggested fix:** Replace with **`E_INTERNAL_INVARIANT`**.

**F-23**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/semantic_query.py:14-17`  
- **Observation:** Claims deferred constructs raise **`E1105`** — not in enum.  
- **Impact:** Same drift as **`models.py`**.  
- **Suggested fix:** Align wording with actual constructor validation.

**F-24**  
- **Severity:** P2  
- **Category:** Test-coverage  
- **Location:** No `tests` grep hits for **`find_bridge_resolutions`** / **`build_bridge_plan`** symbols  
- **Observation:** Bridge logic likely covered **indirectly** via planner/e2e, not unit-named.  
- **Impact:** Regressions may be harder to localize.  
- **Suggested fix:** Add focused **`test_planner_bridge.py`** cases for **`can_apply_bridge_resolution`** and ambiguity.

**F-25**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/algebra/__init__.py` (lines ~25-26 per grep)  
- **Observation:** Package doc references **`filtering_join` for `EXISTS_IN`**.  
- **Impact:** Couples algebra public narrative to a **spec-deferred** surface (see F-13).  
- **Suggested fix:** If EXISTS_IN stays, update spec; if not, soften wording.

**F-26**  
- **Severity:** P1  
- **Category:** Single-responsibility / error messaging  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/planner_scalar.py:192-197`  
- **Observation:** Scalar path rejects semi-joins with **`E_AGGREGATE_IN_SCALAR_QUERY`** and message "convert to aggregation".  
- **Impact:** Wrong code for "feature not in scalar shape"; if semi-join is itself deferred Foundation-wide, should be **`E_DEFERRED_KEY_REJECTED`** instead.  
- **Suggested fix:** Split error: **`E_DEFERRED_KEY_REJECTED`** or dedicated code for scalar+semi-join.

**F-27**  
- **Severity:** P2  
- **Category:** Types  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/classify.py:424-428`  
- **Observation:** **`except Exception`** wrapping identifier normalisation.  
- **Impact:** May hide **`KeyboardInterrupt`** in theory; broad catch is discouraged.  
- **Suggested fix:** Catch **`ValueError`** / **`OSIParseError`** only.

### `planning/algebra/`

**F-28**  
- **Severity:** P2  
- **Category:** Strength / readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/algebra/operations.py:1-185`  
- **Observation:** Strong operator contracts; **`filter_`** identity-on-state documented (**`149-194`**).  
- **Impact:** Makes algebra laws teachable.  
- **Suggested fix:** Preserve.

**F-29**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/planning/algebra/grain.py:1-35`, `148-162`  
- **Observation:** Excellent explanation of **`single_valued`** vs **`grain`**; **`GrainSimulationError`** maps to **`E_INTERNAL_INVARIANT`**.  
- **Impact:** Good bridge from docs to tests.  
- **Suggested fix:** None.

---

### `codegen/`

**F-30**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/codegen/transpiler.py:1-90`  
- **Observation:** Clear layering boundary ("does not read SemanticModel").  
- **Impact:** Matches `ARCHITECTURE.md`.  
- **Suggested fix:** Keep enforcement via import-linter.

**F-31**  
- **Severity:** P2  
- **Category:** Test-coverage  
- **Location:** `tests/unit/codegen/test_transpiler.py`, `test_dialect.py`, `test_cte_optimizer.py`  
- **Observation:** Suite exists for codegen.  
- **Impact:** Positive signal.  
- **Suggested fix:** Add cases when new **`PlanOperation`** variants appear.

---

### `cli.py`

**F-32**  
- **Severity:** P2  
- **Category:** Types / contracts  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/cli.py:60-92`  
- **Observation:** **`_load_query`** uses untyped **`dict`** access; **`_ref`** assumes keys exist (`KeyError` risk).  
- **Impact:** CLI input errors are **raw Python** exceptions, not **`OSIError`**.  
- **Suggested fix:** Validate shape; map to **`E1004`** / **`E1002`**.

**F-33**  
- **Severity:** P2  
- **Category:** Correctness  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/cli.py:74-76` — **`sqlglot.parse_one`**, no **`check_expression_deferred`**  
- **Observation:** Query JSON **`where`** bypasses same deferred SQL screens as model parse.  
- **Impact:** Pivot / grouping sets / deferred functions might only fail later or differently than YAML path; semi-join **`EXISTS_IN`** is *allowed* (F-13).  
- **Suggested fix:** Run shared expression validation on query slots.

**F-34**  
- **Severity:** P2  
- **Category:** Readability  
- **Location:** `/Users/wpugh/projects/OSI_will/impl/python/src/osi/cli.py:197-207`  
- **Observation:** **`_resolve_error_code`** raises **`KeyError`** internally; caught and turned into exit code 2.  
- **Impact:** Acceptable for CLI; not part of library **`OSIError`** contract.  
- **Suggested fix:** Optional: use a tiny typed lookup table.

---

## Cross-cutting findings

**C-1:** **Decision ID drift** (D-028 vs D-030 vs D-031) in **`classify.py`**, **`common/windows.py`**, **`test_window_planner.py`** — undermines Appendix B as a shared language.

**C-2:** **Stale `E1105` textual references** in **`models.py`**, **`semantic_query.py`** vs actual **`ErrorCode`**.

**C-3:** **`ARCHITECTURE.md`** (Layer 1) aligns with **`parser.py`**; **`planning/classify.py` + tests** implement **EXISTS_IN** beyond current **§10** checklist — **spec and repo disagree** until decision archive catches up.

**C-4:** **High-risk spec areas** (M:N bridge completeness, window fan-out **`E_WINDOW_OVER_FANOUT_REWRITE`**, windowed **measure** planning) have **known gaps** documented in **`planner_bridge`**, **`INFRA.md`**, **`errors.py`** comments — good transparency, incomplete vs normative text.

## Recommended sprint backlog

1. **P0:** Resolve **EXISTS_IN** vs **Foundation §10 / D-017** (spec update or strict rejection / flag).  
2. **P0:** Implement or formally defer **D-030** **`E_WINDOW_OVER_FANOUT_REWRITE`** in planner (not only catalog).  
3. **P0:** **Windowed metrics in `Measures`** — extend **`metric_shape` / planner** or reject at parse with precise code (**F-16**).  
4. **P0:** Close **M:N non-distributive bridge** gap or record **Appendix B** variance (**F-17**).  
5. **P1:** Fix **D-xxx** references in **`E_WINDOW_IN_WHERE`** diagnostics (**F-14**); remove **`using_relationships`** suggestion (**F-18**).  
6. **P1:** Refresh **`planner.py`** header (**F-15**); **`resolve.py`** doc (**F-21**); **`semantic_query` / models** **`E1105`** (**F-7, F-23**).  
7. **P1:** Scalar semi-join error code (**F-26**).  
8. **P2:** CLI query validation parity (**F-33**); narrow **`except Exception`** (**F-27**); bridge unit tests (**F-24**).

## Strengths to preserve

- **`errors.py`**: explicit **reserved vs active** commentary and **`E_INTERNAL_INVARIANT`** for true invariant failures.  
- **`planning/planner_bridge.py`**: transparent **D-027** gap statement (**lines 20-29**) instead of silent wrong answers.  
- **`planning/algebra/operations.py` + `grain.py`**: teachable contracts aligned with **`JOIN_ALGEBRA.md`**.  
- **`parsing/parser.py`**: ordered pipeline doc matches **`ARCHITECTURE.md`**.  
- **`tests/unit/`**: broad coverage for **classify**, **joins**, **planner**, **deferred**, **codegen**.

---

## Five-line handoff

Overall health is **good engineering discipline with a few P0 spec mismatches** (semi-join vs §10, window measure planning, missing **D-030** emit path, partial **D-027** bridge).

**Top P0s:**

1. `EXISTS_IN` vs deferred Foundation §10 / D-017.
2. No `E_WINDOW_OVER_FANOUT_REWRITE` emit path in `src/` (D-030).
3. Windowed body metrics + aggregation planner gap.

**Top strengths:** `errors` / `error_catalog` contract, honest bridge-gap docs in `planner_bridge.py`, algebra operator clarity in `operations.py` / `grain.py`, strong unit tests around classify / joins / parser.
