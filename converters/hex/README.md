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

**Hex → Ossie → Hex is lossless for the supported Hex surface.**

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
- `-d, --dialect <dialect>` — Optional. OSI dialect to pick from Ossie expressions. If omitted, the dialect is restored from `HEX` metadata or inferred from the Ossie document.

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
- `-d, --dialect <dialect>` — Required. Warehouse dialect for Ossie expressions.
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
from ossie_hex import convert_hex_to_ossie, convert_ossie_to_hex

ossie_yaml, warnings = convert_hex_to_ossie("hex_project/", dialect="snowflake")
files, warnings = convert_ossie_to_hex(ossie_yaml)  # {relative path: YAML str}
```

## Conversion

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
| Semantic Model | `extension_version`, `hex_dialect`, `views`                                                              |
| Dataset        | `display_name`, `source_kind`, `visibility`, `measures`, `undecomposable_relations`                      |
| Field          | `type`, `visibility`, `expr_sql`, `expr_calc`                                                            |
| Metric         | `model_id`, `measure_id`, `display_name`, `type`, `visibility`, `semi_additive`, `func`, `of`, `filters` |
| Relationship   | `relation_type`, `visibility`                                                                            |
