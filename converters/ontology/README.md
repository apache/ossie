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

# Ossie Ontology Converters

Converters between Ossie, Palantir, LinkML, and Spec ontology formats.

| Converter           | Direction |
|---------------------|-----------|
| `palantir_to_ossie` | Palantir ontology → Ossie model |
| `ossie_to_spec`     | Ossie model → Spec YAML |
| `spec_to_ossie`     | Spec YAML → Ossie model |
| `linkml_to_ossie`   | LinkML schema → Ossie model |
| `ossie_to_linkml`   | Ossie model → LinkML schema |

### LinkML

The LinkML converters use the natively-compiled [LinkML-Scala](https://github.com/NeverBlink-OSS/linkml-scala) library (see: [converter source code](https://github.com/NeverBlink-OSS/linkml-scala/tree/main/generator/src/eu/neverblink/linkml/generator/ossie)). This repository only provides wrappers – please file any issues [here](https://github.com/NeverBlink-OSS/linkml-scala/issues).

The Ossie <-> LinkML mapping and its limitations are documented [here](https://github.com/NeverBlink-OSS/linkml-scala/blob/main/docs/ossie_mapping.md). In general, LinkML supports only a subset of restriction expressions in Ossie, `derived_by` is not yet supported, and `ontology_mappings` are not representable in LinkML. Conversely, Ossie does not support many of the features of LinkML, such as all possible inheritance patterns. We are working to iteratively improve the coverage of the mapping in both directions.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package and dependency manager

Install uv if you don't have it:

```bash
brew install uv
```

## Setup

```bash
cd converters/ontology
uv sync
```

## Generating / updating the lock file

`uv.lock` is produced by uv from `pyproject.toml`. Run this whenever you add or
change a dependency:

```bash
uv lock
uv sync
```

## Usage

The package is importable as `ossie_ontology` after installation:

```python
from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.converter.spec_to_ossie.converter import SpecToOssieConverter
from ossie_ontology.converter.linkml_to_ossie.converter import LinkmlToOssieConverter
from ossie_ontology.converter.ossie_to_linkml.converter import OssieToLinkmlConverter
```

## Scripts

### `scripts/palantir_to_ossie.py`

Converts a Palantir ontology export (a `.zip` archive or an already extracted folder containing a Palantir ontology JSON and a `data_sets` folder of one or more dataset spec JSON files) into an Ossie-compliant YAML representation, printed to stdout.

**Usage:**

```bash
uv run python scripts/palantir_to_ossie.py path/to/palantir_export.zip
# or an extracted folder:
uv run python scripts/palantir_to_ossie.py path/to/palantir_export/
```

Warnings are written to stderr; the Ossie YAML is written to stdout.

**Environment variables (optional):**

| Variable                  | Default    | Description                                              |
|---------------------------|------------|----------------------------------------------------------|
| `SNOWFLAKE_DATABASE_NAME` | `PALANTIR` | Snowflake database name used to qualify table references |
| `SNOWFLAKE_SCHEMA_NAME`   | `PALANTIR` | Snowflake schema name used to qualify table references   |

If already set in your environment they will be picked up automatically. To override them for a single run:

```bash
SNOWFLAKE_DATABASE_NAME=MY_DB SNOWFLAKE_SCHEMA_NAME=MY_SCHEMA \
  uv run python scripts/palantir_to_ossie.py path/to/palantir_export.zip
```

### `scripts/ossie_to_linkml.py`

Converts an Ossie ontology (YAML or JSON) into the LinkML schema that describes it, printed to stdout.

**Usage:**

```bash
uv run python scripts/ossie_to_linkml.py path/to/ossie.yaml
# Second argument is optional, and if provided will be used as the schema's `id`:
uv run python scripts/ossie_to_linkml.py path/to/ossie.yaml https://example.org/my-schema
```

### `scripts/linkml_to_ossie.py`

Converts a LinkML schema into an Ossie-compliant YAML representation of the ontology it describes, printed to stdout. The schema's `imports` are resolved from disk.

**Usage:**

```bash
uv run python scripts/linkml_to_ossie.py path/to/schema.yaml
```

A schema can load and still have errors and warnings against it. Errors and warnings are written to stderr, only fatal problems stop the run.

## Running the tests

```bash
uv run pytest
```

Regenerate pytest snapshots after an intentional output change:

```bash
uv run pytest --snapshot-update
```
