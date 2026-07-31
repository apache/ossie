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

# Contributing to the Ossie–Hex converter

This document covers development of the `ossie-hex` package. Repository-wide
contribution, review, and Apache release requirements are documented in the
[project contribution guide](../../CONTRIBUTING.md).

## Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- A checkout of the complete Apache Ossie repository

```bash
cd converters/hex
uv sync
```

## Development workflow

The implementation is split by responsibility. The two conversion directions are
packages of their own, each reading and writing its own formats and holding one
module per resource it converts, sitting on two shared layers:

- `src/ossie_hex/hex_to_ossie/`: Import (Hex → Ossie)
- `src/ossie_hex/ossie_to_hex/`: Export (Ossie → Hex)
- `src/ossie_hex/hex_types/`: Hex semantic spec models, datatype correspondence,
  and custom-extension payloads
- `src/ossie_hex/ossie_types/`: Ossie patterns, constants, and loaded-document
  types
- `src/ossie_hex/util/`: errors, warnings, YAML 1.2 load/dump, dialect mapping,
  and SQL reference and join rewriting
- `src/ossie_hex/cli.py`: `import` and `export` commands

Add or update fixtures under `tests/fixtures/` and keep conversion behavior
covered in both directions. Hex-only information that has no Ossie equivalent
should be stored in the `HEX` custom extension so that Hex → Ossie → Hex remains
lossless.

### Useful knowledge

Three invariants are easy to break and worth keeping in mind when changing either
direction:

- **Ossie names are not Hex IDs.** Ossie names are free-form; Hex IDs are
  lowercase and restricted. Every dataset and field name is resolved to its Hex
  ID up front, and relationship targets, join columns, metric qualifiers, and
  expression references all have to go through that mapping. Comparing a raw
  Ossie name against an already-coerced ID silently produces refs that point at
  nothing.
- **Hex reaches another model through a relation, not a model ID.** Ossie
  qualifies a foreign column as `dataset.field`, but the equivalent Hex ref is
  `${relation.dimension}`, naming a relation declared on the model holding the
  expression. Two relations can target the same model, so the mapping runs from
  target dataset to relation ID and a dataset with no relation is simply
  unreachable. This is why relations are built before dimensions and measures:
  their IDs are needed to rewrite expressions.

## Verification

Lint and check formatting:

```bash
uv run ruff check
uv run ruff format --check
```

Apply automatic fixes and formatting:

```bash
uv run ruff check --fix
uv run ruff format
```

Run the complete converter test suite:

```bash
uv run pytest
```

Run one file or test while iterating:

```bash
uv run pytest tests/<file>.py
uv run pytest tests/<file>.py::<test>
```

The following commands exercise the installed CLI in both directions:

```bash
uv run ossie-hex import \
  --input tests/fixtures/minimal_hex \
  --dialect snowflake \
  --name demo \
  --output /tmp/ossie-hex-demo.yaml

uv run ossie-hex export \
  --input /tmp/ossie-hex-demo.yaml \
  --dialect snowflake \
  --output /tmp/ossie-hex-demo
```

These are the commands used to verify the initial converter implementation:

```bash
uv sync
uv run pytest
uv run ossie-hex import \
  --input tests/fixtures/minimal_hex \
  --dialect snowflake \
  --name demo
```

CI runs installation, linting, formatting, and testing on Python 3.11 through 3.14 using
`.github/workflows/converter-hex-ci.yml`.

## Building distributions

Build the source distribution and wheel from this directory:

```bash
uv build
```

The artifacts are written to `dist/`. Before publishing, inspect their metadata
and contents:

```bash
uvx twine check dist/*
uv run python -m zipfile -l dist/*.whl
tar -tf dist/*.tar.gz
```

Also test installation in a clean environment. This will only work from PyPI
after the `apache-ossie` dependency has been published:

```bash
uv venv /tmp/ossie-hex-release-test
uv pip install --python /tmp/ossie-hex-release-test/bin/python dist/*.whl
/tmp/ossie-hex-release-test/bin/ossie-hex --help
```

## Publishing

Publishing is deferred to the Apache Ossie project, which governs the broader release cycle. Contributors should not publish this package independently.
