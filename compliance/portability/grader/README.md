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

`grade.py` is the scoring half of issue #576. Conformance (`validate.py` passing)
is a floor and says nothing about whether a model survived a converter round-trip;
this grader is the fidelity half.

It does not drive the converters. It takes the input document and the document a
converter returned, and diffs them element by element into the outcome vocabulary
in `../manifest.yaml`. A thin runner (modelled on `cube/tools/interop_matrix.py` in
apache/ossie-converters) drives each converter and feeds the
grader, so the measurement table becomes one command.

## Usage

```
python grade.py --input model.ossie.yaml --returned returned.ossie.yaml \
    --converter dbt [--diagnostics warnings.txt] [--json]
```

## Outcomes

PORTABLE (with a TAG_PRESERVED sub-flag), REWRITTEN, SURVIVED_VENDOR_ONLY,
PLACEHOLDER, ADDED, RELABELED, DROPPED_DIAGNOSED, DROPPED_SILENT, REJECTED.

## Design

- **Standard library for the diff.** `sqlglot` (already used by the converters
  and `validate.py`) is used when available to compare normalized expression
  trees, so a REWRITTEN that is only a requalify, an unquote or a reorder is
  marked cosmetic, while one it cannot prove equivalent is flagged for review.
  Without `sqlglot` the grader still runs and marks every rewrite as needing
  review.
- **Textual by default, semantic where it can be.** PORTABLE means the converter
  returned the same expression it was given (modulo cosmetic whitespace). True
  semantic equivalence of SQL is undecidable in general, so the grader auto-clears
  only the changes a normalized tree proves equal and leaves the rest for a human.
- **Identity beyond name.** A dropped input element and an added output element
  that carry an equivalent expression are paired as one element whose identity
  (name) was not preserved, rather than scored as a drop plus an unrelated add.
- **Diagnosed vs silent, and PLACEHOLDER, need the converter's warnings.** Pass
  them with `--diagnostics`.

## Tests

`test_grade.py` has one test per outcome plus an identity check that runs the real
M0-M3 fixtures through a no-op converter and asserts every element grades
PORTABLE. Run with `PYTHONPATH=. pytest test_grade.py`.
