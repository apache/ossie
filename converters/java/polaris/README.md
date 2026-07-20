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

# Apache Ossie Polaris Converter

A two-way converter between [Ossie semantic models](../../../core-spec/spec.md) and [Apache Polaris](https://polaris.apache.org/) catalogs.

Apache Polaris is an open-source catalog for Apache Iceberg. This converter communicates with Polaris via the Iceberg REST Catalog API to import catalog metadata into Ossie format and export Ossie models back into Polaris.

## Building

```bash
cd converters/java
mvn -pl polaris -am clean package
```

The generated converter bytecode targets Java 11. Building the reactor requires
Java 17+ and Maven 3.9.12+ because `ossie-common` generates the model classes.
The resulting Polaris jar is self-contained and includes `ossie-common`.

## Usage

### Import (Polaris → Apache Ossie)

Reads all namespaces and tables from a Polaris catalog and generates an Ossie YAML file.

```bash
java -jar polaris/target/ossie-polaris-converter-0.1.0-SNAPSHOT-all.jar import \
  --url http://localhost:8181 \
  --catalog my_catalog \
  --client-id <client-id> \
  --client-secret <client-secret> \
  -o output.yaml
```

Each Polaris namespace becomes a separate Ossie semantic model containing datasets for every table in that namespace.

### Export (Apache Ossie → Polaris)

Reads an Ossie YAML file and creates namespaces and Iceberg tables in a Polaris catalog.

```bash
java -jar polaris/target/ossie-polaris-converter-0.1.0-SNAPSHOT-all.jar export \
  --url http://localhost:8181 \
  --catalog my_catalog \
  --client-id <client-id> \
  --client-secret <client-secret> \
  model.yaml
```

Each Ossie semantic model becomes a Polaris namespace, and each dataset becomes an Iceberg table.

### Options

| Option | Description |
|--------|-------------|
| `--url URL` | Polaris server URL (required) |
| `--catalog CATALOG` | Catalog name (required) |
| `--client-id ID` | OAuth2 client ID |
| `--client-secret SECRET` | OAuth2 client secret |
| `--token TOKEN` | Pre-existing bearer token (alternative to client credentials) |
| `-o FILE` | Output file for import mode (default: stdout) |

## Mapping Reference

### Import (Polaris → Apache Ossie)

| Polaris / Iceberg | Ossie |
|-------------------|-----|
| Namespace | `semantic_model` (name, description) |
| Table | `dataset` (name) |
| Table location (`catalog.namespace.table`) | `dataset.source` |
| Schema fields | `field` with `ANSI_SQL` dialect expression and logical `datatype` |
| `identifier-field-ids` | `dataset.primary_key` |
| Temporal types (`timestamp`, `timestamptz`, `date`, `time`) | `field.dimension.is_time: true` |
| Exact Iceberg type JSON | `field.custom_extensions` (vendor: `POLARIS`) |
| Table properties | `dataset.custom_extensions` (vendor: `COMMON`) |

### Export (Apache Ossie → Polaris)

| Ossie | Polaris / Iceberg |
|-----|-------------------|
| `semantic_model` | Namespace |
| `dataset` | Table |
| `dataset.source` | Stored in table property `osi.source` |
| `dataset.primary_key` | `identifier-field-ids` |
| `field.datatype` | Schema column type |
| Untyped `field.dimension.is_time: true` | `timestamptz` fallback type |
| `dataset.description` | Table property `comment` |

### Data Types

The importer maps Iceberg physical types to Ossie logical types as follows:

| Iceberg type | Ossie `datatype` |
|---|---|
| `boolean` | `Boolean` |
| `int`, `long` | `Integer` |
| `float`, `double` | `Float` |
| `decimal(P,S)` | `Decimal` |
| `date` | `Date` |
| `time` | `Time` |
| `timestamp`, `timestamp_ns` | `DateTime` |
| `timestamptz`, `timestamptz_ns` | `DateTimeTz` |
| `string` | `String` |
| `unknown` | omitted |
| UUID, binary, fixed, variant, spatial, and nested types | `Opaque` |

Because logical types omit physical details such as integer width, decimal precision,
timestamp precision, and nested structure, the importer also stores every exact Iceberg
type in a field-level `POLARIS` custom extension. Nested field IDs are regenerated when
creating a new table so the resulting Iceberg schema remains valid.

The exporter resolves types in this order:

1. **Exact extension type** — restores the complete Iceberg type from a `POLARIS` extension.
2. **Ossie `datatype`** — maps portable logical types to Iceberg defaults.
3. **Legacy description hint** — restores `Iceberg type:` descriptions from older converter output.
4. **Time role** — an untyped field with `dimension.is_time: true` maps to `timestamptz`.
5. **Name conventions** — retains the previous `*_id`, `*_date`, numeric, boolean, and other heuristics.
6. **Default** — `string`.

`datatype` and `dimension.is_time` are independent: an explicitly typed `String` time
dimension remains an Iceberg `string`, while an explicitly typed `DateTime` field becomes
`timestamp` even when it is not assigned a time-dimension role. A `Decimal` without an
exact extension uses `decimal(18,2)` with a warning because Ossie does not specify precision
or scale. `Opaque` requires an exact extension for lossless export; otherwise legacy
inference is used with a warning.

## Architecture

```
                         ┌──────────────────┐
                         │  Polaris REST    │
                         │     Catalog      │
                         └────────┬─────────┘
                                  │
                         ┌────────┴─────────┐
                         │  PolarisClient   │  Iceberg REST API
                         └────────┬─────────┘
                                  │
                  ┌───────────────┼───────────────┐
                  │                               │
         ┌────────┴─────────┐           ┌─────────┴─────────┐
         │ PolarisImporter  │           │ PolarisExporter   │
         │ (Polaris → Ossie)│           │ (Ossie → Polaris) │
         └────────┬─────────┘           └─────────┬─────────┘
                  │                               │
         ┌────────┴─────────┐           ┌─────────┴─────────┐
         │ OsiYamlGenerator │           │  OsiModelParser   │
         └──────────────────┘           └───────────────────┘
```

## Dependencies

- `ossie-common` — schema-generated Ossie Java model
- Jackson 2.18 — Ossie YAML binding and JSON handling for the Polaris REST API
- [JUnit 5](https://junit.org/junit5/) — testing

## License

Apache License 2.0 — see [LICENSE](../../../LICENSE).
