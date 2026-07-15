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

A two-way converter between [Ossie semantic models](../../core-spec/spec.md) and [Apache Polaris](https://polaris.apache.org/) catalogs.

Apache Polaris is an open-source catalog for Apache Iceberg. This converter communicates with Polaris via the Iceberg REST Catalog API to import catalog metadata into Ossie format and export Ossie models back into Polaris.

## Building

```bash
mvn clean package
```

Requires Java 11+.

## Usage

### Import (Polaris → Apache Ossie)

Reads all namespaces and tables from a Polaris catalog and generates an Ossie YAML file.

```bash
java -jar target/ossie-polaris-converter-0.1.0-SNAPSHOT.jar import \
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
java -jar target/ossie-polaris-converter-0.1.0-SNAPSHOT.jar export \
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
| Schema fields | `field` with `ANSI_SQL` dialect expression |
| `identifier-field-ids` | `dataset.primary_key` |
| Temporal types (`timestamp`, `timestamptz`, `date`, `time`) | `field.dimension.is_time: true` |
| Table properties | `dataset.custom_extensions` (vendor: `COMMON`) |

### Export (Apache Ossie → Polaris)

| Ossie | Polaris / Iceberg |
|-----|-------------------|
| `semantic_model` | Namespace |
| `dataset` | Table |
| `dataset.source` | Stored in table property `osi.source` |
| `dataset.primary_key` | `identifier-field-ids` |
| `field` | Schema column |
| `field.dimension.is_time: true` | `timestamptz` type |
| `dataset.description` | Table property `comment` |

### Type Inference (Export)

Since Ossie fields are expression-based and don't carry explicit types, the exporter infers Iceberg types using:

1. **Round-trip hints** — if a field description starts with `Iceberg type:` (produced by the importer), that type is used directly.
2. **Time dimensions** — fields with `dimension.is_time: true` map to `timestamptz`.
3. **Name conventions** — `*_id` → `long`, `*_date` → `date`, `*_at`/`*_timestamp` → `timestamptz`, `*_amount`/`*_price` → `decimal(18,2)`, `*_count`/`quantity` → `int`, `is_*`/`has_*` → `boolean`.
4. **Default** — `string`.

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

- [SnakeYAML 2.2](https://bitbucket.org/snakeyaml/snakeyaml/) — Ossie YAML parsing and generation
- [Jackson Databind 2.17](https://github.com/FasterXML/jackson-databind) — JSON handling for Polaris REST API
- [JUnit 5](https://junit.org/junit5/) — testing

## License

Apache License 2.0 — see [LICENSE](../../LICENSE).
