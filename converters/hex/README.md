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

<!-- markdownlint-disable MD033 -->

# Ossie ↔ Hex converter

Bidirectional, offline conversion between
[Apache Ossie][apache-ossie] and [Hex][hex-semantic-spec].

- **Export** (`ossie-hex export`): Ossie → Hex
- **Import** (`ossie-hex import`): Hex → Ossie

TODO(main): {description}

## Installation

This converter is distributed as a Python package. Install it with `uv` or
`pip`:

```bash
uv tool install ossie-hex

# Alternatively
pip install ossie-hex
```

Requires Python 3.11 or newer.

## Usage

### Command line

#### `export`

TODO(export): {description}

TODO(export): {code-signature}

TODO(export): {options}

TODO(export): {code-example}

#### `import`

TODO(import): {description}

TODO(import): {code-signature}

TODO(import): {options}

TODO(import): {code-example}

### Python API

#### `convert_ossie_to_hex`

Convert an Ossie semantic model(s) into a Hex semantic project(s).

```python
from ossie_hex import convert_ossie_to_hex

hex_projects, problems = convert_ossie_to_hex(
    input="ossie.yaml",
    output="hex/",
    dialect="snowflake",
)
```

Options:

- `input` — Required. Ossie YAML file to export.
- `output` — Optional. Directory where Hex YAML files are written. If omitted,
  the current working directory is used.
- `dialect` — Optional. Ossie dialect to pick from Ossie expressions. If
  omitted, the first dialect an expression declares is used, falling back to
  `ANSI_SQL`.

Returns: a tuple of

- `hex_projects`: A list of Hex semantic project(s).
- `problems`: A list of problems encountered.

## Conversion

The conversion between Ossie and Hex is a multi-step process that is not
one-to-one. It is designed such that the maximal set of information is
returned to the caller. Problems are returned in such a way that
the source can be identified and problem understood from the message alone.

### Phases

Conversion proceeds in three distinct phases, sometimes with sub-phases.

- `load`: Read file(s) from disk. Deserialize and parse file contents. Validate
  and construct in-memory representation(s).
- `convert`: Transform syntax. Validate and construct target domain
  model(s).
  - `analyze`: Parse expressions and validate semantic references.
  - `assign`: Decide where incongruities between source and target data should
    be resolved.
- `dump`: Serialize and encode in-memory representation(s) into the target
  format. Write file(s) to disk.

### Problems

Conversion reports problems encountered. The severity of a problem is one of:

- `fatal`: The problem causes invalidation that cannot be recovered from, or
  an unexpected internal error.
- `error`: The problem invalidates a definition which must be omitted from
  the result. The associated definition(s) have been omitted
  from the result.
- `warning`: The problem is a potential issue that probably should be addressed,
  but is not critical. The associated definitions may behave unexpectedly,
  but are included in the result.
- `info`: The problem is a general informational message that is not an issue.

Each problem contains a `cause_path` which identifies the key of the source
data where the problem occurred. The conversion `phase` in which the problem
occurred is also reported.

### Concepts

The table below shows how Ossie and Hex concepts correspond, and where a concept
in one format has no direct equivalent in the other. Backticks identify literal
fields in each format. Where a concept exists in Hex but is not supported in
Ossie, then data is preserved in the `HEX` custom extension on import. Where a
concept exists in Ossie but is not supported in Hex, then data is omitted on
export.

| Concept                   | Ossie                                          | Hex                                                                      |
| ------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------ |
| Container                 | Semantic model                                 | Semantic project                                                         |
| Logical entity            | Dataset                                        | Model                                                                    |
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

Data types translate between the two formats as follows, with notes where
conversion is not one-to-one.

| Ossie `datatype` | Hex `type`        | Notes                                                |
| ---------------- | ----------------- | ---------------------------------------------------- |
| `String`         | `string`          |                                                      |
| `Decimal`        | `number`          |                                                      |
| `Integer`        | `number`          |                                                      |
| `Float`          | `number`          |                                                      |
| `Boolean`        | `boolean`         |                                                      |
| `Date`           | `date`            |                                                      |
| `DateTime`       | `timestamp_naive` |                                                      |
| `DateTimeTz`     | `timestamp_tz`    |                                                      |
| `Opaque`         | `null`            |                                                      |
| `Opaque`         | `other`           |                                                      |
| `Time`           | `other`           | No Hex equivalent.                                   |
| _omitted_        | `string`/`number` | Warning. String for dimensions, number for measures. |

### Custom extension

TODO(custom-extension): {description}

TODO(custom-extension): {table}

[apache-ossie]: https://ossie.apache.org/
[hex-semantic-spec]: https://learn.hex.tech/docs/connect-to-data/semantic-models/semantic-authoring/modeling-specification
