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

Each `OssieDocument` is one semantic model: `name`, `datasets`, `relationships`,
and `metrics` sit at the root alongside `version`.
Construct documents with `OssieDocument(name="sales", datasets=[...])` and access
their datasets as `document.datasets`. JSON and YAML serialization use the same
flat shape. The former `semantic_model` wrapper is rejected. Unwrap old
single-model documents and split multi-model documents into separate files,
preserving model contents and setting `version` in each file before loading them.

`OssieSemanticModel` remains available for embedded models, such as ontology
components, which do not include document metadata.

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
