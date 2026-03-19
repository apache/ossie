# OSI Converters

## Overview

An OSI Converter translates between the OSI semantic model format and a specific vendor's semantic implementation. This enables teams to author a semantic model once in the OSI standard and then generate the corresponding vendor-specific representation automatically.

## Hub-and-Spoke Model

OSI converters follow a **hub-and-spoke** architecture:

- **Hub**: The OSI core specification acts as the central, vendor-neutral format.
- **Spokes**: Each converter handles translation to or from a specific vendor format.

```
                  ┌─────────────┐
                  │  Snowflake  │
                  └──────┬──────┘
                         │
┌─────────────┐    ┌─────┴─────┐    ┌─────────────┐
│     dbt     ├────┤    OSI    ├────┤  Salesforce │
└─────────────┘    └─────┬─────┘    └─────────────┘
                         │
                  ┌──────┴──────┐
                  │  Databricks │
                  └─────────────┘
```

This approach avoids the need for point-to-point converters between every pair of vendors. With N vendors, a point-to-point strategy would require N*(N-1) converters. With OSI as the hub, only 2*N converters are needed (one import and one export per vendor), and interoperability with all other vendors comes for free.

## Converter Responsibilities

A converter handles two directions of translation:

### Export (OSI → Vendor)

Read an OSI semantic model and produce the equivalent vendor-specific representation. For example:

- OSI → Snowflake semantic model definition
- OSI → dbt `semantic_models` YAML
- OSI → Tableau data source / Salesforce semantic layer
- OSI → Databricks semantic layer definition

### Import (Vendor → OSI)

Read a vendor-specific semantic model and produce a valid OSI model, including mapping vendor-specific metadata into `custom_extensions`.

## Supported Vendors

The OSI specification currently defines extensions for the following vendors:

| Vendor | Description |
|--------|-------------|
| `SNOWFLAKE` | Snowflake semantic model |
| `SALESFORCE` | Salesforce / Tableau semantic layer |
| `DBT` | dbt semantic models |
| `DATABRICKS` | Databricks semantic layer |

Each vendor may define custom extensions (via the `custom_extensions` field in the OSI spec) to carry vendor-specific metadata that does not have an equivalent in the core specification.

## Mapping Core Constructs

The following table shows how each OSI construct maps conceptually to vendor equivalents. A converter must handle each of these:

### Semantic Model

The top-level container. Maps to the root object in each vendor's format.

| OSI Field | Description | Converter Consideration |
|-----------|-------------|------------------------|
| `name` | Model identifier | Map to vendor's model/project name |
| `description` | Human-readable description | Most vendors support a description field |
| `ai_context` | Instructions, synonyms for AI tools | Map if vendor supports AI/LLM annotations |
| `datasets` | Logical datasets (fact/dimension tables) | See dataset mapping below |
| `relationships` | Foreign key connections | See relationship mapping below |
| `metrics` | Aggregate measures | See metric mapping below |
| `custom_extensions` | Vendor-specific metadata | Extract extensions matching the target vendor |

### Datasets

Datasets represent logical tables (fact or dimension tables). They contain fields and define the structure of the data.

| OSI Field | Description | Converter Consideration |
|-----------|-------------|------------------------|
| `name` | Dataset identifier | Map to table/entity/model name |
| `source` | Physical table reference (e.g., `database.schema.table`) | Parse into vendor-specific catalog/schema/table components |
| `primary_key` | Single or composite primary key | Map to vendor's PK syntax; note composite keys use arrays like `[order_id, line_number]` |
| `unique_keys` | Alternative unique identifiers | Map if vendor supports unique constraints |
| `fields` | Row-level attributes (columns) | See field mapping below |
| `ai_context` | Synonyms and context for AI | Map if vendor supports semantic annotations |
| `custom_extensions` | Vendor-specific metadata | Extract extensions matching the target vendor |

### Fields

Fields represent row-level attributes. They can be simple column references or computed expressions.

