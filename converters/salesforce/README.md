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
Salesforce Semantic Model JSON. Each input must contain one document; duplicate
mapping keys and trailing documents are rejected before conversion. Supported
unmapped Salesforce properties are preserved in `custom_extensions`; see the
mapping reference for direction-specific limits.

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

Both conversion directions validate input and output. Obtain the Salesforce schema
before building so it is bundled into the jar; conversion fails if it is missing.
Maven copies the canonical Ossie schema from `../../core-spec/ossie-schema.json`.

### Salesforce Semantic Model Schema

1. Visit the [Salesforce Semantic Model Schema documentation](https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html)
2. Copy the JSON schema content from the page
3. Save it to `src/main/resources/schemas/salesforce-semantic-model-schema.json`

Run the complete suite, including Salesforce schema checks, with:

```bash
mvn -DrequireSalesforceSchema=true clean verify
```

The property explicitly fails the suite when the Salesforce schema is missing,
including tests that otherwise skip for missing resources. Public API and CLI
checks require both schemas. `verify` also checks Apache license headers. Do not
commit downloaded schemas.

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

- **Schema-validated** - Input and final output are validated against JSON Schema
- **Explicit conversion boundaries** - Supported native metadata is preserved; unsupported expressions, missing references and omitted declared entities fail conversion
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
| Derived row fields | Validated Tua in `semanticCalculatedDimensions[]`, with dataset-qualified generated names |
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
| `Time`, `Opaque` | Rejected unless a compatible exact native extension type exists |

Salesforce has one `DateTime` type, so exporting timezone-free Ossie `DateTime`
loses its distinction from `DateTimeTz`; the converter logs a warning because a
subsequent Salesforce import interprets that value as `DateTimeTz`.

An exact Salesforce extension value takes precedence over the portable mapping.
If it conflicts with `datatype`, conversion fails. An absent direct-field datatype
can remain unspecified in metadata, but an expression using it needs a known,
compatible type.

### Field Role and Time Dimensions

`datatype` does not determine whether an Ossie field is a dimension or a fact.
For direct fields, the presence of the `dimension` object determines whether the
field is exported to `semanticDimensions` or `semanticMeasurements`. A derived row expression becomes a model-level calculated dimension.

On import, Salesforce `Date` and `DateTime` dimensions set `dimension.is_time` to
`true`; other dimension types set it to `false`. On export, `dimension.is_time`
does not invent or override a scalar type. This preserves Ossie's separation of
logical data type from temporal role, including integer year and string month
dimensions.

### Relationships

Every declared relationship must be exported with the same endpoint and ordered
join-key pairs. Missing fields, unequal composite-key lengths, duplicate names,
and calculated join keys fail conversion; no relationship is silently filtered.
The default cardinality is `ManyToOne`, following OSI. Valid explicit Salesforce
cardinality metadata is preserved for native round trips. Declared primary and
unique keys are checked against references and the unique side of the chosen
cardinality. This checks metadata consistency, not uniqueness in actual data.

On Salesforce import, unsupported Formula/SemanticField relationships remain in
model extensions. Export currently rejects those joins explicitly. Core
relationships and native extension arrays must not cause one another to disappear.
An extension-only relationship with omitted enablement metadata cannot establish
proven connectivity for a cross-dataset calculation.

### Field expressions and physical bindings

Field conversion parses the selected expression dialect (`TABLEAU`, then `SNOWFLAKE`, then `ANSI_SQL`) and builds one dependency plan per model. Every declared field must either produce a direct field or a supported calculated dimension. Unsupported expressions fail with the dataset and field name; they are never silently omitted.

A direct SQL identifier defines a physical column. For example, a field named `amount` with expression `revenue__c` emits `apiName: amount` and `dataObjectFieldName: revenue__c`. Double-quoted identifiers preserve their contents, including spaces, operators, periods, and escaped quotes. A verified qualification such as `warehouse.sales.orders.revenue__c` is reduced to its column name. Qualifiers must match the declared dataset name or its physical source; the converter does not guess unrelated catalog paths.

