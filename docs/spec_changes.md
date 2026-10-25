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

# Specification Change History

This document records significant changes to the Apache Ossie specification since version 0.1.1, providing rationale and impact details for converter authors and tool vendors. The normative release record remains in the "Version History" section of `core-spec/spec.md`. Entries are added here when a corresponding specification PR merges.

## 0.2.0.dev0 (unreleased)

### Core model

- **One semantic model per document** ([#383](https://github.com/apache/ossie/pull/383), 2026-09-16).
  - Why: Simplify document structure by removing the wrapper and defining the semantic model directly at the root.
  - Impact: **Breaking.** Converters must emit flat documents without a wrapper object and update parsing logic to read the model from the root.

- **Drop root-level `dialects` and `vendors` arrays** ([#397](https://github.com/apache/ossie/pull/397), 2026-09-15).
  - Why: These arrays were added by a converter change without spec definition and are not read by any tool.
  - Impact: **Breaking.** Converters must stop emitting `dialects` and `vendors` at the document root; validation will reject them.

- **`vendor_name` is a free-form string** ([7e984cb](https://github.com/apache/ossie/commit/7e984cb), 2026-03-21).
  - Why: A new vendor should not need a spec change before it can use `custom_extensions`.
  - Impact: Relaxing. Consumers must accept any `vendor_name` string, not only the former enum values.

- **Add `datatype` to Field and Metric; reframe `is_time` as role marker** ([#113](https://github.com/apache/ossie/pull/113), 2026-07-23).
  - Why: Separate the logical data type (casting and serialization) from the temporal role, so a time field can have any type (for example an integer year).
  - Impact: Additive field. When `is_time` is unset it now defaults to `true` for `date`, `time`, `timestamp`, and `timestamp_tz` and `false` otherwise; converters that need another value should set `is_time` explicitly.

- **Reject empty identifiers** ([#414](https://github.com/apache/ossie/pull/414), 2026-09-23).
  - Why: Empty-string identifiers passed validation silently, and deeply nested input crashed the validator.
  - Impact: **Breaking.** The schema adds `minLength: 1` to required identifiers (`name` on every object, dataset `source`, relationship `from` and `to`); documents with empty values now fail validation.

- **Require a column in primary key and unique key definitions** ([#574](https://github.com/apache/ossie/pull/574), 2026-10-13).
  - Why: A key that lists no columns identifies nothing.
  - Impact: **Breaking.** Documents with empty arrays for `primary_key` or items in `unique_keys` will now fail validation.

- **Names in metrics, keys, and relationships refer to dataset fields** ([#486](https://github.com/apache/ossie/pull/486), 2026-10-22).
  - Why: Earlier versions did not say whether these names were fields or columns of the dataset's `source`, and converters read them both ways.
  - Impact: **Breaking.** Metric expressions, `primary_key`, `unique_keys`, and relationship `from_columns`/`to_columns` resolve to declared fields. Model-scoped metrics use qualified `dataset.field` references. See [Name Resolution](../core-spec/spec.md#name-resolution).

- **Support dataset-scoped metrics** ([#591](https://github.com/apache/ossie/pull/591), 2026-10-21).
  - Why: Allow metrics to be declared on individual datasets for aggregations that resolve within a single dataset, keeping them next to their dependent fields.
  - Impact: Additive; converters may now emit metrics under `datasets[].metrics` in addition to the model scope.

### Expression language

- **Add `FILTER (WHERE ...)` aggregate modifier** ([#382](https://github.com/apache/ossie/pull/382), 2026-10-19).
  - Why: Provide SQL-standard conditional aggregation support with clear semantics for empty-input values.
  - Impact: Additive; converters may emit `FILTER (WHERE ...)` postfix modifiers on aggregate expressions.

- **Allow `NULLS FIRST` and `NULLS LAST` in window `ORDER BY`** ([#577](https://github.com/apache/ossie/pull/577), 2026-10-13).
  - Why: Enable authors to explicitly control NULL placement in window functions for consistent engine behavior.
  - Impact: Additive; converters may include `NULLS FIRST` or `NULLS LAST` clauses in window function definitions.

- **Define `OSSIE_SQL_2026` dialect** ([#439](https://github.com/apache/ossie/pull/439), 2026-09-21).
  - Why: Name the dialect of the Ossie expression language itself, so expressions written in it can say so.
  - Impact: Additive; converters may reference `OSSIE_SQL_2026` as a valid dialect value.

- **Additive dialect and vendor names**.
  - Why: Register platforms that converters already target.
  - Impact: Additive; converters may now use `BIGQUERY`, `SIGMA`, `THOUGHTSPOT`, `HONEYDEW`, GoodData (MAQL dialect), and Power BI vendor names.

### Relational Query Interface

- **Add Relational Query Interface specification** ([#354](https://github.com/apache/ossie/pull/354), 2026-10-19).
  - Why: Define Layer 2 of the layered query interface, enabling querying Ossie semantic models through SQL extended with measure semantics.
  - Impact: Additive; it does not change model documents. Tools that offer SQL over Ossie models can implement it; see `core-spec/relational_semantics.md` and `core-spec/spec_layers.md`.

### Ontology

- **Add ontologies and ontology mappings** ([#133](https://github.com/apache/ossie/pull/133), 2026-05-29).
  - Why: Let semantic models map to shared business concepts, not only to physical tables.
  - Impact: Additive; adds `ontology/ontology.md` and `ontology/ontology.json`.

- **Flatten concept declarations** ([#257](https://github.com/apache/ossie/pull/257), 2026-07-24).
  - Why: One flat shape for concept declarations in the schema, the spec snippets, and the flights example.
  - Impact: **Breaking.** Ontology documents written with the earlier nested concept shape must move to the flat shape.

- **Rename `uri` to `iri`** ([#400](https://github.com/apache/ossie/pull/400), 2026-09-27).
  - Why: Align with RFC 3987 and clarify that the global identifier covers both full IRIs and QNames.
  - Impact: **Breaking.** Converters must rename the `uri` property to `iri` in concepts and relationships; prefix maps use the `iri` format.

- **Add top-level `prefixes`** ([#332](https://github.com/apache/ossie/pull/332), 2026-09-14).
  - Why: Enable QName expansion against prefixes for global identifiers.
  - Impact: Additive; converters may populate the top-level `prefixes` map in ontology documents.

- **Mappings reference core schema root** ([#441](https://github.com/apache/ossie/pull/441), 2026-09-25).
  - Why: An embedded model should validate the same way as a standalone core document.
  - Impact: **Breaking.** Each model embedded in an ontology mapping must be a complete core document; validation enforces this.

- **Decouple Ontologies, Semantic Models, and Mappings** ([#458](https://github.com/apache/ossie/pull/458), 2026-10-04).
  - Why: Allow standalone mapping documents that reference one ontology and one semantic model by name or IRI.
  - Impact: Deprecation. The embedded `ontology_mappings` list is deprecated in favor of standalone mapping documents (`ontology/mapping.json`); new tools should emit mapping documents.

- **A mapping must say how it populates its target** ([#582](https://github.com/apache/ossie/pull/582), 2026-10-14).
  - Why: A mapping that names a concept but not how to populate it carries no information.
  - Impact: **Breaking.** Mapping documents must include either `object_mappings`/`link_mappings` or `expression`/`referent_mappings` as appropriate; empty mappings are now invalid.

### Clarifications (wording only)

- Rank `NOT` below comparison in precedence list ([#614](https://github.com/apache/ossie/pull/614), 2026-10-22). Impact: wording only.
- Clarify model declaration vs. query scope of expression language ([#536](https://github.com/apache/ossie/pull/536), 2026-10-08). Impact: wording only.
- Correct the MAQL acronym expansion to match `spec.yaml` ([#528](https://github.com/apache/ossie/pull/528), 2026-10-07). Impact: wording only.

## 0.1.1 (2025-12-11, initial release)

The first release: the core semantic model structure (datasets, relationships, fields, and metrics), multi-dialect expressions, the vendor extension framework, and AI context. The entries above are measured against this baseline.