| OSI Field | Description | Converter Consideration |
|-----------|-------------|------------------------|
| `name` | Field identifier | Map to column/attribute name |
| `expression.dialects` | Multi-dialect SQL expressions | Select the dialect matching the target vendor; fall back to `ANSI_SQL` |
| `dimension.is_time` | Whether the field is a time dimension | Map to vendor-specific time dimension markers |
| `label` | Categorization label | Map if vendor supports field labels/tags |
| `description` | Human-readable description | Most vendors support field descriptions |
| `ai_context` | Synonyms and business context | Map if vendor supports semantic annotations |

**Expression dialect selection**: A field may provide expressions in multiple dialects. For example:

```yaml
expression:
  dialects:
    - dialect: ANSI_SQL
      expression: LOWER(email)
    - dialect: SNOWFLAKE
      expression: LOWER(email)::VARCHAR
```

A Snowflake converter should pick the `SNOWFLAKE` dialect when available, and fall back to `ANSI_SQL` otherwise.

### Relationships

Relationships define foreign key connections between datasets. They support both simple and composite keys.

| OSI Field | Description | Converter Consideration |
|-----------|-------------|------------------------|
| `name` | Relationship identifier | Map to vendor's join/relationship name |
| `from` | Many-side dataset | Map to the referencing table |
| `to` | One-side dataset | Map to the referenced table |
| `from_columns` | Foreign key columns (array) | Positional correspondence with `to_columns`; handle composite keys |
| `to_columns` | Primary/unique key columns (array) | Must have same cardinality as `from_columns` |

**Composite key example**: A composite relationship like:

```yaml
from_columns: [product_id, variant_id]
to_columns: [id, variant_id]
```

means `from.product_id = to.id AND from.variant_id = to.variant_id`. The converter must generate the equivalent multi-column join in the target vendor format.

### Metrics

Metrics are aggregate measures defined at the semantic model level. They can span multiple datasets via relationships.

| OSI Field | Description | Converter Consideration |
|-----------|-------------|------------------------|
| `name` | Metric identifier | Map to vendor's measure/KPI name |
| `expression.dialects` | Multi-dialect aggregate expressions | Select the appropriate dialect; fall back to `ANSI_SQL` |
| `description` | What the metric measures | Most vendors support descriptions |
| `ai_context` | Synonyms and business context | Map if vendor supports semantic annotations |

**Cross-dataset metric example**:

```yaml
- name: customer_lifetime_value
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)
```

The converter must ensure that the dataset references (`store_sales`, `customer`) are resolved correctly in the target vendor's format and that any required joins are established.

### Custom Extensions

Custom extensions carry vendor-specific metadata as a JSON string. A converter should:

1. **On export (OSI → Vendor)**: Extract extensions where `vendor_name` matches the target vendor, parse the `data` JSON, and apply the vendor-specific settings.
2. **On import (Vendor → OSI)**: Capture vendor-specific settings that have no OSI core equivalent and store them as a `custom_extension` entry.

**Example**: A Snowflake export converter would extract:

```yaml
custom_extensions:
  - vendor_name: SNOWFLAKE
    data: '{"warehouse": "ANALYTICS_WH", "database": "PROD", "schema": "PUBLIC"}'
```

and apply `warehouse`, `database`, and `schema` to the appropriate places in the Snowflake model definition.

### AI Context

The `ai_context` field appears at every level (model, dataset, field, relationship, metric). It can be a simple string or a structured object:

```yaml
# Simple string
ai_context: "orders, purchases, sales"

# Structured object
ai_context:
  instructions: "Use this for sales analysis"
  synonyms:
    - "orders"
    - "purchases"
  examples:
    - "Show total sales last month"
```

A converter should map `ai_context` when the target vendor supports equivalent constructs (e.g., LLM instructions, column descriptions with business terms). When the target vendor does not support AI annotations natively, the converter may encode them in `custom_extensions` on import to avoid data loss.

## Writing a Converter

### Step-by-Step Guide

1. **Validate input**: Use the [OSI JSON Schema](../core-spec/osi-schema.json) and the [validation script](../validation/validate.py) to ensure the source OSI model is valid before conversion.

2. **Parse the OSI model**: Load the YAML file and iterate over the top-level `semantic_model` entries.