A derived SQL expression can reference declared fields or physical columns exposed by direct fields in that same dataset. For example, with direct fields `amount = revenue__c` and `cost = cost__c`, both `amount - cost` and `revenue__c - cost__c` bind to `[Orders].[amount] - [Orders].[cost]`. A name matching different physical columns or semantic fields is rejected as ambiguous. SQL unquoted identifiers use case folding; quoted identifiers preserve case. A single-identifier SQL expression always defines a physical binding, even when it matches another semantic field name. A native `TABLEAU` expression such as `[Orders].[amount]` instead denotes a semantic alias and is emitted as a calculated dimension.

Derived fields can depend on other derived fields, including declarations appearing later in the input. The plan expands those dependencies into row expressions over direct semantic fields. This lets metrics consume derived fields without relying on an undocumented global calculated-field reference syntax. Each calculated dimension uses `syntax: Tua`, a stable name based on `dataset__field`, and flattened direct-field dependencies. Name collisions, including collisions with metric names and sanitized names from other datasets, receive deterministic hash suffixes.

Row fields must stay within their dataset and cannot contain aggregations.
A constant-only row field can be emitted as a calculated dimension, but a metric
cannot reference it until a native dataset anchor is supported; otherwise
`SUM(dataset.constant_one)` would collapse to an unscoped `SUM(1)`. Put aggregate expressions in `metrics`. Referenced fields need supported, compatible datatypes; inferred result types must agree with declared types. Unknown references, dependency cycles, unsupported operations, and incompatible types stop conversion. Dependency chains are limited to 128 fields, and the shared expression emitter limits expanded formulas to 131,072 characters. These are converter resource limits, not advertised Tableau platform limits.

Environment-specific Salesforce bindings are applied after expressions have been bound in their original source scope. They can change the target data object and direct physical column names while preserving semantic API names, formulas, dependency identities, and the OSI input. Thus rebinding `amount` to `NetRevenue__c` still leaves its formulas referring to `[Orders].[amount]`.

On Salesforce import, direct `dataObjectFieldName` values are represented as quoted `ANSI_SQL` identifiers. Calculated expressions remain `TABLEAU`. This preserves punctuation and avoids mislabeling physical column names as native Tableau formulas.

These checks establish local binding and supported translation behavior. Native formula validation and result equivalence still require validation against the intended Tableau Next environment and dataset.

### Metric expressions

Fields and metrics select `TABLEAU`, then `SNOWFLAKE`, then `ANSI_SQL`, independent
of entry order. Duplicate dialect entries, an empty selected expression or an
unsupported selected expression fail without falling back to another dialect.
The source OSI model needs no new dialect entries or formula edits.

