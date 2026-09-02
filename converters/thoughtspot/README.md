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

# Apache Ossie ThoughtSpot Converter

Will convert between **ThoughtSpot TML** and the Apache Ossie semantic model, in both
directions — neither direction is implemented yet; see Status below:

- **ThoughtSpot TML → Ossie** — will read a Model TML document plus the Table and SQL
  View documents it references, and emit one Ossie semantic model.
- **Ossie → ThoughtSpot TML** — will read one Ossie semantic model and emit the
  corresponding set of TML documents.

A single Ossie semantic model corresponds to **1 + N TML documents**, not one file: one
`model:` document plus one `table:` or `sql_view:` document per dataset. The converter reads
and writes the set.

File-to-file only. Nothing here calls a ThoughtSpot API.

## Status

Foundations only. Neither conversion direction is implemented yet. The foundations built so
far are: a YAML 1.2 codec (`_yaml.py`), structured issue reporting (`issues.py`), the
`custom_extensions` stash for data a conversion cannot carry natively (`stash.py`),
identifier derivation (`identifiers.py`), and key derivation (`keys.py`).

**The `THOUGHTSPOT` dialect is registered upstream** — apache/ossie#351 merged 2026-09-01.
Expressions are emitted under `THOUGHTSPOT`, with an `ANSI_SQL` entry alongside it where the
expression is portable, so consumers that do not implement our dialect still get something
they can execute.

## Coverage matrix

Every construct this converter does not carry, with its consequence. Each row is
required to raise a structured `ConverterIssue` at conversion time once the conversion
directions land — nothing may be dropped silently.

| # | Construct | Limitation | Consequence |
|---|---|---|---|
| L1 | Object identity (`guid`, `obj_id`, `fqn`) | Not carried — instance-local by construction | A round-tripped document imports as a new object |
| L2 | Row-level security (`rls_rules`) | Not carried — rule expressions name instance-local groups. **ERROR severity**: a single issue is raised, its message naming every affected table | RLS is unrepresentable in Ossie core and is security-bearing; rules must be re-applied in the target for each table named in the error |
| L3 | Presentation artifacts (Answers, Liveboards, charts) | Out of scope — Ossie models semantics, not visualisations | No loss to the semantic model |
| L4 | Spotter coaching objects | Separate object types; `ai_context.examples` is not interchangeable | Coaching must be re-created in the target |
| L5 | Aggregate-model associations (`aggregated_models`) | Entries are GUIDs of other Models — instance-local | Query routing is silently disabled; the issue is the only signal |
| L6 | Worksheets, Views, Sets, Alerts, Model Aliases | Predecessors or layers, not models | Convert the Model the alias points at instead |

## Known limitations

Separate from the coverage matrix above — that covers TML constructs not carried
(NM1-NM6); this covers identifier derivation correctness.

`identifiers.py`'s `normalise()` folds diacritics via Unicode NFKD decomposition before
lowercasing and substituting — a stdlib operation, not a policy choice — so accented
Latin now normalises correctly: `"Café"` -> `"cafe"`, `"Ürün"` -> `"urun"`, `"Zürich"` ->
`"zurich"`. The residual limitation is narrower: a character with **no ASCII
decomposition** (Cyrillic, CJK, and similarly non-Latin scripts) is still dropped, not
transliterated, and a name with no ASCII alphanumerics surviving still raises
`ValueError` (a CJK-only name, for example). There is also an open question NFKD does
not settle: some accented Latin folds to a *conventional* ASCII expansion rather than
the bare decomposed letter — German `"Müller"` decomposes to `"Muller"` here, not the
conventional `"Mueller"` — and choosing between them is a product decision left to a
later change.

## Rules

Rule identifiers referenced in the source (`ID1`-`ID4`, `X1`-`X9`, `KD1`-`KD3`, `R1`-`R11`,
`E1`-`E13`, `NM1`-`NM6`, and others) refer to an external specification: the construct and
expression mapping tables maintained in ThoughtSpot's own internal `thoughtspot-agent-skills`
repository, which today is the normative source for this converter's behaviour. That
repository is not ASF-hosted and is not publicly readable, so a rule identifier in this
source tree is currently **unresolvable from inside this repository** — a real gap against
the project's vendor-neutrality goal, and no other converter in this monorepo defers its
normative behaviour to an external, vendor-controlled document. The intent is to contribute
those mapping tables into this repository, under `docs/` or alongside this converter, so the
normative source becomes ASF-hosted like every sibling converter's. That is a larger change
needing its own review and is not done in this change; this section exists so the gap is
acknowledged rather than silent.

**Before declaring any expression untranslatable, consult the function mapping.** Many window
and LOD constructs have exact native equivalents; declaring one untranslatable without
checking is an error (invariant I7).

## Development

```bash
uv run --python 3.13 pytest tests/ -v
```

`uv run` syncs the `dev` dependency group (declared via PEP 735
`[dependency-groups]`, not an extra) and runs the tests in one step — see
`.github/workflows/converter-thoughtspot-ci.yml` for the CI invocation this
mirrors.
