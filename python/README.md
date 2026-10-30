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

# Apache Ossie Python Package

The Apache Ossie Python package provides Pydantic v2 models for the Apache Ossie semantic model specification. It is the shared foundation used by Apache Ossie converters to parse, construct, validate, and serialize Ossie documents from Python application.

Each `OssieSemanticModel` is one semantic model: `name`, `datasets`, `relationships`,
and `metrics` sit at the root alongside `version`.
Construct documents with `OssieSemanticModel(name="sales", datasets=[...])` and access
their datasets as `document.datasets`. JSON and YAML serialization use the same
flat shape, with `version` serialized first when included. The former
`semantic_model` wrapper is rejected. Unwrap old
single-model documents and split multi-model documents into separate files,
preserving model contents and setting `version` in each file before loading them.

The same `OssieSemanticModel`, `version` included, is embedded in ontology maps.
Earlier releases used `OssieDocument` for semantic models; that role now belongs to
`OssieSemanticModel`, so replace `OssieDocument(...)` and
`OssieDocument.from_ossie_yaml(...)` with their `OssieSemanticModel` equivalents.

Load serialized documents with `OssieSemanticModel.from_ossie_yaml(text)` or
`OssieSemanticModel.from_ossie_json(text)`. Both reject duplicate mapping keys,
including keys in nested objects, so an earlier value is not silently lost.
The YAML loader preserves aliases, repeated merge directives and explicit
overrides of merged keys.

`OssieOntologyModel` mirrors `ontology/ontology.json`: concepts
(`OssieConceptComponent`), the relationships among them
(`OssieOntologyRelationship`, distinct from the dataset join `OssieRelationship`),
and, deprecated, embedded `ontology_mappings`.

`OssieSemanticModel` and `OssieOntologyModel` both extend `OssieDocument`, which holds
`version` and the `to_ossie_*` and `from_ossie_*` methods. An ontology file
therefore loads with `OssieOntologyModel.from_ossie_yaml(text)`, under the same checks.

## Development

### Prerequisites
- Python 3.11 or later
- uv >= 0.9.0

### Installation
```bash
uv sync
```

### Generating package distributions
```bash
uv build
```
