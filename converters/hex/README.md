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
