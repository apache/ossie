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

# Apache Ossie Salesforce Converter

A two-way converter between [Ossie semantic models](../../core-spec/spec.md) and [Salesforce Semantic Model](https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html).

This converter supports conversion in both directions between Ossie YAML and
Salesforce Semantic Model JSON. Unmapped Salesforce properties are preserved in
`custom_extensions`; see the mapping reference for direction-specific limits.

## Requirements

- **Java 21+**
- **Maven 3.6+** — required to build the jar

## Building

Build the executable jar from source:

```bash
mvn clean package
```

This produces a self-contained executable jar at `target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar` with all dependencies bundled.

## Setup

Obtain the Salesforce schema before building so it is bundled into the jar.
Maven copies the canonical Ossie schema from `../../core-spec/ossie-schema.json`.

### Salesforce Semantic Model Schema

1. Visit the [Salesforce Semantic Model Schema documentation](https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html)
2. Copy the JSON schema content from the page
3. Save it to `src/main/resources/schemas/salesforce-semantic-model-schema.json`

Run the complete suite, including Salesforce schema checks, with:

```bash
mvn -DrequireSalesforceSchema=true clean verify
```

The property makes a missing Salesforce schema fail the test run. Without it,
schema-dependent tests retain their existing skip behavior. `verify` also checks
Apache license headers. Do not commit downloaded schemas.

## Usage

### Command Line

#### Import (Salesforce → Apache Ossie)

Convert a Salesforce Semantic Model JSON file to Ossie YAML format:

```bash
java -jar target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar toOssie input.json
# Output: Customer_Orders_Model.yaml (named after model's 'name' field)
# Created in the same directory as the input file
```

Example:
```bash
java -jar target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar toOssie \
  src/test/resources/examples/salesforceToOssie.json
# Output: src/test/resources/examples/Customer_Orders_Model.yaml
```

#### Export (Apache Ossie → Salesforce)

Convert an Ossie YAML file to Salesforce Semantic Model JSON format:

```bash
java -jar target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar toSF input.yaml
# Output: Customer_Orders_Model.json (named after model's 'apiName' field)
# Created in the same directory as the input file
```

Example:
```bash
java -jar target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar toSF \
  src/test/resources/examples/ossieToSalesforce.yaml
# Output: src/test/resources/examples/Customer_Orders_Model.json
```

### Programmatic API

#### String Conversion

```java
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;

Converter sfToOssie = ConverterFactory.getConverter(ConversionDirection.SALESFORCE_TO_OSSIE);
List<String> ossieYamlList = sfToOssie.convert(salesforceJsonString);
String ossieYaml = ossieYamlList.get(0);

Converter ossieToSf = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE);
List<String> salesforceJsonList = ossieToSf.convert(ossieYamlString);
```

#### File Conversion

```java
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;

import java.nio.file.Paths;

Converter sfToOssie = ConverterFactory.getConverter(ConversionDirection.SALESFORCE_TO_OSSIE);
sfToOssie.convert(Paths.get("input/model.json"), Paths.get("output/"));

Converter ossieToSf = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE);
ossieToSf.convert(Paths.get("input/model.yaml"), Paths.get("output/"));
```

### Features

- **Schema-validated** - Input is validated against JSON Schema before processing
- **Lossless conversion** - Unmapped properties are preserved in `custom_extensions`
- **Bidirectional** - Supports both directions, with direction-specific limits documented below
- **Supports Ossie Specification v0.2.0.dev0**

## Mapping Reference

### Import (Salesforce → Apache Ossie)

| Salesforce | Ossie |
|------------|-----|
| `apiName` | `name` |
| `semanticDataObjects[]` | `datasets[]` |
| `semanticDataObjects[].apiName` | `datasets[].name` |
| `semanticDataObjects[].dataObjectName` | `datasets[].source` |
| `semanticDimensions[]` + `semanticMeasurements[]` | `fields[]` |
| `dataObjectFieldName` | `expression.dialects[].expression` |
| Field `dataType` | Field `datatype` |
| `semanticRelationships[]` | `relationships[]` |
| `criteria[]` | `from_columns` + `to_columns` |
| `semanticCalculatedMeasurements[]` | `metrics[]` |
| `semanticCalculatedDimensions[]` | Converted to `fields[]` if single data object dependency, otherwise stored in `custom_extensions` |
| `businessPreferences` | `ai_context` |
| Unmapped properties | `custom_extensions` (vendor: `SALESFORCE`) |