The target is the Salesforce/Tableau Next semantic model's
[Tua calculation language](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-functions.html).
Calculated measurements emit `syntax: Tua`, `aggregationType: UserAgg` and
`level: AggregateFunction`. The default numeric `dataType` is `Number`; compatible
native `Currency`/`Percentage` and display metadata such as labels and decimal
places are retained. Stale extension expressions cannot replace compiled formulas. Salesforce
extension data must be a single JSON object with unique keys; invalid metadata
fails with the owning entity name.
See [calculated fields](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-calculated-fields.html)
and [aggregation rules](https://developer.salesforce.com/docs/data/semantic-layer/guide/query-api-in-depth-aggregation.html).

| SQL input | Tua output / restriction |
|-----------|--------------------------|
| `SUM`, `AVG`, `MIN`, `MAX`, `COUNT(field)` | Same aggregate |
| `COUNT(DISTINCT field)` | `COUNTD(field)` |
| `+`, `-`, `*`, `/`, parentheses, numeric constants | Explicitly grouped arithmetic |
| Searched `CASE WHEN` | `IF … THEN … ELSEIF … ELSE … END` |
| Comparisons, `AND`, `OR`, `NOT` | Equivalent grouped operators |
| `COALESCE(a, b, …)` | Nested two-argument `IFNULL` |
| `NULLIF(a, b)` | `IF a = b THEN NULL ELSE a END` |
| `IS NULL`, `IS NOT NULL` | `ISNULL`, `NOT ISNULL` |
| `ABS`, `ROUND`, `CEIL`, `FLOOR` | `ABS`, `ROUND`, `CEILING`, `FLOOR` |
| `YEAR(date)` | `YEAR`; requires `Date` or timezone-free `DateTime` |
| `LENGTH(text)` | `LEN` |
| `POSITION(needle IN text)` | `FIND(text, needle)` |
| `SUBSTRING(text, start[, length])` | `MID`; start must be provably positive and optional length nonnegative |

These constructs compose. For example:

```yaml
metrics:
  - name: margin
    datatype: Decimal
    expression:
      dialects:
        - dialect: SNOWFLAKE
          expression: total_profit / NULLIF(total_revenue, 0)
  - name: total_profit
    datatype: Decimal
    expression:
      dialects:
        - dialect: SNOWFLAKE
          expression: SUM(orders.profit)
  - name: total_revenue
    datatype: Decimal
    expression:
      dialects:
        - dialect: SNOWFLAKE
          expression: SUM(orders.revenue)
```

Named metric references resolve independently of declaration order, are checked
for cycles and are inlined as aggregate expressions. Compiled dependencies are
cached per model. `[metric]` is the corresponding native `TABLEAU` reference.
A field and metric sharing an unqualified name are ambiguous; qualify the field
or use a unique metric name. Aggregating an already aggregated metric fails.

**Binding and types.** Metric references use declared logical field names,
including supported derived fields. Physical column names and source paths are
not metric aliases. Unqualified SQL fields must be unique across datasets.
Regular SQL names normalize to uppercase; double-quoted names match the normalized
declaration exactly, following the [expression specification](../../core-spec/expression_language.md).
Thus `"ORDERS"."AMOUNT"` matches regular declarations `orders.amount`, while
`"orders"."amount"` requires explicitly quoted lowercase declarations. Native
`TABLEAU` uses exact `[dataset].[field]` names. Legacy complete bracket references
in `ANSI_SQL` are retained as a compatibility case; Snowflake requires SQL quotes.
Names containing brackets or control characters fail because their Tua escaping
is not established.

Referenced fields need known compatible datatypes. Arithmetic and `SUM`/`AVG`
require numbers; branches and comparisons require compatible operand types.
Metrics must return numbers and be aggregated or constant. Declared `Integer`
results cannot conceal fractional expressions. `CEIL`, `FLOOR` and `ROUND` at
nonpositive precision infer integral values. All-null metrics need an explicit
numeric datatype. Mixed row/aggregate expressions and nested aggregates fail.

One aggregate cannot combine fields from different datasets. Separate aggregates
may reference datasets connected through enabled exported relationships, including
references introduced by metric dependencies. Connectivity checks do not prove
that the target query planner preserves the intended grain.

**Boundaries.** Parser acceptance never implies target support. Windows, LOD,
subqueries, SQL casts, simple `CASE`, UDFs, `COUNT(*)`, comments, backslash string
escapes, implicit type coercions and unlisted functions are unsupported. No raw
SQL fallback is emitted. `COUNT`/`COUNTD` accept declared fields. `ROUND` accepts
one argument or an integer-literal precision; rounding-mode overloads are rejected.
Literal zero divisors fail; `NULLIF` can guard a denominator. Each expression is
bounded to 32,768 input characters, 8,192 tokens, 128 syntax/dependency levels and
131,072 generated characters. These are converter resource limits, not platform
limits. Errors identify the field/metric and rejected construct or reference.

### Environment bindings

Use an optional JSON or YAML manifest to bind the same OSI model to a Salesforce
environment. This file contains deployment names, never business formulas:

```yaml
models:
  sales:
    dataspace: default
    datasets:
      orders:
        dataObjectName: Orders__dll
        dataObjectType: Dlo
        fields:
          profit: NetProfit__c
          revenue: Revenue__c
```

Only listed values are overridden. Model, dataset and field keys are exact OSI
names. Fields must be direct physical bindings. Unknown names/properties,
duplicate keys, multiple YAML documents and bindings for calculated fields fail.
Native object types must match the bundled Salesforce schema; this is not catalog
discovery or DLO/DMO provisioning. Semantic names, formulas and the OSI file remain
unchanged. Without a manifest, existing source and extension mappings apply.

```bash
java -jar target/ossie-salesforce-converter-0.1.0-SNAPSHOT.jar \
  toSF input.yaml --bindings bindings.yaml
```

```java
SalesforceBindings bindings = SalesforceBindings.fromPath(Path.of("bindings.yaml"));
Converter converter = ConverterFactory.getConverter(
    ConversionDirection.OSSIE_TO_SALESFORCE, bindings);
List<String> output = converter.convert(osiYaml);
```

Bindings are export-only. CLI usage/input errors exit with code 1/2; conversion
and schema errors are printed to stderr with exit code 3. All models are converted
and validated before the file API begins writing any output, so conversion failure
in a later model does not leave earlier model files behind.

## Architecture

```text
OSI input -> source schema validation -> ConversionContext (one per model)
  -> dataset mapping
  -> FieldExpressionPlan: classify physical fields, bind and compile derived fields
  -> validated relationships
  -> MetricCompilationPlan: resolve fields/metrics, detect cycles, compile dependencies
  -> native metadata restoration
  -> physical environment bindings
  -> final identity/reference/coverage checks -> target schema validation -> output

ExpressionCompiler:
  SQL -> JSqlParser frontend --+
                              +-> immutable AST -> typed/aggregation analysis -> Tua emitter
  TABLEAU -> bounded frontend -+
```

JSqlParser 5.3 is used under its Apache-2.0 option for SQL syntax parsing. It does
not provide Tua semantics. `ExpressionFunctionRegistry` defines supported
dialect/function/arity combinations; `ExpressionAnalyzer` checks types and
aggregation; `TuaExpressionEmitter` writes only validated nodes. Supporting a new
function requires an explicit registry entry, semantic checks, lowering rule and
tests. SQLGlot is not required at runtime, and no Python bridge is introduced.

`MetricFieldResolver` builds model-scoped identity and relationship indexes once.
Field and metric dependency plans cache compilation while enforcing depth and
output limits. No model state is stored in reusable handlers. The existing generic
mapping pipeline, schema resources, datatype mapper and public converter APIs remain
in use. `PipelineStep` retains the original map-based method for custom handlers.

Final validation detects dangling references, duplicate identities, omitted core
or native-extension entities, disconnected calculations and inconsistent join keys.
Extension-only calculations also pass the shared expression compiler. Schema
validation runs after all extensions and bindings; neither can bypass the final
checks. A native extension array that conflicts with an emitted core array fails
instead of silently losing distinct entities.

## Validation

```bash
mvn -DrequireSalesforceSchema=true clean verify
```

The suite covers parser/AST rejection, quoted names, types, aggregation, field and
metric dependency graphs, binding overlays, relationship preservation, native
metadata, CLI failures and both conversion directions. An independent local Tua
evaluator checks results over synthetic rows including nulls, duplicates, empty
inputs, decimals, dates and string fields. Stress cases exercise dependency depth,
expansion bounds and many metrics sharing one dependency. `verify` also runs Apache
RAT license-header checks.

These are local checks, not native Tableau Next execution. The Salesforce output
schema validates structure, not tenant catalog existence or backend formula
semantics. Deployment still needs native formula/authoring validation and result
comparison in the intended org, especially numeric precision, rounding ties, nulls,
empty groups, timezones and multi-dataset grain. General Snowflake/ANSI SQL cannot
be promised equivalent where Tua has no supported construct. Unsupported cases
must use an explicit new lowering or a separately designed native execution route.

## Examples

- `src/test/resources/examples/ossieToSalesforce.yaml`: valid OSI export fixture
- `src/test/java/org/apache/ossie/MetricExportIntegrationTest.java`: public API, dependencies and bindings
- `src/test/java/org/apache/ossie/converter/ExpressionCompilerTest.java`: supported/rejected expressions
- `src/test/java/org/apache/ossie/converter/FieldExpressionPlanTest.java`: derived fields and scope
- `src/test/java/org/apache/ossie/SalesforceToOssieConverterTest.java`: reverse conversion

## License

Apache License 2.0 — see [LICENSE](../../LICENSE).
