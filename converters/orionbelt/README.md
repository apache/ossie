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

# apache-ossie-orionbelt

Bidirectional converter between **OBML** (OrionBelt Markup Language) semantic
models and **Ossie** ([Apache Ossie](https://ossie.apache.org/)),
the open standard for portable semantic models (metrics, dimensions,
relationships).

This package is licensed under **Apache-2.0** and may be used freely. It is the
OrionBelt converter in the Ossie converter ecosystem. The canonical source is
developed in the
[orionbelt-semantic-layer](https://github.com/ralfbecher/orionbelt-semantic-layer)
repository (under `packages/ossie-orionbelt`) and published to PyPI from there;
file issues and contributions upstream.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Install

```bash
pip install apache-ossie-orionbelt
```

Optional deep OBML semantic validation (cycles, duplicate names, invalid refs)
via the full OrionBelt engine:

```bash
pip install "apache-ossie-orionbelt[obml-validation]"
```

Without that extra, OBML validation runs JSON-schema checks only and emits a
warning for the deeper semantic pass.

## CLI

A single `ossie-orionbelt` command with two subcommands (mirroring `ossie-dbt`):

| Subcommand | Direction | In | Out |
|---------|-----------|----|----|
| `obml-to-ossie` | OBML -> Ossie core-spec | OBML YAML | Ossie YAML |
| `obml-to-ossie --ontology` | OBML -> Ossie ontology | OBML YAML | Ossie ontology YAML |
| `ossie-to-obml` | Ossie core-spec -> OBML | Ossie YAML | OBML YAML |

```bash
ossie-orionbelt obml-to-ossie -i model.obml.yaml -o model.ossie.yaml
ossie-orionbelt obml-to-ossie --ontology -i model.obml.yaml -o model.ontology.yaml
ossie-orionbelt ossie-to-obml -i model.ossie.yaml -o model.obml.yaml
```

`-i/--input` and `-o/--output` are required. Each subcommand prints conversion
warnings and a validation summary to stderr, and exits non-zero when the
produced document fails schema validation (unless `--no-validate`). Run
`ossie-orionbelt --help` or `ossie-orionbelt obml-to-ossie --help` for the full
option list.

## Python API

```python
import yaml
from ossie_orionbelt import OBMLtoOssie, OssietoOBML, validate_ossie

obml = yaml.safe_load(open("model.obml.yaml"))
ossie = OBMLtoOssie(obml, "sales", "Sales model").convert()
result = validate_ossie(ossie)
assert result.valid

obml_again = OssietoOBML(ossie).convert()
```

## Vendor extensions

Ossie `custom_extensions` carry vendor-tagged payloads. This converter:

- emits OrionBelt/OBML-proprietary data under the **`ORIONBELT`** vendor on OBML
  to Ossie (OBML-only filters, settings, owner, refresh, type info, etc.);
- stashes Ossie-native fields that OBML can't represent (unique keys, field
  labels, leftover `ai_context`) under the **`Ossie`** vendor when going Ossie to
  OBML, restoring them to first-class Ossie fields on the way back;
- **preserves third-party vendor extensions verbatim** (e.g. `SNOWFLAKE`,
  `DBT`, `SALESFORCE`, `GOODDATA`) at the model, dataset, field, and
  measure/metric levels, so a full Ossie to OBML to Ossie roundtrip keeps the
  original vendor and data. Ossie has no separate dimension entity, so an OBML
  dimension's foreign extensions surface on its Ossie field.

Legacy `COMMON` / `OBSL` tags from earlier converter versions are still accepted
on read.

## Limitations / unsupported constructs

Some OBML constructs have no native Ossie equivalent and are carried in vendor
`custom_extensions` (`obml_*` payloads) so they round-trip without loss back to
OBML, but are not interpreted by other Ossie consumers:

- **Many-to-many joins** - represented in OBML join cardinality; flagged on
  export.
- **Named secondary join paths** - OBML's multiple join paths between the same
  pair of objects are an OBML-specific topology feature.
- **Measures / metrics and column-level value concepts in the ontology layer** -
  not represented in the Ossie ontology export.
- **Ossie metrics with no OBML representation** - a metric whose only expression is
  in a non-SQL dialect (`MDX`, `TABLEAU`, `MAQL`), or whose SQL expression cannot
  be decomposed into OBML measures/metrics, is **not** dropped: the original Ossie
  metric is preserved verbatim in a model-level `Ossie`-vendor `custom_extension`
  (`obml_unconverted_metrics`) and re-emitted on OBML to Ossie, so the Ossie to OBML
  to Ossie roundtrip stays lossless. A `LOSSY:` warning is raised for each such
  metric because it is **not queryable through OBML**. SQL expressions in the
  `ANSI_SQL`, `SNOWFLAKE`, and `DATABRICKS` dialects are all read on import.

Ossie v0.1.x inputs are accepted on read via a legacy normalization shim; output
targets Ossie **v0.2.0.dev0**.

See [`ossie_obml_mapping_analysis.md`](./ossie_obml_mapping_analysis.md) for the
full OBML <-> Ossie core-spec mapping and
[`ossie_obml_ontology_mapping_analysis.md`](./ossie_obml_ontology_mapping_analysis.md)
for the ontology-layer mapping and its documented gaps.

## Development

```bash
uv sync          # install
uv run pytest    # run the test suite (includes a TPC-DS baseline)
uv run ruff check && uv run mypy src/ossie_orionbelt
```