3. **Map datasets**: For each dataset, translate the `name`, `source`, `primary_key`, `unique_keys`, and `fields` to the vendor's format. Parse the `source` string (typically `database.schema.table`) into the vendor's catalog structure.

4. **Map fields with dialect selection**: For each field, select the expression dialect that matches the target vendor. Implement a fallback chain:
   - Prefer the vendor-specific dialect (e.g., `SNOWFLAKE` for a Snowflake converter)
   - Fall back to `ANSI_SQL` if the vendor dialect is not available
   - Raise a warning or error if neither is available

5. **Map relationships**: Translate relationship definitions into the vendor's join syntax. Ensure composite key column ordering is preserved.

6. **Map metrics**: Translate metric expressions using the same dialect selection logic as fields. Resolve dataset references (e.g., `store_sales.ss_ext_sales_price`) into the vendor's qualified column format.

7. **Apply custom extensions**: Extract `custom_extensions` entries matching the target `vendor_name` and apply vendor-specific settings to the output.

8. **Preserve AI context**: Map `ai_context` to vendor-equivalent annotations where supported.

9. **Validate output**: Verify the generated vendor model is valid according to the vendor's own schema or tooling.

### Handling Edge Cases

| Scenario | Recommended Approach |
|----------|---------------------|
| Missing vendor-specific dialect for a field/metric | Fall back to `ANSI_SQL`; log a warning |
| Computed fields with vendor-specific SQL syntax | Require the vendor dialect in the source OSI model; error if neither vendor nor ANSI dialect is available |
| Composite primary keys | Ensure the vendor format supports composite keys; if not, flatten or document the limitation |
| Cross-dataset metrics referencing multiple tables | Ensure all referenced datasets exist and relationships are defined; resolve qualified column names |
| Custom extensions with unknown vendor | Ignore (do not discard) — preserve for round-tripping |
| `ai_context` on a vendor that doesn't support it | Store in a `custom_extension` with `vendor_name: COMMON` to preserve for round-tripping |

### Round-Trip Fidelity

A well-implemented converter pair (import + export) should preserve as much information as possible during round-tripping:

```
Vendor A model → [Import] → OSI model → [Export] → Vendor A model
```

To achieve this:

- **Never discard information silently**. If a vendor-specific attribute has no OSI core equivalent, store it in `custom_extensions`.
- **Preserve field ordering** where possible, as some vendors are sensitive to declaration order.
- **Preserve `custom_extensions`** for all vendors, not just the target vendor. This allows a model to carry metadata for multiple vendors simultaneously (e.g., a single OSI model with both Snowflake and dbt extensions).

## Example: Conceptual Conversion Flow

Given the [TPC-DS example](../examples/tpcds_semantic_model.yaml) included in this repository, a Snowflake export converter would:

1. Read the `tpcds_retail_model` semantic model.
2. Create a Snowflake semantic model with name `tpcds_retail_model`.
3. Map each dataset (`store_sales`, `date_dim`, `customer`, `item`, `store`) to Snowflake table references, parsing `source` values like `tpcds.public.store_sales` into Snowflake's `database.schema.table` format.
4. For each field, select the `ANSI_SQL` dialect expression (since no `SNOWFLAKE` dialect is provided in this example).
5. Translate relationships into Snowflake join definitions, mapping `from_columns` and `to_columns` pairs.
6. Translate metrics like `total_sales` (`SUM(store_sales.ss_ext_sales_price)`) into Snowflake metric definitions.
7. Extract the `SALESFORCE` and `DBT` custom extensions — these would not be applied to the Snowflake output but should be preserved if the model is later imported back to OSI.

## Contributing a New Converter

To add support for a new vendor:

1. Add the vendor to the `vendors` enum in the [core specification](../core-spec/spec.md) if not already present.
2. Define the custom extension schema for the vendor (what vendor-specific metadata fields are supported in the `data` JSON).
3. Implement the export converter (OSI → Vendor).
4. Implement the import converter (Vendor → OSI).
5. Add tests using the [TPC-DS example model](../examples/tpcds_semantic_model.yaml) as a baseline.
6. Document any limitations or unsupported constructs.
