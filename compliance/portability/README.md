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

# Portability measurement ladder (M0-M6)

A small set of portable fixtures, and a grader, for measuring how faithfully a
model survives a round-trip through each converter. This is issue #576
(measurements for model portability across converters). The fixtures are the
inputs; `grader/` scores each returned document against its input, element by
element, into the outcome vocabulary in `manifest.yaml`. A thin runner that drives
the converters (modelled on `cube/tools/interop_matrix.py` in apache/ossie-converters)
turns the whole thing into one command; the grader is written to sit behind it.

## What is here

Each rung adds exactly one capability on top of the one below it, so a
difference in a converter's output localizes to a single construct:

| Rung | Capability added | Construct |
|------|------------------|-----------|
| M0 | baseline | one dataset, one field, one `SUM` metric |
| M1 | a scalar field, no aggregation | `amount * 1.1` calculated column |
| M2 | the core aggregate set | `SUM`, `COUNT`, `COUNT(DISTINCT)`, `AVG`, `MIN`, `MAX` |
| M3 | a filtered aggregate | `SUM(CASE WHEN ... THEN x ELSE 0 END)`, and the same with `FILTER (WHERE ...)` |
| M4 | a cross-dataset metric | two datasets, a relationship, a model-scoped metric across the join |
| M5 | metric composition | a model-scoped metric built from two other metrics by name |
| M6 | date and time scalars | `YEAR`, `DATE_TRUNC`, `DATEDIFF(part, start, end)` |

`manifest.yaml` lists the fixtures, the element each rung exercises, and the
per-element outcome vocabulary the grader assigns (PORTABLE / TAG_PRESERVED,
REWRITTEN, SURVIVED-vendor-only, PLACEHOLDER, ADDED, RELABELED,
DROPPED-diagnosed, DROPPED-silent, REJECTED). `grader/grade.py` implements it
and `grader/test_grade.py` has one test per outcome plus an identity check that
grades the real fixtures all-PORTABLE. See `grader/README.md`.

## Design rules

- **Portable dialect only.** Every fixture is authored in `ANSI_SQL` or
  `OSSIE_SQL_2026`. A vendor-tagged input would only exercise one converter,
  which defeats a cross-converter measurement.
- **Qualified reference form is primary.** Where a metric references a dataset
  field, the qualified `dataset.field` form is the primary fixture and the bare
  form is a sibling variant, pending the name-resolution decisions in #486 and
  #343.
- **Conformance is a floor, not the measurement.** Every fixture passes
  `validation/validate.py` on current `main`. Fidelity is what the grader
  measures on top of that.

## Scope and placement

Rungs M7 and M8 (window and offset functions: time intelligence and
ratio-to-total) are held until the default NULL placement in window `ORDER BY`
is settled (#587), and are not in this set.

The fixtures measure the specification rather than any one converter, so they
live in this repository; the fixtures are self-contained and the manifest paths
are relative.
