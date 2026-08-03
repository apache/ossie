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

# Ossie ↔ Hex converter

Bidirectional, offline conversion between [Apache Ossie](https://ossie.apache.org/) and [Hex](https://learn.hex.tech/docs/connect-to-data/semantic-models/semantic-authoring/modeling-specification).

- **Export** (`ossie-hex export`): Ossie → Hex
- **Import** (`ossie-hex import`): Hex → Ossie

A Hex semantic project is a directory of YAML resource files (models and views). This converter maps an Ossie semantic model to/from that layout.

During export, datasets and fields become models and dimensions, relationships become per-model relations, and metrics become per-model measures. Import performs the reverse conversion.

**Hex → Ossie → Hex is lossless.** The reverse is not.

Invalid input raises a `ConversionError`. Anything Hex cannot represent is
reported as a warning rather than dropped quietly.

## Installation

This converter is distributed as a Python package. Install it with `uv` or `pip`:

```bash
uv tool install ossie-hex

# Alternatively
pip install ossie-hex
```

Requires Python 3.11 or newer.

## Usage

### Command line

#### `export`

Convert an Ossie semantic model into a Hex semantic project.

```bash
ossie-hex export -i <file> -o <directory> \
  [--model <name>] \
  [--base-model <dataset>] \
  [--dialect <dialect>]
```

Options:

- `-i, --input <file>` — Required. Ossie YAML file to export.
- `-o, --output <directory>` — Required. Directory where Hex YAML files are written.
- `--model <name>` — Optional. Ossie semantic model to export. If omitted, the first model is exported and a warning is emitted when the document contains multiple models.
- `--base-model <dataset>` — Optional. Dataset to receive metrics that cannot be attributed to a single dataset.
- `-d, --dialect <dialect>` — Optional. Ossie dialect to pick from Ossie expressions. If omitted, the dialect the document declares is used, falling back to `ANSI_SQL`.

Example:

```bash
ossie-hex export -i model.yaml -o hex_project/ \
  --model revenue \
  --base-model orders \
  --dialect snowflake
```

#### `import`

Convert Hex semantic project resource files into Ossie YAML.

```bash
ossie-hex import -i <directory> --dialect <dialect> \
  [-o <file>] \
  [--name <name>]
```

Options:

- `-i, --input <directory>` — Required. Directory containing the Hex YAML files.
- `-d, --dialect <dialect>` — Required. Ossie dialect the project's SQL is written in. A Hex project does not record one, so the converted expressions can only be tagged with what you supply here, and it becomes the document's declared dialect.
- `-o, --output <file>` — Optional. Ossie YAML output file. If omitted, output is written to stdout.
- `--name <name>` — Optional. Name to assign to the imported Ossie model. If omitted, the project directory name is used.

Examples:

```bash
# Write Ossie YAML to a file
ossie-hex import -i hex_project/ -o model.yaml \
  --dialect snowflake \
  --name my_model

# Write Ossie YAML to stdout
ossie-hex import -i hex_project/ --dialect snowflake
```

### Python API

```python
from ossie import OSIDialect
from ossie_hex import convert_hex_to_ossie, convert_ossie_to_hex

ossie_yaml, warnings = convert_hex_to_ossie(
    hex_files,  # {file name: YAML str}
    dialect=OSIDialect.ANSI_SQL,
    model_name="my_model",
)

hex_files, warnings = convert_ossie_to_hex(ossie_yaml)  # {file name: YAML str}
```

## Problems

Conversion raises a `ConversionError` when the process cannot produce reasonable output:

In Hex → Ossie,

- The Hex project directory is missing or contains no YAML resources.

In Ossie → Hex,

- The Ossie file is missing.
- Compiled Hex resource fails validation.
- Unique identifiers that normalize to the same Hex ID.
- Metrics cannot be assigned to a model and `--base-model` is not given.
- Custom extension data is malformed.

Conversion emits a `ConversionWarning` when the output is lossy:

- [Concepts](#concepts) that are supported in one format but not the other.
- [Data types](#data-types) that are not one-to-one.

## Conversion

### Concepts

The table below shows how Ossie and Hex concepts correspond, and where a concept in one format has no direct equivalent in the other. Backticks identify literal fields in each format. Where a concept exists in Hex but is not supported in Ossie, then data is preserved in the `HEX` custom extension on import. Where a concept exists in Ossie but is not supported in Hex, then data is omitted on export.

| Concept                   | Ossie                                          | Hex                                                                      |
| ------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------ |
| –                         | Semantic model                                 | Semantic project                                                         |
| –                         | Dataset                                        | Model                                                                    |
| Data source               | Dataset `source`                               | Model `base_sql_table` or `base_sql_query`                               |
| Primary key               | Dataset `primary_key` (simple, composite)      | _Not supported; translated to Dimension `unique: true`; drops composite_ |
| Unique key                | Dataset `unique_keys` (simple, composite)      | Dimension `unique: true` (simple); _Composite not supported_             |
| Row-level attribute       | Field                                          | Dimension                                                                |
| Data type                 | Field / Metric `datatype`                      | Dimension / Measure `type`                                               |
| Connections               | Relationship                                   | Relation                                                                 |
| Arbitrary join condition  | _Not supported beyond column-pair equality._   | Relation `join_sql`                                                      |
| Explicit join cardinality | _Not supported beyond many-to-one direction._  | Relation `type`                                                          |
| Quantitative measures     | Metric                                         | Measure                                                                  |
| Unique identifier         | Dataset / Field / Metric / Relationship `name` | Model / Dimension / Measure / Relation `id`                              |
| Display metadata          | Field / Metric `description`, Field `label`    | Model / Dimension / Measure / Relation `name`, `description`             |
| AI context                | `ai_context`                                   | _Not supported._                                                         |
| Custom extension          | `custom_extensions`                            | _Not supported._                                                         |
| Cross-dataset reference   | `dataset.field` qualifier                      | `${relation.dimension}` qualifier                                        |
| Time role                 | Dimension `is_time`, temporal `datatype`       | Temporal `type`; _Additional time metadata not supported._               |
| Curated view              | _Not supported._                               | View                                                                     |
| Visibility                | _Not supported._                               | Model / Dimension / Measure / Relation `visibility`                      |
| Calculation formula       | _Not supported._                               | Measure `func_calc` / Dimension `expr_calc`                              |
| Structured filter         | _Not supported._                               | Measure `filters`                                                        |
| Semi-additive measure     | _Not supported._                               | Measure `semi_additive`                                                  |

### Data types

Data types translate between the two formats as follows, with notes where conversion is not one-to-one.

| Ossie `datatype` | Hex `type`        | Notes                                                |
| ---------------- | ----------------- | ---------------------------------------------------- |
| `String`         | `string`          |                                                      |
| `Decimal`        | `number`          |                                                      |
| `Integer`        | `number`          | Returns as `Decimal`.                                |
| `Float`          | `number`          | Returns as `Decimal`.                                |
| `Boolean`        | `boolean`         |                                                      |
| `Date`           | `date`            |                                                      |
| `DateTime`       | `timestamp_naive` |                                                      |
| `DateTimeTz`     | `timestamp_tz`    |                                                      |
| `Opaque`         | `null`            | Preserved in the `HEX` custom extension.             |
| `Opaque`         | `other`           |                                                      |
| `Time`           | `other`           | No Hex equivalent.                                   |
| _omitted_        | `string`/`number` | Warning. String for dimensions, number for measures. |

### Custom extension

Hex features that Ossie cannot express are preserved in an Ossie custom extension (vendor name `HEX`) so they survive a round trip. The extension data is a JSON object. Data contents are versioned with a key at the document's top-level custom extensions field. The keys used at each scope are listed below.

| Scope          | Keys                                                                                                     |
| -------------- | -------------------------------------------------------------------------------------------------------- |
| Semantic Model | `extension_version`, `views`                                                                             |
| Dataset        | `display_name`, `source_kind`, `visibility`, `dimensions`, `measures`, `relations`                       |
| Field          | `type`, `visibility`, `expr_sql`                                                                         |
| Metric         | `model_id`, `measure_id`, `display_name`, `type`, `visibility`, `semi_additive`, `func`, `of`, `filters` |
| Relationship   | `relation_type`, `visibility`                                                                            |
