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

Converters between Ossie, Palantir, Spec, and RelationalAI ontology formats.

| Converter               | Direction |
|-------------------------|-----------|
| `palantir_to_ossie`     | Palantir ontology → Ossie model |
| `ossie_to_spec`         | Ossie model → Spec YAML |
| `spec_to_ossie`         | Spec YAML → Ossie model |
| `ossie_to_relationalai` | Ossie model → RelationalAI (PyRel) |

### The `relationalai` extra

> **`relationalai` is proprietary and declares no license.** Its PyPI
> distribution carries no `License` field, no license classifier and no
> `LICENSE` file, and the [project page](https://pypi.org/project/relationalai/)
> lists none either — so it grants no use or redistribution rights by default.
> Get the applicable terms from RelationalAI (`support@relational.ai`) before
> installing it. Ossie is Apache-2.0 and neither bundles nor depends on it.
>
> Installing the package is also not enough to *run* what this converter emits.
> That needs a Snowflake account with the RelationalAI Native App installed from
> the [Marketplace](https://app.snowflake.com/marketplace/listing/GZTYZOOIX8H/relationalai-relationalai),
> and access enabled by RelationalAI on request — see the
> [setup guide](https://docs.relational.ai/manage/get-started/install/). Ossie
> never executes the generated source: it converts and compiles in-process,
> offline, against no engine.

`ossie_to_relationalai` is the only converter that needs a vendor SDK. Because
that SDK is non-free, it is kept out of the base install entirely and gated
behind an opt-in extra, so nobody acquires it without asking for it:

```bash
pip install "apache-ossie-ontology[relationalai]"    # opt in, having read the above
pip install apache-ossie-ontology                    # everything else, no vendor SDK
```

Nothing reachable from `ossie_ontology/__init__.py` imports `relationalai`, so
everything else — parsing Ossie, converting Palantir, reading and writing the
spec — installs and runs without it. `tests/test_optional_extra.py` asserts that
boundary in a subprocess with the SDK masked, so the base install cannot start
depending on it by accident. The converter's own tests skip themselves when it
is absent.

The PyRel-side model it targets — `OntologyModel` and its bindings, roles and
CSV plumbing — lives under `ossie_ontology/vendor/relationalai/`,
next to `ossie_ontology/vendor/palantir/`. The converter package itself holds
only the translation. The one piece that sits elsewhere is the formula emitter,
`ossie_ontology/expr/formula/visitor/converter.py`, which stays with the other
formula visitors it is a variant of.

It converts an `OssieOntology` into an in-memory `OntologyModel`, which can then
be serialized to PyRel source:

```python
from pathlib import Path

from relationalai.semantics.metamodel.pyrel_codegen import to_pyrel

from ossie_ontology.parser import OssieParser
from ossie_ontology.converter.ossie_to_relationalai import OssieToRelationalAIConverter

ontology = OssieParser().parse(Path("model.yaml"))

model = OssieToRelationalAIConverter.convert(ontology)
Path("model_pyrel.py").write_text(to_pyrel(model.base_model().to_metamodel()))
```

`OssieParser` parses and validates `derived_by` and `requires` expressions into
an AST by default, which is what the conversion needs — an unparsed formula is
skipped and never reaches PyRel. To keep formulas as raw text instead, pass the
plain `FormulaFactory` and `MappingFormulaFactory` from `ossie_ontology.model`.
`SpecToOssieConverter` and `PalantirToOssieConverter` take the same argument and
default the same way.

Two things about the environment this needs. Constructing a model makes
`relationalai` resolve its configuration, so a `raiconfig.yaml` must be present.
And each dataset's `source` is read from the configured connection to get its
column types, so the call above needs one that can reach those tables.

To convert without a warehouse, pass a different `table_provider`. The default,
`warehouse_table`, resolves each dataset's `source` against the connection —
which is what gets the column identifiers right, since Snowflake folds an
unquoted name to upper case. The alternative, `declared_table`, declares the
columns from the Ossie spec instead and reads nothing:

```python
from ossie_ontology.converter.ossie_to_relationalai import declared_table

model = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
```

Nothing leaves the process, so the tables the generated source names need not
exist. The tradeoff is the one `warehouse_table` avoids: the declared
identifiers have to match how the table was actually created.

That is how the test suite runs — see `tests/conftest.py` for the offline config
it pins and the network guard it installs, and `tests/relationalai/test_converter.py`
for the rest of the offline setup.

`table_provider` is also the extension point for reading rows from somewhere
else entirely — from inline CSV, for example, so a test corpus needs no
warehouse. This package ships no such provider and has no CSV handling at all,
because reading rows is not part of converting an ontology.

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

## Running the tests

```bash
uv run pytest
```

Regenerate pytest snapshots after an intentional output change:

```bash
uv run pytest --snapshot-update
```
