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

# Proposal: One semantic model per document

Status: proposed for community discussion; not an adopted specification change.

## Motivation

The core JSON Schema currently defines `semantic_model` as an array. This lets
one JSON or YAML document carry multiple independent semantic models, although
one model already contains multiple datasets, relationships, and metrics.

Support across converters is uneven. Omni, Sigma, Snowflake, and other converters
warn and convert only the first model. Polaris uses the collection to exchange
multiple namespaces, and Salesforce can produce one output per model. A valid
multi-model Ossie document therefore does not consistently translate in full.

A single-model document would give each model its own validation, review,
conversion, and publication unit. Bulk exchange remains useful, but need not be
part of the core model representation.

## Proposed representation

Keep the document envelope and change `semantic_model` from an array to one
required object referencing the existing `SemanticModel` definition. Apply the
same structure to JSON and YAML. Keep `version`, `dialects`, and `vendors` at the
document level, and keep the internal model definition unchanged.

Current YAML shape (dataset details abbreviated):

```yaml
version: 0.2.0.dev0
semantic_model:
  - name: sales_analytics
    datasets:
      - name: orders
        source: sales.public.orders
```

Proposed YAML shape (dataset details abbreviated):

```yaml
version: 0.2.0.dev0
semantic_model:
  name: sales_analytics
  datasets:
    - name: orders
      source: sales.public.orders
```

Corresponding JSON shape:

```json
{
  "version": "0.2.0.dev0",
  "semantic_model": {
    "name": "sales_analytics",
    "datasets": [
      {"name": "orders", "source": "sales.public.orders"}
    ]
  }
}
```

The examples illustrate the structural change using the current development
version. The community must choose the target specification version before
implementation; these examples do not make the proposed shape valid today.

The schema property would become:

```json
"semantic_model": {
  "$ref": "#/$defs/SemanticModel"
}
```

An array, null, or missing `semantic_model` would be invalid under the new format.
Multiple datasets and relationships within one model remain supported. Putting
models in the same document does not itself define cross-model references or
reuse; those capabilities need their own semantics.

## Bulk exchange

For catalog exports such as Polaris, produce one document per model in an output
directory. Archives can transport those files together. Defer standardizing a
bundle or manifest until concrete interoperability requirements justify one.

Implementation must define deterministic filenames, handle duplicate or unsafe
model names without overwriting files, and preserve every model. Bulk conversion
must report failures clearly rather than silently selecting the first model.
Existing single-file CLI options will need explicit handling for multiple outputs.

## Compatibility and migration

This is a breaking wire-format and SDK change. Coordinate the schema, shared
models, validators, converters, examples, and documentation in the target version.
Do not permanently accept both object and array forms in the canonical schema.

A migration utility or explicitly versioned compatibility reader should:

- Unwrap a legacy array containing exactly one model.
- Split an array containing multiple models into one document per model.
- Preserve the applicable document-level dialect and vendor declarations, and
  validate each resulting document; do not guess which declarations are unused.
- Report an empty array as a migration error. It cannot produce a model document.
- Preserve model contents and custom extensions, and update the specification
  version to the selected target version when emitting the new format.

Legacy documents remain interpreted according to their original format. If a
transition reader is provided, its version handling and removal policy should be
explicit. Readers must not reinterpret a multi-model legacy document as its first
model without reporting the loss.

## Implementation impact and validation

Implementation should cover:

- Core JSON Schema, YAML reference, and specification examples.
- Python `OssieDocument` and its serialization interfaces.
- Validator traversal, reference checks, and document-level declaration checks.
- Every converter's inputs, outputs, fixtures, and round-trip expectations.
- Polaris and Salesforce multi-output behavior and relevant CLI workflows.
- Ontology integration: `OntologyMap.semantic_model` already references the
  individual `SemanticModel` definition, so preserve that shape and check its
  consumers for document-envelope assumptions.

Focused validation should verify acceptance of the object form, rejection of
arrays and missing/null models, preserved serialization and converter round trips,
and lossless migration of single- and multi-model legacy documents. Bulk-output
checks should cover name collisions and partial failures. Full regression suites
can run in CI.

This proposal changes no executable schema or converter behavior. Implementation
and its tests follow community agreement on the representation and transition.

## Alternatives and discussion questions

Keeping the array preserves bulk export compatibility but leaves every consumer
responsible for model selection or multiple outputs. Restricting it to one element
retains the wrapper without simplifying the SDK or authoring shape. Moving all
model properties to the document root would remove another level, but is a larger
structural change than needed for the cardinality decision.

Feedback requested:

1. Are there use cases that require multiple models in one core document and
   cannot be adequately served by separate files or an archive?
2. Is retaining the envelope with an object-valued `semantic_model` preferable?
3. Which specification version should introduce the change, and is a migration
   utility sufficient or is a temporary compatibility reader needed?

Follow the specification-change discussion and voting process in
[CONTRIBUTING.md](../../CONTRIBUTING.md#specification-changes) before adoption.
