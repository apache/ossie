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

# Significant specification changes

This is a **curated history of merged changes** that affect the format,
interpretation, or implementation of Ossie specifications. It complements the
[core specification's version history](../core-spec/spec.md#version-history):
that section records versions, while this page links individual decisions to
their motivation and downstream impact. The source specifications and schemas,
not this summary, define the normative behavior.

The current development version is **`0.3.0.dev0`, which is unreleased and mutable**.
Earlier entries were merged under the former `0.2.0.dev0` development label;
[#691](https://github.com/apache/ossie/pull/691) renamed the in-development version.
There will be no `0.2.0` release.
They are not evidence of a published release or a completed migration in every
converter. Refer to the linked pull requests for review discussions, and check
the [current core specification](../core-spec/spec.md) and
[ontology specification](../ontology/ontology.md) before implementing.

## `0.3.0.dev0` development history (unreleased)

| Merged (UTC) | Area / source | Change and rationale | Impact on producers and consumers |
| --- | --- | --- | --- |
| 2026-09-16 | Core [#383](https://github.com/apache/ossie/pull/383) | **One model per document.** Model properties moved to the document root to remove the wrapper and make the exchange unit unambiguous. | **Breaking within the unreleased schema.** Unwrap a single `semantic_model`; split a multi-model wrapper into separate documents. Old wrapped documents do not validate against the new root. See [migration guidance](../core-spec/spec.md#migrating-earlier-document-shapes). |
| 2026-09-22 | Core [#439](https://github.com/apache/ossie/pull/439) | **Register `OSSIE_SQL_2026` as a portable expression dialect.** Its existing language definition could not be used in documents while the dialect token was absent from schema validation. | Additive at merge time; no change to the default dialect. This initially used `expression.dialects`; the later [#617](https://github.com/apache/ossie/pull/617) adds the preferred `expression.sql` form. |
| 2026-10-04 | Ontology [#458](https://github.com/apache/ossie/pull/458) | **Separate ontology, model, and mapping documents.** Independent references allow an ontology and a semantic model to be reused without embedding copies of either. | Mapping authors can use standalone documents validated by `ontology/mapping.json` and references to one ontology and one model. The older embedded `ontology_mappings` form remains accepted but is deprecated; importers should support the standalone form. |
| 2026-10-13 | Expression language [#577](https://github.com/apache/ossie/pull/577) | **Explicit `NULLS FIRST` / `NULLS LAST` in window `ORDER BY`.** Engine defaults differ, so explicit ordering is needed for portable window results. | Expression parsers and SQL translators should accept and preserve the new clause. The default when the clause is omitted was *not* decided by this change. |
| 2026-10-19 | Relational interface [#354](https://github.com/apache/ossie/pull/354) | **Layer 2 Relational Query Interface added.** It describes querying model measures through SQL relations and `MEASURE()` with correct aggregation semantics. | Additive specification document, not a new core JSON/YAML field. Query-engine implementers can adopt the interface; schema-only consumers need no migration. See [relational semantics](../core-spec/relational_semantics.md). |
| 2026-10-22 | Core [#591](https://github.com/apache/ossie/pull/591) | **Dataset-scoped metrics.** Metrics can live with the dataset they describe instead of all metrics living at the model root. | Additive optional `datasets[].metrics`. Resolving a dataset metric uses unqualified declared field names; model-scoped metric expressions use qualified `dataset.field` names. Consumers must account for both scopes when reading metrics. |
| 2026-10-26 | Core [#617](https://github.com/apache/ossie/pull/617) | **Portable `expression.sql` and open vendor dialect tokens.** The Ossie-defined portable expression becomes primary, while vendor-specific dialects stay opaque and extensible. | Existing `expression.dialects` remains accepted; writers should prefer `expression.sql` for portable SQL, and readers should give it precedence over a duplicate `OSSIE_SQL_2026` dialect entry. Dialect names are no longer a closed enum; consumers must tolerate unknown values. |
| 2026-11-01 | Ontology [#668](https://github.com/apache/ossie/pull/668) | **Non-empty ontology identifiers.** Validation now rejects empty names, IRIs, references, and mapping SQL expressions, matching existing restrictions elsewhere. | Authors should supply non-empty values for identifying strings. Some malformed documents that previously passed schema validation now fail earlier, with schema-layer errors. |
| 2026-11-09 | Core / Ontology [#691](https://github.com/apache/ossie/pull/691) | **Move the in-development version to `0.3.0.dev0`.** The next planned release is `0.3.0-incubating`; no `0.2.0` release is planned. | **Breaking for development documents.** Version constants in the current schemas reject `0.2.0.dev0`; producers must declare `0.3.0.dev0` and coordinate schema/package/converter versions. This is a development-version change, not a release announcement. |
| 2026-11-10 | Core [#522](https://github.com/apache/ossie/pull/522) | **Object `nonportable_extensions` data.** Vendor extension payloads become objects, and the field is renamed from `custom_extensions` so portable core semantics stay separate from vendor state. The core schema does not describe keys under `data`. | **Breaking within unreleased `0.3.0.dev0`.** Rename the field to `nonportable_extensions` and write `data` as an object, not a JSON string. The old name does not validate. Quote scalars under `data` that YAML may read as a boolean, number, or date. See [nonportable extensions](../extensions/nonportable_extensions.md). |
| 2026-11-12 | Expression language [#687](https://github.com/apache/ossie/pull/687) | **`ROUND` ties and true division in `OSSIE_SQL_2026`.** `ROUND` rounds half away from zero, and `/` is true division also for two integers, because engines differ on both and the same portable metric returned different numbers ([#643](https://github.com/apache/ossie/issues/643); [dev@ lazy consensus](https://lists.apache.org/thread/c1hy8vw7fqcp2y2hhxzj582516wqkoq6)). | Engines and converters with half-to-even `ROUND` or integer division emit the equivalent expressions the Numeric Semantics section gives; exact where `DECIMAL` arithmetic stays `DECIMAL`, best effort for exact ties of binary floating-point values. Documents need no change. |
| 2026-11-11 | Core / Ontology [#705](https://github.com/apache/ossie/pull/705) | **A key, and an ontology list of names or expressions, names each thing once.** A key is the set of fields it names, and a supertype, identifying relationship, constraint or verbalization pattern given twice says nothing the first already said. | **Validation becomes stricter.** `primary_key` and each `unique_keys` definition reject a repeated field, as do the ontology's `extends`, `identify_by` and `verbalizes` lists and `requires` and `derived_by` wherever they appear. Field names resolve case-insensitively, so `[email, EMAIL]` repeats one field; that case is reported by the validator rather than the schema. Remove the duplicate entry; a document that had one declared less than it appeared to. A `unique_keys` definition that restates another remains valid and is reported as a warning. |

## Semi-automated discovery and upkeep

The rows above are **curated highlights**, not an exhaustive release log. For
the live stream of all merged work, use the
[GitHub merged-PR list](https://github.com/apache/ossie/pulls?q=is%3Apr+is%3Amerged);
the most recent merge may not be reflected here immediately.

To reduce drift, maintainers can run an **offline, standard-library-only**
audit from a repository checkout:

```bash
python scripts/audit-spec-changes.py --since 2026-09-01
```

The audit examines first-parent commits touching `core-spec/` or `ontology/`,
recognizes squash subjects ending in `(#123)` and `Merge pull request #123`
subjects, and lists those
**not yet represented** in this history. It is advisory, not a release gate:
editorial PRs may be intentionally omitted, and commits without PR numbers
cannot be discovered automatically. Reviewers decide which changes materially
affect interoperability, rather than inferring migration advice from code.
For a significant specification PR, add its history entry **in the same PR**
or explain why no entry is warranted in the PR checklist.

## Keeping this history useful

For future **merged** specification changes that materially affect adopters,
add an entry that records:

1. The merge date, affected specification area, and source PR (with relevant
   mailing-list decision linked from the PR when applicable).
2. The rationale for the change, not just the file or field that changed.
3. Whether existing documents become invalid, a construct is deprecated,
   validation becomes stricter, or a new capability is optional.
4. Concrete migration or consumer actions, or an explicit statement that none
   is required.

Keep unreleased changes under their development version; move or annotate them
when the release is actually published. Documentation-only clarifications with
no behavioral impact need not be listed. This is a selected impact history, not
a substitute for the [Git commit log](https://github.com/apache/ossie/commits/main/)
or the release process.
