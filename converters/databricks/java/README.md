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

# Apache Ossie Databricks Converter

Bidirectional, offline conversion between an [Apache Ossie](https://github.com/apache/ossie)
semantic model and a Databricks
[Unity Catalog Metric View](https://docs.databricks.com/aws/en/metric-views/) (YAML `1.1`). Pure
YAML text in, YAML text out: it reads and writes the two formats as parsed maps and lists.

The directions are named from the Apache Ossie model's point of view, matching the
[Python converter](../python/README.md):

- **Export** (`OssieToMetricView`): Apache Ossie -> Metric View (one fact `source` with a nested
  `joins` tree and a flat `dimensions` list).
- **Import** (`MetricViewToOssie`): Metric View -> Apache Ossie. Metric-View-only features Apache
  Ossie has no native field for are preserved in `custom_extensions[DATABRICKS]`, so
  `MV -> Apache Ossie -> MV` is lossless.

On **export** (Apache Ossie -> Metric View), Apache Ossie features with no Metric View slot --
relationship `ai_context`, the non-`synonyms` members of a field/metric `ai_context` object,
`dimension.is_time`, foreign-vendor `custom_extensions` -- are **dropped with a notice**. An
expression prefers the `DATABRICKS` dialect, then `ANSI_SQL`; the other dialect alternatives are
ignored (no notice) when a supported one is present, while a field or metric with no supported
dialect is dropped with a notice. A measure that names a dataset reached by more than one join
path (a diamond) is likewise dropped with a notice, because a bare dataset reference cannot be
unambiguously qualified; and a second `DATABRICKS` `custom_extensions` entry on one object is
ignored with a notice (the first one wins) rather than rejected. On **import** (Metric View ->
Apache Ossie), Metric-View-only
features (`filter`, `parameters`, `materialization`, per-column `format`, measure `window` /
`partition`) are instead **preserved** in `custom_extensions[DATABRICKS]`, so
`MV -> Apache Ossie -> MV` is lossless. Any input that breaks a [requirement](#requirements)
**raises a `ConversionException`** -- the converter never silently drops a field or produces an
invalid result.

## Requirements

- **Java 21+**
- **Maven 3.6+** -- required to build the jar

## Building

Build the self-contained executable jar from source:

```bash
mvn clean package
```

This produces `target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar` with all dependencies
(Jackson and SnakeYAML) bundled.

## Usage

### Command line

```bash
# export: Apache Ossie -> Metric View
java -jar target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar export model.yaml -o view.yaml

# import: Metric View -> Apache Ossie
java -jar target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar import view.yaml -o model.yaml
```

With no `-o`, output goes to stdout. `--source` (export only) picks the fact/grain (default: the
FK-sink dataset; naming a coarser-grain dataset produces `one_to_many` joins); `--name` (import
only) sets the Apache Ossie model name (default: the source's last identifier). Passing a command
its counterpart's flag is an error rather than a silent reinterpretation. Conversion notices
(features dropped on export) are written to stderr; a non-convertible input exits non-zero.

### Java API

```java
import org.apache.ossie.converter.databricks.OssieConverter;

// export: Apache Ossie -> Metric View (optionally choose the fact/grain; default: the FK-sink
// dataset -- naming a coarser-grain dataset produces one_to_many joins)
OssieConverter.Result view = OssieConverter.convertOssieToMetricView(ossieYaml, "orders");

// import: Metric View -> Apache Ossie (optionally name the model; default: the source's last part)
OssieConverter.Result ossie = OssieConverter.convertMetricViewToOssie(metricViewYaml, "sales");
```

Each `Result` carries the output YAML (`result.yaml`) and any notices raised (`result.notices`,
the features dropped on export). A broken [requirement](#requirements) throws a
`ConversionException` instead.

## Mapping

Each row maps in both directions; the **Notes** flag where a behavior is specific to
**export** (Apache Ossie -> Metric View) or **import** (Metric View -> Apache Ossie).

| Apache Ossie | Metric View (v1.1) | Notes |
|---|---|---|
| `semantic_model.description` | `comment` | Model-level description only. |
| root dataset | `source` | The fact/grain. |
| other `datasets` | nested `joins[]` | Export: the relationship graph is reassembled into the join tree; a dataset reached by two paths (a diamond) fans out into one aliased join per path. |
| `relationship` `from_columns`/`to_columns` | join `on` (differing names) / `using` (shared names) | Decomposed into columns on import; rebuilt into `on`/`using` on export. A join `on` that is non-equi, function-wrapped, or carries an extra filter has no equi-join relationship form, so on import it is preserved verbatim in the model-level `complex_joins` stash (under `custom_extensions[DATABRICKS]`) instead of a relationship, and export rebuilds it. |
| `relationship.from`/`to` direction | join `cardinality` | Export: source on the many (`from`) side -> `many_to_one`; on the one (`to`) side -> `one_to_many`. |
| `dataset.primary_key` / `unique_keys` | join `rely.at_most_one_match` | Both directions: export sets `at_most_one_match` when a key covers the join columns; import recovers a `unique_keys` from it. |
| `dataset.fields[]` | `dimensions[]` | Export: fields flatten into one list and a joined column is qualified by its full join path (`customer.c_name`; `customer.region.r_name` when nested). |
| `field.expression.dialects[]` | `expr` | Export: prefer the `DATABRICKS` dialect, else `ANSI_SQL`; other alternatives are ignored when a supported one is present, and a field with no supported dialect is dropped with a notice. |
| `metrics[]` | `measures[]` | Fact columns are referenced bare (`SUM(amount)`). A joined column is addressed by dataset name in Apache Ossie and by its full join path in the Metric View, so export expands `SUM(region.population)` to `SUM(customer.region.population)` and import maps it back. |
| `field.label` | dimension `display_name` | A measure's `display_name` has no `label` on the Apache Ossie metric shape, so it rides in the stash instead (see the `custom_extensions` row). |
| `field` / `metric` `description` | `comment` | |
| `ai_context.synonyms` | `synonyms` | Only `synonyms`: every other member of an `ai_context` object is dropped with a notice. |
| `custom_extensions[DATABRICKS]` | `filter`, `parameters`, `materialization`, per-column `format`, measure `window` / `partition` / `display_name` | Import stashes Metric-View-only features here; export restores them -- keeping `MV -> Apache Ossie -> MV` lossless. |

## Requirements

Conversion throws a `ConversionException` (rather than guessing or emitting something invalid) when
an input breaks one of these:

- the Metric View `version` is not `1.1`;
- a `source` is not a 3-part `catalog.schema.table` name or a `SELECT`/`WITH` subquery;
- the relationship graph is not acyclic and resolvable to a single fact -- a cycle, or multiple
  candidate facts without a chosen source, is rejected (a diamond is allowed and fanned out);
- an Apache Ossie -> Metric View conversion has more than 200 distinct datasets or expands to more
  than 200 join nodes, or a Metric View -> Apache Ossie conversion produces more than 200 datasets
  (the same bound applies both ways, so anything that converts one way can convert back);
- on the first semantic model, a consumed `custom_extensions` value is not a list of mappings, or
  has non-empty `DATABRICKS` `data` that is not a string
  containing one strict JSON object. Duplicate keys, trailing tokens, YAML syntax, and non-object
  JSON roots are rejected; missing, null, or empty `data` is treated as an empty object.
  Dataset-level `DATABRICKS` data has no Metric View counterpart and is not consumed;
- a join has no condition (a cross join has no Apache Ossie relationship form);
- the input YAML is malformed or contains duplicate mapping keys.

## Development

Run the test suite:

```bash
mvn test
```

JUnit 5 suites (unit, round-trip, CLI) live under `src/test/java/`, with the YAML fixtures in
`src/test/resources/`. The source layout:

```
src/main/java/org/apache/ossie/converter/databricks/
  OssieConverter.java           public facade: entry points + ConversionException/Notices/Result
  OssieConverterCommon.java     shared constants, YAML I/O, map accessors, the stash codec
  OssieToMetricView.java        export: Apache Ossie -> Metric View v1.1
  MetricViewToOssie.java        import: Metric View v1.1 -> Apache Ossie
  OssieDatabricksConverter.java command-line entry point (export / import)
```

The authoritative contract is Metric View YAML v1.1 as Databricks defines it; the checked-in
fixtures under `src/test/resources/` pin the expected output of both directions.

## Future effort

Both the Apache Ossie specification and the Databricks Unity Catalog Metric View YAML are still
evolving. As either side adds or changes fields, this converter will be updated to track them --
extending the mapping and coverage in both directions to keep the conversion current and to support
as much as each format allows over time.
