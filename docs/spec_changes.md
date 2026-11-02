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

The entries below belong to **`0.2.0.dev0`, which is unreleased and mutable**.
They are not evidence of a published release or a completed migration in every
converter. Refer to the linked pull requests for review discussions, and check
the [current core specification](../core-spec/spec.md) and
[ontology specification](../ontology/ontology.md) before implementing.

## `0.2.0.dev0` (unreleased)

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