### Export (Apache Ossie → Salesforce)

| Ossie | Salesforce |
|-----|------------|
| `name` | `apiName` |
| `datasets[]` | `semanticDataObjects[]` |
| `datasets[].name` | `semanticDataObjects[].apiName` |
| `datasets[].source` | `semanticDataObjects[].dataObjectName` |
| Direct `fields[]` | Split into `semanticDimensions[]` and `semanticMeasurements[]` based on `dimension` presence |
| Calculated Tableau fields | `semanticCalculatedDimensions[]` through the existing expression-analysis path |
| `expression.dialects[].expression` | `dataObjectFieldName` |
| Field `datatype` | Field `dataType` when a safe mapping exists |
| `relationships[]` | `semanticRelationships[]` |
| `from_columns` + `to_columns` | `criteria[]` |
| `metrics[]` | Validated Tua expressions in `semanticCalculatedMeasurements[]` |
| `ai_context` | `businessPreferences` |
| `custom_extensions` (vendor: `SALESFORCE`) | Restored properties |

### Data Types

Salesforce imports map field and calculated-measurement types to Ossie's portable
logical `datatype` vocabulary:

| Salesforce `dataType` | Ossie `datatype` |
|-----------------------|------------------|
| `Text`, `Email`, `PhoneNumber`, `Url` | `String` |
| `Number`, `Currency`, `Percentage` | `Decimal` |
| `Boolean` | `Boolean` |
| `Date` | `Date` |
| `DateTime` | `DateTimeTz` |
| `Geo` or another known vendor type | `Opaque` |

`Number` remains `Decimal` even when `decimalPlace` is zero because
`decimalPlace` is display metadata, not an integral-value constraint. Missing
Salesforce types remain unspecified. Exact Salesforce types are also retained in
the `SALESFORCE` custom extension so distinctions such as `Email` versus `Text`
and `Currency` versus `Number` round-trip losslessly.

Ossie field export uses these portable defaults when no exact Salesforce extension
type exists:

| Ossie `datatype` | Salesforce `dataType` |
|------------------|-----------------------|
| `String` | `Text` |
| `Integer`, `Decimal`, `Float` | `Number` |
| `Boolean` | `Boolean` |
| `Date` | `Date` |
| `DateTime`, `DateTimeTz` | `DateTime` |
| `Time`, `Opaque` | Omitted with a warning unless an exact extension type exists |

Salesforce has one `DateTime` type, so exporting timezone-free Ossie `DateTime`
loses its distinction from `DateTimeTz`; the converter logs a warning because a
subsequent Salesforce import interprets that value as `DateTimeTz`.

An exact Salesforce extension value takes precedence over the portable mapping.
If it conflicts with `datatype`, the converter preserves the exact Salesforce
value and logs a warning.

### Field Role and Time Dimensions

`datatype` does not determine whether an Ossie field is a dimension or a fact.
For direct fields, the presence of the `dimension` object determines whether the
field is exported to `semanticDimensions` or `semanticMeasurements`. A calculated
Tableau expression follows the converter's existing calculated-dimension path.

On import, Salesforce `Date` and `DateTime` dimensions set `dimension.is_time` to
`true`; other dimension types set it to `false`. On export, `dimension.is_time`
does not invent or override a scalar type. This preserves Ossie's separation of
logical data type from temporal role, including integer year and string month
dimensions.

### Relationship Handling

**Unsupported relationships** (containing Formula or SemanticField types) are stored in `custom_extensions` at the model level rather than being converted to Ossie relationships.

### Metric expressions

