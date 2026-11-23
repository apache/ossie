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

# Portability fidelity grader

`grade.py` is the scoring half of apache/ossie-converters#24 (formerly #576).
Conformance (`validate.py` passing) is a floor and says nothing about whether a
model survived a converter round-trip; this grader is the fidelity half.

It does not drive the converters. It takes the input document and the document a
converter returned, and diffs them element by element into the outcome vocabulary
in `../manifest.yaml`. A thin runner (modelled on `cube/tools/interop_matrix.py` in
apache/ossie-converters) drives each converter and feeds the
grader, so the measurement table becomes one command.

Elements are metrics and field expressions. A model-scoped metric is keyed by its
name, a dataset-scoped metric (`datasets[].metrics`) and a field by `dataset.name`,
the way the spec references them. A metric a converter moves between the two scopes
with the same expression is graded REWRITTEN (identity not preserved), not PORTABLE.

## Usage

```
python grade.py --input model.ossie.yaml --returned returned.ossie.yaml \
    --converter dbt [--diagnostics warnings.txt] [--json]
```

## Outcomes

PORTABLE (with a TAG_PRESERVED sub-flag), REWRITTEN, SURVIVED_VENDOR_ONLY,
PLACEHOLDER, ADDED, RELABELED, DROPPED_DIAGNOSED, DROPPED_SILENT, REJECTED.

## Keys and relationships

The grader also collects `key` and `relationship` elements (#693). These findings
compare declarations; they do not prove that source data satisfies a key or that
a converter's native joins execute correctly. Validate documents before grading.

| Declaration | Element name | Comparison |
|---|---|---|
| Primary key | `dataset.primary_key` | Dataset, primary role, and column set |
| Alternate key | `dataset.unique_keys["column1","column2"]` | Dataset, unique role, and column set; column names are sorted and JSON-quoted |
| Relationship | Its declared name | From/to datasets and the set of complete `(from_column, to_column)` pairs |

Reordering columns within a key, alternate keys within their list, or complete
join pairs is `PORTABLE`. Reordering only one side of a compound join is
`REWRITTEN`: it changes which columns are compared. Key roles and compound
grouping matter: changing primary to unique, or splitting one compound unique
key into independent keys, is `REWRITTEN`, with a review detail.

Changed primary keys and relationships retain stable names. A renamed
relationship with an identical declaration, a key-role change, or a changed
alternate key is paired only when the match is unambiguous within its scope.
Ambiguous changes remain separate drops and additions. No key is matched across
datasets, and expression matching never repairs a structural declaration.

A replacement single-column key is normally `REWRITTEN`, including truncations.
To grade a surrogate as `PLACEHOLDER`, the diagnostics must identify the original
element name, the returned column, and the word `surrogate` on the same line, e.g.:

```text
customer_sales_area.primary_key replaced by surrogate area_id
```

This records a diagnosed stand-in, not proof of equivalent identity. Dropped
structural elements are `DROPPED_DIAGNOSED` only when diagnostics name the scoped
element (the relationship name for a relationship); otherwise `DROPPED_SILENT`.
Additional declarations are `ADDED`. The existing expression outcomes are unchanged.

The independent K0 fixture in `../k0/` exercises a four-part customer-sales-area
primary key, an alternate key, a fact primary key, and a compound relationship.
Its `elements_under_test` entries in the manifest use the names above.

## Design

- **Standard library for the diff.** `sqlglot` (already used by the converters
  and `validate.py`) is used when available to compare normalized expression
  trees, so a REWRITTEN that is only a requalify, an unquote or a reorder is
  marked cosmetic, while one it cannot prove equivalent is flagged for review.
  Without `sqlglot` the grader still runs and marks every rewrite as needing
  review.
- **A qualifier is checked against the model.** Before trees are compared, each
  column is resolved to the dataset it names: a bare name in a field expression
  or dataset-scoped metric is the element's own dataset, elsewhere it is the one
  dataset that declares it. A requalify that names the same dataset is cosmetic;
  one that names another dataset (`customers.amount` for `sales.amount`) needs
  review.
- **Textual by default, semantic where it can be.** PORTABLE means the converter
  returned the same expression it was given (modulo cosmetic whitespace). When
  the input has an `OSSIE_SQL_2026` form (`sql`, or the dialect entry when there
  is no `sql`), that text must survive (under `OSSIE_SQL_2026` or an identical
  portable sibling); a differing ANSI_SQL sibling alone is not PORTABLE. A
  returned `OSSIE_SQL_2026` form whose text differs from the authored one is
  REWRITTEN even when the input had only an ANSI_SQL entry and that entry came
  back unchanged. True semantic equivalence of SQL is undecidable in general, so
  the grader auto-clears only the changes a normalized tree proves equal and
  leaves the rest for a human.
- **NULL placement in a window `ORDER BY` follows the authored tag.** sqlglot's
  base dialect sorts NULL as the smallest value, the opposite of the
  `OSSIE_SQL_2026` default (#587). An `OSSIE_SQL_2026` expression is compared
  with NULL as the largest value, so making the default explicit (`DESC NULLS
  FIRST`) is cosmetic and flipping it (`DESC NULLS LAST`) needs review. An
  `ANSI_SQL` expression leaves the default to the engine, so an omitted and an
  explicit placement are only cosmetic when they agree under both orderings.
- **REJECTED means no document came back.** A valid document with no elements is a
  return, so each element is DROPPED (diagnosed or silent).
- **Identity beyond name.** A dropped input element and an added output element
  that carry an equivalent expression are paired as one element whose identity
  (name) was not preserved, rather than scored as a drop plus an unrelated add.
- **Diagnosed vs silent, and PLACEHOLDER, need the converter's warnings.** Pass
  them with `--diagnostics`.

## Tests

`test_grade.py` has one test per outcome plus an identity check that runs the real
M0-M8 fixtures through a no-op converter and asserts every element grades
PORTABLE. Two manifest checks assert that every element the manifest names is found
by kind and name in each variant, and that each primary variant uses the rung's primary
reference form.

`test_structure.py` covers structural changes, ambiguity, scoped
diagnostics, CLI JSON output, and the K0 fixture. Run from the repository root:

```sh
uv run --with pytest --with pyyaml --with sqlglot -m pytest compliance/portability/grader/
uv run validation/validate.py compliance/portability/k0/k0_compound_keys.ossie.yaml
```
