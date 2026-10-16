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

# Portability measurement ladder (M0-M3)

A small set of portable fixtures for measuring how faithfully a model survives a
round-trip through each converter. This is the fixture half of issue #576
(measurements for model portability across converters). The scoring half is a
grader that diffs each returned document against the input element by element;
that lives with the interoperability harness in #348, not here.

## What is here

Each rung adds exactly one capability on top of the one below it, so a
difference in a converter's output localizes to a single construct:

| Rung | Capability added | Construct |
|------|------------------|-----------|
| M0 | baseline | one dataset, one field, one `SUM` metric |
| M1 | a scalar field, no aggregation | `amount * 1.1` calculated column |
| M2 | the core aggregate set | `SUM`, `COUNT`, `COUNT(DISTINCT)`, `AVG`, `MIN`, `MAX` |
| M3 | a filtered aggregate | `SUM(CASE WHEN ... THEN x ELSE 0 END)` |

`manifest.yaml` lists the fixtures, the element each rung exercises, and the
per-element outcome vocabulary a grader assigns (PORTABLE, SURVIVED-vendor-only,
ADDED, RELABELED, DROPPED-diagnosed, DROPPED-silent, REJECTED).

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

Rungs M4-M8 (relationships and cross-dataset metrics, metric-to-metric
composition, date and time scalars, window and offset functions) are held
pending #354, #591 and #587, and are not in this set.

Location is a starting point. If these should sit with the interoperability
harness from #348 or elsewhere once that lands, they can move; the fixtures are
self-contained and the manifest paths are relative.