Metrics select `TABLEAU`, then `SNOWFLAKE`, then `ANSI_SQL`, independent of entry
order. The selected expression is parsed and validated; an invalid preferred
expression fails rather than falling back to another dialect. Duplicate selected
dialect entries are errors. Successful conversion exports every declared metric.

The target is the Salesforce/Tableau Next semantic model's
[Tua calculation language](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-functions.html).
Calculated measurements emit `syntax: Tua`, `dataType: Number`, and
`aggregationType: UserAgg`, so an already aggregated formula is not aggregated
again. See [calculated fields](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-calculated-fields.html)
and [aggregation rules](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-aggregation.html).

| SQL input | Tua output |
|-----------|------------|
| `SUM`, `AVG`, `MIN`, `MAX`, `COUNT(field)` | Same aggregate |
| `COUNT(DISTINCT field)` | `COUNTD(field)` |
| `+`, `-`, `*`, `/`, parentheses, numeric constants | Explicitly grouped arithmetic |
| Searched `CASE WHEN` | `IF … THEN … ELSEIF … ELSE … END` |
| Comparisons, `AND`, `OR`, `NOT` | Equivalent grouped operators |
| `COALESCE(a, b, …)` | Nested `IFNULL` |
| `NULLIF(a, b)` | `IF a = b THEN NULL ELSE a END` |
| `IS NULL`, `IS NOT NULL` | `ISNULL`, `NOT ISNULL` |
| `ABS`, `ROUND`, `CEIL`, `FLOOR` | `ABS`, `ROUND`, `CEILING`, `FLOOR` |

These constructs compose. For example, with declared numeric fields `profit` and
`revenue` in dataset `orders`:

```yaml
metrics:
  - name: margin
    datatype: Decimal
    expression:
      dialects:
        - dialect: SNOWFLAKE
          expression: SUM(orders.profit) / NULLIF(SUM(orders.revenue), 0)
```

The resulting expression is:

```text
(SUM([orders].[profit]) / (IF (SUM([orders].[revenue]) = 0) THEN NULL ELSE SUM([orders].[revenue]) END))
```

**Binding and types.** References resolve against declared dataset and field
names, then against fields actually emitted to Salesforce. Physical column names
and source paths are not aliases. Unqualified SQL fields must be unique. Regular
SQL names normalize to uppercase; double-quoted names match the normalized
declaration exactly, following the [expression specification](../../core-spec/expression_language.md).
Thus `"ORDERS"."AMOUNT"` matches regular declarations `orders.amount`, while
`"orders"."amount"` requires explicitly quoted lowercase declarations. Target
API names are preserved; conversion does not rename fields or discover columns.
Names containing brackets or control characters fail because their Tua escaping
is not established.

`TABLEAU` references use exact `[dataset].[field]` API names. Its supported formula
subset is the Tua equivalents above, including `IF`, `IFNULL`, `ISNULL`, `COUNTD`
and `CEILING`. Existing `ANSI_SQL` expressions using complete bracket notation
retain that spelling as a compatibility case and receive the same validation.
Bracket notation is not accepted as Snowflake SQL.

Fields need a known compatible datatype, either declared in OSI or restored from
an existing Salesforce field type. Arithmetic and `SUM`/`AVG` require numbers;
`MIN`/`MAX` also permit text and temporal values inside numeric calculations.
Comparisons and conditional/null-handling branches must have compatible types.
Metrics must return numbers; a declared `Integer` result cannot conceal a
fractional expression. Missing metric types are inferred. A formula must be
aggregated or constant: mixed row/aggregate expressions, nested aggregates, and
aggregates without a dataset field fail. A single aggregate cannot combine fields
from multiple datasets. Separate aggregates can use datasets connected through
enabled exported relationships; connectivity alone does not prove join grain.

**Limits and compatibility.** `COUNT`/`COUNTD` take a field. `ROUND` supports one
argument or a second integer-literal precision; rounding-mode overloads are not
supported. `CEIL`/`FLOOR` take one argument. Simple `CASE`, date/time and string
functions, casts, metric/calculated-field references, windows, LOD, `COUNT(*)`,
SQL comments and backslash string escapes are outside this subset. String and
Boolean literals are supported in predicates. No null-to-zero setting or implicit
cast is added. Errors name the metric and explain the rejected construct or
reference. Literal zero divisors fail; use `NULLIF` to make a zero denominator
nullable. Expressions have bounded size, nesting and generated output.

