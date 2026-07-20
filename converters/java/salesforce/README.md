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

A two-way converter between [Ossie semantic models](../../../core-spec/spec.md) and [Salesforce Semantic Model](https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html).

This converter supports conversion in both directions between Ossie YAML and
Salesforce Semantic Model JSON. Unmapped Salesforce properties are preserved in
`custom_extensions`; see the mapping reference for direction-specific limits.

## Requirements

- **Java 17+**
- **Maven 3.9.12+** — required to build the Java reactor

## Building

Build the executable jar from source:

```bash
cd converters/java
mvn -pl salesforce -am clean package
```

This produces a self-contained executable jar at
`salesforce/target/ossie-salesforce-converter-0.1.0-SNAPSHOT-all.jar` with all
dependencies bundled.

## Setup

The canonical Apache Ossie schema is copied automatically from `core-spec` at
build time. The Salesforce schema must be obtained separately and placed under
`salesforce/src/main/resources/schemas/` so it can be bundled into the jar.

### Salesforce Semantic Model Schema

1. Visit the [Salesforce Semantic Model Schema documentation](https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html)
2. Copy the JSON schema content from the page
3. Save it to `salesforce/src/main/resources/schemas/salesforce-semantic-model-schema.json`

## Usage

### Command Line

#### Import (Salesforce → Apache Ossie)

Convert a Salesforce Semantic Model JSON file to Ossie YAML format:

```bash
java -jar salesforce/target/ossie-salesforce-converter-0.1.0-SNAPSHOT-all.jar toOSI input.json
# Output: Customer_Orders_Model.yaml (named after model's 'name' field)
# Created in the same directory as the input file
```

Example:
```bash
java -jar salesforce/target/ossie-salesforce-converter-0.1.0-SNAPSHOT-all.jar toOSI \
  salesforce/src/test/resources/examples/salesforceToOsi.json
# Output: salesforce/src/test/resources/examples/Customer_Orders_Model.yaml
```

#### Export (Apache Ossie → Salesforce)

Convert an Ossie YAML file to Salesforce Semantic Model JSON format:

```bash
java -jar salesforce/target/ossie-salesforce-converter-0.1.0-SNAPSHOT-all.jar toSF input.yaml
# Output: Customer_Orders_Model.json (named after model's 'apiName' field)
# Created in the same directory as the input file
```

Example:
```bash
java -jar salesforce/target/ossie-salesforce-converter-0.1.0-SNAPSHOT-all.jar toSF \
  salesforce/src/test/resources/examples/osiToSalesforce.yaml
# Output: salesforce/src/test/resources/examples/Customer_Orders_Model.json
```

### Programmatic API

#### String Conversion

```java
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;

Converter sfToOsi = ConverterFactory.getConverter(ConversionDirection.SALESFORCE_TO_OSI);
List<String> osiYamlList = sfToOsi.convert(salesforceJsonString);
String osiYaml = osiYamlList.get(0);

Converter osiToSf = ConverterFactory.getConverter(ConversionDirection.OSI_TO_SALESFORCE);
List<String> salesforceJsonList = osiToSf.convert(osiYamlString);
```

#### File Conversion

```java
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;

import java.nio.file.Paths;

Converter sfToOsi = ConverterFactory.getConverter(ConversionDirection.SALESFORCE_TO_OSI);
sfToOsi.convert(Paths.get("input/model.json"), Paths.get("output/"));

Converter osiToSf = ConverterFactory.getConverter(ConversionDirection.OSI_TO_SALESFORCE);
osiToSf.convert(Paths.get("input/model.yaml"), Paths.get("output/"));
```

### Features

- **Schema-validated** - Input is validated against JSON Schema before processing
- **Lossless conversion** - Unmapped properties are preserved in `custom_extensions`
- **Bidirectional** - Supports both directions, with direction-specific limits documented below
- **Supports Ossie Specification v0.2.0.dev0**

Ossie metrics currently have no Salesforce export mapping and are omitted in
the Ossie-to-Salesforce direction.

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
| `metrics[]` | Not currently exported |
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

## Architecture

```
                    ┌───────────────────────┐
                    │ OsiSalesforceConverter│
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

**Pipeline Configuration** — Handlers and direction-specific settings defined in `osi-salesforce-converter-config.yaml`

**GenericMappingEngine** — Path-based property mapping using `mappings.yaml` configuration

**CustomExtensionHandler** — Preserves supported unmapped Salesforce properties in Ossie's `custom_extensions` for round-trip conversion

**SchemaValidator** — Validates input against JSON schemas before conversion

## Examples

See the test suite for sample models demonstrating various features:
- `salesforce/src/test/resources/examples/osiToSalesforce.yaml` - Ossie model example
- `salesforce/src/test/java/org/apache/ossie/OsiToSalesforceConverterTest.java` - Ossie to Salesforce conversion tests
- `salesforce/src/test/java/org/apache/ossie/SalesforceToOsiConverterTest.java` - Salesforce to Ossie conversion tests

## License

Apache License 2.0 — see [LICENSE](../../../LICENSE).
