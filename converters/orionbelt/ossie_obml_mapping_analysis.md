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

# Ossie ↔ OBML Mapping Analysis

> Bidirectional conversion between [Ossie](https://ossie.apache.org/) v0.2.0.dev0 and [OrionBelt ML (OBML)](https://github.com/ralfbecher/orionbelt-semantic-layer) v1.0 semantic model formats. Ossie v0.1.x inputs are still accepted on read via a legacy normalization shim; output targets v0.2.0.dev0.

## 1. Structural Comparison

| Aspect | Ossie v0.2.0.dev0 | OBML v1.0 |
|---|---|---|
| **Top-level** | `semantic_model[]` (array of models) | Single model with `dataObjects`, `dimensions`, `measures`, `metrics` sections |
| **Tables / Entities** | `datasets[]` (flat array) | `dataObjects{}` (named dictionary) |
| **Column identifiers** | `fields[].name` (snake_case code) | `columns{}.code` (with display name as dict key) |
| **Expressions** | `expression.dialects[]` per field (multi-dialect) | Single SQL expression via `code` (single dialect) |
| **Joins / Relationships** | `relationships[]` (global, separate section) | `joins[]` (inline on each data object) |
| **Dimensions** | `field.dimension.is_time` (inline flag on fields) | `dimensions{}` (separate top-level section) |
| **Measures** | N/A (merged into metrics) | `measures{}` (explicit aggregation definitions) |
| **Metrics** | `metrics[]` with full SQL expressions | `metrics{}` with `{[Measure]}` references |
| **AI Context** | `ai_context` on every entity | `customExtensions` with `vendor: "Ossie"` |
| **Extensibility** | `custom_extensions[]` per entity | `customExtensions[]` per entity |
| **Keys** | `primary_key`, `unique_keys` on datasets | N/A |
| **Secondary joins** | N/A | `secondary: true`, `pathName` |
| **Fan-out protection** | N/A | `allowFanOut`, `reduceToRelationDimensionality` |

## 2. Key Differences

### 2.1 Naming Convention

- **Ossie** uses snake_case codes everywhere (`name: "store_sales"`)
- **OBML** supports dual naming — a display name as the dictionary key and a `code` for the physical SQL reference

During Ossie → OBML conversion, field names are used directly as both the display name and code. During OBML → Ossie conversion, the `code` value becomes the Ossie field `name`.

### 2.2 Relationship Placement

- **Ossie** defines relationships globally, referencing dataset names by string
- **OBML** defines joins inline on the "from" side data object

The converter restructures between these two representations automatically, preserving column mappings and generating descriptive relationship names.

### 2.3 Measures vs. Metrics

This is the most fundamental structural difference between the two formats.

- **Ossie** has a single "metrics" concept with full SQL expressions (e.g., `SUM(store_sales.ss_ext_sales_price)`)
- **OBML** explicitly separates:
  - **Measures**: Simple aggregations on columns (e.g., `SUM` of `ss_ext_sales_price`)
  - **Metrics**: Derived calculations referencing measures via `{[Name]}` syntax (e.g., `{[total_sales]} / {[customer_count]}`)

The converter handles this decomposition automatically:

| Ossie metric type | OBML mapping |
|---|---|
| `AGG(dataset.column)` | Direct measure |
| `AGG(DISTINCT dataset.column)` | Measure with `distinct: true` |
| `AGG(expr)` (e.g., `SUM(a.x * a.y)`) | Expression-based measure |
| Multi-aggregation expression | Auto-generated measures + metric formula |

**Example** — Ossie metric `customer_lifetime_value`:
```yaml
# Ossie
expression: SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)
```

is decomposed into OBML:
```yaml
# OBML measures (auto-generated)
measures:
  total_sales:
    columns:
      - dataObject: store_sales
        column: ss_ext_sales_price
    resultType: float
    aggregation: sum

  _customer_c_customer_sk_count_distinct:
    columns:
      - dataObject: customer
        column: c_customer_sk
    resultType: float
    aggregation: count
    distinct: true

# OBML metric (references the measures)
metrics:
  customer_lifetime_value:
    expression: "{[total_sales]} / {[_customer_c_customer_sk_count_distinct]}"
```

When a simple Ossie metric (e.g., `SUM(store_sales.ss_ext_sales_price)`) is equivalent to an existing named measure, the converter deduplicates and reuses the named measure rather than creating a redundant auto-measure.

### 2.4 AI Context Preservation

Ossie's `ai_context` (instructions, synonyms, examples) is preserved losslessly during conversion via OBML's `customExtensions` mechanism:

```yaml
# Ossie input
ai_context:
  synonyms:
    - "sales transactions"
    - "store purchases"

# OBML output (via customExtensions)
customExtensions:
  - vendor: Ossie
    data: '{"synonyms": ["sales transactions", "store purchases"]}'
```

This applies at all levels: datasets → data objects, fields → columns, and model-level `ai_context`.

During OBML → Ossie conversion, the `customExtensions` with `vendor: "Ossie"` are read back and restored as native `ai_context` on the Ossie side.

### 2.5 OBML-Specific Features (Not Representable in Ossie)

These OBML features have no direct Ossie equivalent. Where possible, metadata is preserved in Ossie `ai_context` or `custom_extensions` (with `vendor_name: "COMMON"` and `obml_`-prefixed keys) for lossless roundtrip:

- Secondary joins with `pathName` (preserved in relationship `ai_context`)
- `allowFanOut` — preserved in metric `custom_extensions` (`obml_allow_fan_out`)
- Dynamic date filters (`dynamicDate`, `dynamicDateRange`) — not yet preserved
- `timeGrain` on dimensions — preserved in field `custom_extensions` (`obml_time_grain`)
- Dimension `format` — preserved in field `custom_extensions` (`obml_dimension_format`)
- Measure filters — preserved in metric `custom_extensions` (`obml_filters`)
- Measure `total` — preserved in metric `custom_extensions` (`obml_total`)
- Measure `format` — preserved in metric `custom_extensions` (`obml_format`)
- Measure `delimiter` — preserved in metric `custom_extensions` (`obml_delimiter`)
- Measure `withinGroup` — preserved in metric `custom_extensions` (`obml_within_group`)
- Metric `format` — preserved in metric `custom_extensions` (`obml_format`)
- Locale settings — not yet preserved
- `abstractType` (OBML type system) — preserved in field `custom_extensions` (`obml_abstract_type`)

### 2.6 Ossie-Specific Features and How They Map to OBML

- **`primary_key`** — natively represented: Ossie's dataset-level `primary_key` array maps to per-column `primaryKey: true` on OBML columns (`DataObjectColumn.primaryKey`), and back to the dataset array on export.
- **`unique_keys`** — no native OBML equivalent; round-trips via an `Ossie`-vendor `customExtension` (`obml_unique_keys`).
- **Multi-dialect expressions** — on import the converter reads the first available SQL dialect in the order `ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`; non-SQL dialects (`MDX`, `TABLEAU`, `MAQL`) are not parsed. A metric with no SQL-parseable dialect, or an expression OBML cannot decompose, is preserved verbatim (`obml_unconverted_metrics`) with a `LOSSY:` warning rather than dropped. On export, OBML measures/metrics emit `ANSI_SQL`.
- **`ai_context`** — preserved losslessly via `customExtensions` (see Section 2.4).
- **`custom_extensions`** — mapped to OBML `customExtensions`.

## 3. Conversion Strategies

### 3.1 Ossie → OBML

1. Parse `source` string to extract `database`, `schema`, and `table`
2. Convert fields to columns with type inference (heuristic-based `abstractType`)
3. Restructure global relationships into inline joins on data objects
4. Decompose metric SQL expressions into OBML measures + metrics
5. Extract dimension-flagged fields into the top-level `dimensions` section (excluding FK/PK join keys)
6. Preserve `ai_context` losslessly via `customExtensions` (vendor: `"Ossie"`)

### 3.2 OBML → Ossie

1. Combine `database.schema.code` into the Ossie `source` string
2. Convert columns to fields with `ANSI_SQL` dialect expressions
3. Extract inline joins into global relationships with generated names
4. Convert measures to Ossie metrics with SQL expressions
5. Expand metric templates by substituting measure SQL into `{[Name]}` references
6. Map OBML dimension metadata into `field.dimension.is_time` flags
7. Preserve secondary join info in relationship `ai_context`
8. Store OBML-specific type info in `custom_extensions` with `vendor_name: "COMMON"`

## 4. Validation

The converter includes dual-layer validation for both formats, ensuring that converted output is structurally and semantically correct.

### 4.1 OBML Validation

1. **JSON Schema** — validates against `schema/obml-schema.json` (Draft 7)
2. **Semantic** — runs OrionBelt's `ReferenceResolver` + `SemanticValidator` (reference integrity, cycle detection, multipath detection, duplicate identifiers)

### 4.2 Ossie Validation

1. **JSON Schema** — validates against the Ossie core `ossie-schema.json` (Draft 2020-12), resolved from the repo's `core-spec/` (no vendored copy)
2. **Unique names** — checks uniqueness of dataset, field, metric, and relationship names
3. **References** — verifies that relationship `from`/`to` reference existing datasets

Validation runs automatically after each conversion. Use `--no-validate` to skip.

## 5. Converter Usage

### CLI

A single `ossie-orionbelt` command with two subcommands is installed with the package:

```bash
# Ossie → OBML
ossie-orionbelt ossie-to-obml -i tpcds_ossie.yaml -o tpcds_as_obml.yaml

# OBML → Ossie
ossie-orionbelt obml-to-ossie -i tpcds_as_obml.yaml -o tpcds_obml_as_ossie.yaml \
  --model-name tpcds_retail_model \
  --description "TPC-DS retail semantic model"

# OBML → Ossie ontology document
ossie-orionbelt obml-to-ossie --ontology -i tpcds_as_obml.yaml -o tpcds_ontology.yaml

# Skip validation
ossie-orionbelt ossie-to-obml -i input.yaml -o output.yaml --no-validate
```

### CLI Options

| Subcommand / Option | Description |
|---|---|
| `ossie-to-obml` | Convert Ossie → OBML |
| `obml-to-ossie` | Convert OBML → Ossie |
| `--ontology` | (`obml-to-ossie`) emit an Ossie ontology document instead of core-spec |
| `-i`, `--input` | Input file (required) |
| `-o`, `--output` | Output file (required) |
| `--model-name` | Model name for OBML → Ossie |
| `--description` | Model description for OBML → Ossie |
| `--ai-instructions` | AI instructions for OBML → Ossie |
| `--database` | Default database for Ossie → OBML (default: `ANALYTICS`) |
| `--schema` | Default schema for Ossie → OBML (default: `PUBLIC`) |
| `--no-validate` | Skip post-conversion validation |

### Python API

```python
from ossie_orionbelt import OssietoOBML, OBMLtoOssie, validate_obml, validate_ossie

# Ossie → OBML
converter = OssietoOBML(ossie_dict)
obml = converter.convert()
result = validate_obml(obml)
assert result.valid

# OBML → Ossie
converter = OBMLtoOssie(obml_dict, model_name="my_model")
ossie = converter.convert()
result = validate_ossie(ossie)
assert result.valid
```

## 6. Example: TPC-DS Roundtrip

The converter is validated against the official [TPC-DS example](https://github.com/apache/ossie/blob/main/examples/tpcds_semantic_model.yaml) from the Ossie repository. That file is vendored at `tests/fixtures/tpcds_semantic_model.yaml` and exercised by `tests/test_ossie_tpcds_baseline.py`, which runs the Ossie converters guide's [conceptual conversion flow](https://github.com/apache/ossie/blob/main/converters/README.md#example-conceptual-conversion-flow) end to end: Ossie to OBML to Ossie, asserting validity at each step and that the example's `SALESFORCE` and `DBT` custom extensions survive the round-trip (step 7).

### Ossie → OBML

The TPC-DS Ossie model with 5 datasets, 4 relationships, and 5 metrics converts cleanly to OBML:

- 5 data objects with inline joins
- 16 dimensions (FK/PK join keys excluded)
- 5 measures (3 direct + 2 auto-generated for metric decomposition)
- 2 metrics (composite expressions referencing measures)
- All `ai_context` synonyms preserved via `customExtensions`

### OBML → Ossie (Roundtrip)

Converting the OBML output back to Ossie produces a valid Ossie model where:

- All `ai_context` synonyms are restored from `customExtensions`
- Measures are re-expanded into SQL metric expressions
- Inline joins are extracted back into global relationships
- OBML type information is preserved in `custom_extensions`

### Files

| File | Description |
|---|---|
| `tests/fixtures/tpcds_ossie.yaml` | Official TPC-DS Ossie example (from Ossie repo) |
| `tests/fixtures/tpcds_as_obml.yaml` | Converted OBML output |
| `core-spec/ossie-schema.json` | Ossie core JSON Schema (Draft 2020-12), resolved at runtime; not vendored |
| `src/ossie_orionbelt/converter.py` | Bidirectional converter with validation |

## 7. Future Considerations

- **MCP/API integration** — Expose Ossie import/export as OrionBelt MCP tools or REST API endpoints
- **Multi-dialect support** — Preserve non-ANSI dialect expressions during roundtrip
- **Primary keys in OBML** — Add optional `primary_key` to data objects for richer metadata
- **Vendor enum** — Register `"ORIONBELT"` as an Ossie vendor for OBML-specific extensions