This replaces the unvalidated SQL fallback introduced in
[#402](https://github.com/apache/ossie/pull/402). Previously accepted invalid,
unsupported or untyped formulas now fail, including invalid `TABLEAU` input.
Metric/model extension preservation remains owned by #402. CLI conversion errors
are printed to stderr with exit code 3; this small change overlaps the conversion
error handling in [#286](https://github.com/apache/ossie/pull/286).

**Implementation choice.** The converter reuses its mapping pipeline, datatype
mapper and exceptions. Its bounded recursive-descent parser synthesizes a typed
Tua formula and aggregation level at each production. Add expression support in
the relevant parser/function rule with type, aggregation and composition tests.
It adds no dependencies. The open [#222](https://github.com/apache/ossie/pull/222)
implements an Ossie SQLGlot dialect in Python; it has no Tua emitter or model-bound
field/type checks. Using it here would require a Python runtime bridge and the
same target checks. The Java implementation follows the specification's naming
rules and SQL `NOT` precedence, including #222's proposed precedence correction,
without implementing a new shared expression framework.

**Validation boundary.** Unit tests cover parsing, binding and failure behavior.
Independent local Tua evaluation tests use synthetic rows with nulls, duplicates,
empty inputs and zero denominators. Those tests model the intended semantics;
they are not native Tableau Next execution. The published Salesforce **output**
schema checks structure, not formula syntax, catalog bindings or authoring API
acceptance. Before deployment, validate authoring and native queries in a test
org, including numeric precision, rounding ties, empty groups, null behavior and
unguarded dynamic division and multi-dataset grain. This converter does not provision Data 360 bindings or enrich
missing fields.

## Architecture

```
                    ┌───────────────────────┐
                    │ OssieSalesforceConverter│
                    │      (CLI App)        │
                    └───────────┬───────────┘
                            │
                    ┌───────┴────────┐
                    │ ConverterFactory│
                    └───────┬────────┘
                            │
              ┌─────────────┴─────────────┐
              │      ConverterImpl        │
              │   (Pipeline-based)        │
              │                           │
              │ • Configurable pipeline   │
              │ • Bidirectional mapping   │
              └─────────────┬─────────────┘
                            │
              ┌─────────────┴─────────────┐
              │    Pipeline Handlers      │
              ├───────────────────────────┤
              │ • DatasetMappingHandler   │
              │ • FieldMappingHandler     │
              │ • RelationshipHandler     │
              │ • MetricMappingHandler    │
              │ • SemanticModelHandler    │
              └─────────────┬─────────────┘
                            │
              ┌─────────────┴─────────────┐
              │   Support Components      │
              ├───────────────────────────┤
              │ • GenericMappingEngine    │
              │ • CustomExtensionHandler  │
              │ • SchemaValidator         │
              └───────────────────────────┘
```

**ConverterFactory** — Creates converter instances for specified direction

**Pipeline Configuration** — Handlers and direction-specific settings defined in `ossie-salesforce-converter-config.yaml`

**GenericMappingEngine** — Path-based property mapping using `mappings.yaml` configuration

**CustomExtensionHandler** — Preserves unmapped Salesforce properties in Ossie's `custom_extensions` for lossless bi-directional conversion

**SchemaValidator** — Validates input against JSON schemas before conversion

## Examples

See the test suite for sample models demonstrating various features:
- `src/test/resources/examples/ossieToSalesforce.yaml` - Ossie model example
- `src/test/java/org/apache/ossie/OssieToSalesforceConverterTest.java` - Ossie to Salesforce conversion tests
- `src/test/java/org/apache/ossie/SalesforceToOssieConverterTest.java` - Salesforce to Ossie conversion tests

## License

Apache License 2.0 — see [LICENSE](../../LICENSE).
