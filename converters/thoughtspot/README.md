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

Bidirectional, offline conversion between an [Apache Ossie](https://github.com/apache/ossie)
semantic model and ThoughtSpot TML. No ThoughtSpot connection required.

- **ThoughtSpot TML → Ossie** (`to-ossie`): reads a Model TML document plus the Table and
  SQL View documents it references, and emits one Ossie semantic model.
- **Ossie → ThoughtSpot TML** (`to-tml`): reads one Ossie semantic model and emits the
  corresponding set of TML documents.

A single Ossie semantic model corresponds to **1 + N TML documents**, not one file: one
`model:` document plus one `table:` or `sql_view:` document per dataset. The converter
reads and writes the set. File-to-file only — nothing here calls a ThoughtSpot API.

The `THOUGHTSPOT` dialect is registered upstream — apache/ossie#351 merged 2026-09-01.
Both conversion directions are implemented and tested: example-based unit tests, an
exact-document comparison against a shared TPC-DS fixture set (the same retail schema
every sibling converter round-trips), a round-trip suite asserting preservation and
translation separately, and a Hypothesis property-based suite over adversarial
identifiers.

## Installation

```bash
pip install apache-ossie-thoughtspot        # once published to PyPI
# or, from a checkout of this directory:
pip install -e .
```

The only runtime dependency is `PyYAML`. Python 3.10+.

## Usage

### Command line

```bash
ossie-thoughtspot to-ossie <tml-file>... -o <out.yaml>  [--issues <issues.json>] [--force]
ossie-thoughtspot to-tml   <ossie.yaml>  -o <out-dir>    [--issues <issues.json>] [--force]
```

`to-ossie` takes the Model TML document plus every Table/SQL View document it references
and writes one Ossie YAML file. `to-tml` takes one Ossie YAML document and writes the
corresponding TML document set — one file per document, tables before the model — into
an output directory it creates if needed.

`-o`/`--output` is required in both directions: `to-tml` writes a set of files that has
no single-file stdout representation, so unlike some sibling converters there is no
"default: stdout" fallback. Neither subcommand overwrites an existing output file unless
`--force` is given.

Every declared loss or degradation the conversion records is written as a JSON array of
issues — to `--issues` when given, to stderr otherwise — never mixed into the document
output. The process exits `1` when that issue log contains an ERROR-severity issue, `0`
otherwise: a conversion that only warned or informed about a declared loss is still a
successful conversion.

### Python API

```python
from ossie_thoughtspot import tml, tml_to_ossie, ossie_to_thoughtspot, _yaml

# ThoughtSpot TML -> Ossie
texts = [(path, open(path).read()) for path in ("model.model.tml", "orders.table.tml")]
result = tml_to_ossie.convert(tml.load_document_set(texts))
ossie_yaml = _yaml.dump(result.model)          # result.issues: IssueLog

# Ossie -> ThoughtSpot TML
ossie_document = _yaml.load(open("model.yaml").read())
result = ossie_to_thoughtspot.convert(ossie_document)
for filename, text in tml.dump_document_set(result.documents):
    ...                                          # result.issues: IssueLog
```

`result.issues` is an `IssueLog`: `has_errors()`, `count_by_severity()`, `as_dicts()`.
Every declared loss raises an issue here — see [Coverage matrix](#coverage-matrix) and
[Expression translation](#expression-translation-what-is-not-translated-and-why) below
for what gets declared and why.

## Mapping

| Ossie | ThoughtSpot TML | Notes |
|---|---|---|
| `semantic_model` (one entry) | one Model document + the Table/SQL View documents it references | Exactly one `semantic_model` entry per document; more than one is a hard failure |
| `dataset` | `table:`/`sql_view:` document, surfaced via the Model's `model_tables[]` entry | One dataset per participating `model_tables[]` entry, not per physical table — a self-join or a table used twice gets two datasets sharing one `source` |
| `dataset.source` | `db`.`schema`.`db_table`, or `sql_query` for a SQL View | A dotted part is stashed individually (`source_parts`) when the joined form would be ambiguous |
| `dataset.fields` | Table `columns[]` (physical) or Model `formulas[]` + surfacing `columns[]` entry (computed) | A computed field is attributed to the one dataset every column reference in its expression resolves to; ambiguous or cross-dataset references raise an issue instead of guessing |
| `relationship` | `model_tables[].joins[]` (inline) or Table `joins_with[]` (referencing) | `from_columns`/`to_columns` are the join's equality pairs; a join with a non-equality residual (range/ASOF) narrows the same pairs, with the verbatim condition stashed — see [the payload section](#the-custom_extensionsthoughtspot-payload) below |
| `dataset.primary_key` / `unique_keys` | *(not native to TML)* | TML declares no keys; Ossie's are derived from to-one relationships targeting the dataset |
| `metric` | Model `formulas[]` + surfacing `columns[]` entry with `column_type: MEASURE` | Three TML shapes compose into one metric: a bare aggregate formula, a scalar formula plus the surfacing column's `aggregation`, or a physical column plus `aggregation` |
| `field`/`metric` `expression.dialects` | `formulas[].expr` or a physical `db_column_name` | See [Expression translation](#expression-translation-what-is-not-translated-and-why) |
| `custom_extensions[THOUGHTSPOT]` | TML fields with no Ossie equivalent | See [The `custom_extensions[THOUGHTSPOT]` payload](#the-custom_extensionsthoughtspot-payload) below |

## The `custom_extensions[THOUGHTSPOT]` payload

TML carries properties Ossie's core specification has no field for — a Connection name,
search-indexing settings, a display column's warehouse name when it differs from its
label, a join's exact type, and more. `TML → Ossie` stashes each one under a single
`custom_extensions` entry (`vendor_name: THOUGHTSPOT`) attached to the Ossie object it
came from; `Ossie → TML` reads the same entry back to reconstruct the original TML
property, so `TML → Ossie → TML` is lossless for everything TML itself can express.

```yaml
custom_extensions:
- vendor_name: THOUGHTSPOT
  data: '{"_v": 1, "connection_name": "My Snowflake", "tml_object": "table"}'
```

`data` is always a single JSON-encoded string (never a nested object — the core
specification requires this), carrying:

- `_v`, a shape version bumped only when the payload's *shape* changes, never for a
  value change — an unrecognised version is a hard failure rather than a silent
  misread of a shape this converter has never seen.
- Never a `guid`, `obj_id`, or `fqn` at any depth — object identity is instance-local
  and is refused outright rather than carried, at write time.
- Foreign-vendor `custom_extensions` entries on the same object pass through untouched
  in both directions.
- An object with nothing to stash gets no `custom_extensions[THOUGHTSPOT]` entry at
  all, so a converted document stays as small as its content requires.

`Ossie → TML` restores a stashed value only when it is still current: several keys
(a physical column's warehouse name, a relationship's verbatim join condition, a
dataset's TML object kind) are recorded alongside a witness copy of the live value they
were stashed next to, and the stash is used only when that witness still matches the
document's current value — an edit to the Ossie document since the stash was written
(retargeting a relationship, renaming a field) makes the stash stale for that key, and
the value is re-derived instead of silently reapplied to the wrong thing.

The full key vocabulary is documented in `src/ossie_thoughtspot/constants.py`, grouped
by which Ossie object each key's entry attaches to (model, dataset, relationship,
field/metric).

## Expression translation: what is not translated, and why

**A ThoughtSpot formula is captured verbatim under a `THOUGHTSPOT` dialect entry. A
portable `ANSI_SQL` sibling is added only when the whole expression is a single bare
column reference — the one shape where portability is certain. Every other shape — a
function call, an operator expression, a runtime parameter, a formula cross-reference —
is recorded THOUGHTSPOT-only, with an issue explaining why no portable sibling was
produced.** No SQL dialect is ever re-rendered into another dialect in this converter,
in either direction.

This is a deliberate design position, not an oversight, and it is worth stating plainly
rather than leaving it to be inferred from reading `tml_to_ossie.py`:

1. **The specification's own default is pass-through.** `core-spec/spec.md` defines
   `dialects[]` as a list of `{dialect, expression}` pairs precisely so a value a
   converter cannot translate can still travel, tagged with the dialect it is valid in.
   Emitting an untranslated ThoughtSpot expression under `THOUGHTSPOT` and stopping
   there is using the mechanism the specification provides for exactly this case, not
   working around a gap in it.
2. **No converter in this repository re-renders an expression from one SQL dialect into
   another.** Every sibling converter that meets a dialect it does not natively speak
   either passes the expression through under its own vendor dialect or falls back to
   `ANSI_SQL` when the source already provides one — none parses a foreign dialect's
   grammar and re-emits it in a different one. A hand-rolled reimplementation of that
   translation, done once per converter, is exactly the kind of duplicated, easy-to-get-
   subtly-wrong logic a shared SQL parser would exist to prevent — and no such shared
   parser exists in this project today. Building one is out of scope for a single
   converter to take on unilaterally.
3. **The reference converter (`converters/databricks`) tags its own vendor dialect and
   reads that first**, falling back to `ANSI_SQL` only when the source document already
   provides it — it does not translate a foreign dialect into its own either. This
   converter follows the same shape: prefer `THOUGHTSPOT`, add `ANSI_SQL` only when it
   can be produced with certainty, never invent a translation.

**What "certain" means in practice.** A bare column reference (`[TABLE::Column]`) is the
one shape this converter resolves without ambiguity: the reference names a physical or
computed field this document already knows how to place, so the `ANSI_SQL` sibling is
just that field's own dataset-qualified name — no expression semantics are being
translated at all, only a reference being resolved. Everything past that — even a
composition ThoughtSpot's own documentation says is exactly equivalent to a portable
form — is left THOUGHTSPOT-only. `src/ossie_thoughtspot/expressions/reverse.py` records,
for testing and future use, which of ThoughtSpot's native functions compose into a
portable Ossie expression and which do not (`ReverseDisposition`: compose fully,
compose partially, resolve only to a dialect entry, or have no Ossie form at all) — but
this inventory is not yet called from the shipped `TML → Ossie` conversion path itself.
The current release is more conservative than what that inventory shows is possible: it
never guesses, so it never translates something it has not resolved to a certainty.

**The complementary direction.** `Ossie → TML` faces the reverse problem: rendering an
Ossie specification construct as a ThoughtSpot formula. There, `expressions/catalog.py`
maps all 146 constructs the Ossie expression language defines to a ThoughtSpot rendering
— 108 with a native equivalent, 37 as a `sql_*_op` pass-through (opaque,
warehouse-dialect-specific SQL ThoughtSpot cannot introspect, logged at WARNING every
time), and 1 (`EXISTS_IN()`) with no representation ThoughtSpot has a slot for at all
(logged at ERROR, never silently dropped). This direction *can* translate constructs to
their ThoughtSpot equivalents because the Ossie expression language — unlike an
arbitrary ThoughtSpot formula — is the one grammar this converter fully parses; nothing
here reads or re-renders raw ThoughtSpot formula syntax, or any other vendor's SQL.

Read together with the [coverage matrix](#coverage-matrix) below, this is the
converter's whole answer to "what does not survive a round trip and why": expressions
pass through by declared design; every other construct's loss is declared per row.

## Coverage matrix

Every construct this converter does not carry, with its consequence. Each row raises a
structured `ConverterIssue` at conversion time — nothing is dropped silently.

| # | Construct | Limitation | Consequence |
|---|---|---|---|
| L1 | Object identity (`guid`, `obj_id`, `fqn`) | Not carried — instance-local by construction | A round-tripped document imports as a new object |
| L2 | Row-level security (`rls_rules`) | Not carried — rule expressions name instance-local groups. **ERROR severity**: a single issue is raised, its message naming every affected table | RLS is unrepresentable in Ossie core and is security-bearing; rules must be re-applied in the target for each table named in the error |
| L3 | Presentation artifacts (Answers, Liveboards, charts) | Out of scope — Ossie models semantics, not visualisations | No loss to the semantic model |
| L4 | Spotter coaching objects | Separate object types; `ai_context.examples` is not interchangeable | Coaching must be re-created in the target |
| L5 | Aggregate-model associations (`aggregated_models`) | Entries are GUIDs of other Models — instance-local | Query routing is silently disabled; the issue is the only signal |
| L6 | Worksheets, Views, Sets, Alerts, Model Aliases | Predecessors or layers, not models | Convert the Model the alias points at instead |

## Known limitations

Separate from the coverage matrix above — this covers identifier derivation
correctness, not TML constructs.

`identifiers.py`'s `normalise()` folds diacritics via Unicode NFKD decomposition before
lowercasing and substituting, so accented Latin normalises correctly: `"Café"` ->
`"cafe"`, `"Ürün"` -> `"urun"`, `"Zürich"` -> `"zurich"`. A character with **no ASCII
decomposition** (Cyrillic, CJK, and similarly non-Latin scripts) is still dropped, not
transliterated, and a name with no ASCII alphanumerics surviving still raises
`ValueError`. There is also an open question NFKD does not settle: some accented Latin
folds to a *conventional* ASCII expansion rather than the bare decomposed letter —
German `"Müller"` decomposes to `"Muller"` here, not the conventional `"Mueller"` — and
choosing between them is a product decision left to a later change.

## Rules

Earlier revisions of this converter's comments and docstrings cited short, letter-plus-
number rule identifiers drawn from an internal construct/expression mapping reference
that is not part of this repository and is not publicly readable — a citation of that
shape in the shipped source was therefore unresolvable from inside this repository
alone. That has been resolved: every such citation has been rewritten to state the
substance it stood for directly, in place, so nothing shipped here depends on material
outside this repository. `tests/test_shipped_references.py` enforces this going
forward — it fails the suite if a citation of that shape reappears in any shipped
file.

**Before declaring any expression untranslatable, consult the function mapping.** Many
window and LOD constructs have exact native equivalents; declaring one untranslatable
without checking is an error.

## Generated reference documentation

`docs/` holds four Markdown reference documents, generated from this converter's own
code rather than hand-authored — the code is the single source of truth for the
mapping each one describes, so a document maintained separately would only be able to
drift from it:

- [`docs/expression-mapping.md`](docs/expression-mapping.md) — every Ossie
  specification construct, its classification and its ThoughtSpot rendering, generated
  from `expressions/catalog.py`'s `CATALOG`.
- [`docs/reverse-inventory.md`](docs/reverse-inventory.md) — every ThoughtSpot-only
  function with no specification counterpart, and how it composes (or does not) back
  into a portable Ossie expression, generated from `expressions/reverse.py`'s `REVERSE`.
- [`docs/datatype-map.md`](docs/datatype-map.md) — the bidirectional datatype map and
  which types are declared lossy, generated from `datatypes.py`.
- [`docs/vendor-payload.md`](docs/vendor-payload.md) — every
  `custom_extensions[THOUGHTSPOT]` key, its scope and how it is treated on the return
  trip, generated from `constants.py`'s `STASH_KEY_CLASSIFICATION`.

`tools/generate_reference_docs.py` produces all four; it is dev/tooling only — not a
runtime dependency, not part of the wheel, not a `[project.scripts]` entry point.
Regenerate with:

```bash
uv run --python 3.13 python tools/generate_reference_docs.py
```

`tests/test_reference_docs_current.py` regenerates on every test run and compares the
result against the committed files byte-for-byte, so a `docs/*.md` file that has
drifted from the code it describes fails the suite rather than going unnoticed.

## Development

```bash
uv run --python 3.13 pytest tests/ -v
```

`uv run` syncs the `dev` dependency group (declared via PEP 735 `[dependency-groups]`,
not an extra — `pytest`, plus `jsonschema` and `hypothesis` for the schema-validation and
property-based suites) and runs the tests in one step — see
`.github/workflows/converter-thoughtspot-ci.yml` for the CI invocation this mirrors,
run across every Python version this package declares support for.

## Future effort

The Apache Ossie specification is still evolving. As it adds or changes fields, this
converter will be updated to track them — extending the mapping and coverage in both
directions to keep the conversion current and to support as much as the format allows
over time. `expressions/reverse.py`'s classified inventory of ThoughtSpot-only functions
is a candidate foundation for a future, more ambitious `TML → Ossie` composition
strategy, once that expansion is deliberately taken on rather than folded into this
release.
