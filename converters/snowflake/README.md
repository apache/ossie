# Snowflake ↔ OSI Converter

Bi-directional converter for [Snowflake Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst) semantic model YAML and [OSI (Open Semantic Interchange)](https://github.com/open-semantic-interchange/OSI) YAML semantic models. Pure offline conversion — no Snowflake connection required.

This converter implements the hub-and-spoke architecture documented in [`converters/index.md`](../index.md), enabling teams to:
- **Export**: Convert OSI semantic models to Snowflake format for deployment
- **Import**: Convert existing Snowflake semantic models to OSI format for standardization and interoperability with other tools

> **Note:** These converters are under active development. They handle common cases but have not been thoroughly tested against all edge cases — use with caution in production.

## Setup

```bash
pip3 install -r requirements.txt
```

## Usage

### Export: OSI → Snowflake

Convert an OSI semantic model to Snowflake Cortex Analyst format:

```bash
python3 src/osi_to_snowflake_yaml_converter.py -i input_osi.yaml -o output_snowflake.yaml
```

**Example input (OSI format):**
```yaml
version: "0.2.0.dev0"
semantic_model:
  - name: retail_model
    datasets:
      - name: orders
        source: retail_db.public.orders
        primary_key: [order_id]
        fields:
          - name: order_date
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: order_date
            dimension:
              is_time: false
          - name: amount
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: amount
```

**Example output (Snowflake format):**
```yaml
name: retail_model
tables:
  - name: orders
    base_table:
      database: RETAIL_DB
      schema: PUBLIC
      table: ORDERS
    primary_key:
      columns: [order_id]
    dimensions:
      - name: order_date
        expr: order_date
    facts:
      - name: amount
        expr: amount
```

### Import: Snowflake → OSI

Convert a Snowflake Cortex Analyst semantic model to OSI format:

```bash
python3 src/snowflake_to_osi_yaml_converter.py -i input_snowflake.yaml -o output_osi.yaml
```

**Example input (Snowflake format):**
```yaml
name: retail_model
tables:
  - name: orders
    base_table:
      database: RETAIL_DB
      schema: PUBLIC
      table: ORDERS
    dimensions:
      - name: order_date
        expr: order_date
      - name: customer_id
        expr: customer_id
    facts:
      - name: amount
        expr: amount
relationships:
  - name: orders_to_customers
    left_table: orders
    right_table: customers
    relationship_columns:
      - left_column: customer_id
        right_column: id
```

**Example output (OSI format):**
```yaml
version: "0.2.0.dev0"
semantic_model:
  - name: retail_model
    datasets:
      - name: orders
        source: RETAIL_DB.PUBLIC.ORDERS
        fields:
          - name: order_date
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: order_date
            dimension:
              is_time: false
          - name: customer_id
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: customer_id
            dimension:
              is_time: false
          - name: amount
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: amount
      - name: customers
        source: RETAIL_DB.PUBLIC.CUSTOMERS
    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
```

## Tests

```bash
python3 -m pytest tests/
```

Include verbose output to see warnings emitted during conversion:
```bash
python3 -m pytest tests/ -v -W default
```

## Mapping Reference

### Datasets & Tables

| OSI | Snowflake |
|-----|-----------|
| `dataset.name` | `table.name` |
| `dataset.source` (e.g., `db.schema.table`) | `table.base_table` (parsed into `database`, `schema`, `table`) |
| `dataset.primary_key` (array) | `table.primary_key.columns` |
| `dataset.unique_keys` (array of arrays) | `table.unique_keys` (array of `{columns: [...]}`) |
| `dataset.description` | `table.description` |
| `dataset.ai_context.synonyms` | `table.synonyms` |

### Fields

| OSI | Snowflake |
|-----|-----------|
| `field.name` | `dimension.name`, `fact.name`, `measure.name` |
| `field.expression.dialects[].expression` (SNOWFLAKE dialect) | `dimension.expr`, `fact.expr`, `measure.expr` |
| `field.dimension.is_time = true` | `time_dimension` |
| `field.dimension.is_time = false` | `dimension` |
| No dimension or `is_time = null` | `fact` or `measure` |
| `field.description` | `dimension.description`, `fact.description`, etc. |
| `field.ai_context.synonyms` | `dimension.synonyms`, `fact.synonyms`, etc. |

### Relationships

| OSI | Snowflake |
|-----|-----------|
| `relationship.from` | `relationship.left_table` |
| `relationship.to` | `relationship.right_table` |
| `relationship.from_columns` | `relationship.relationship_columns[].left_column` |
| `relationship.to_columns` | `relationship.relationship_columns[].right_column` |

### Top-level Metrics

| OSI | Snowflake |
|-----|-----------|
| `metric.name` | `metric.name` |
| `metric.expression.dialects[].expression` | `metric.expr` |
| `metric.description` | `metric.description` |
| `metric.ai_context.synonyms` | `metric.synonyms` |

## Limitations

### OSI → Snowflake Export

- **Dropped fields**: `ai_context` (when structured as an object; string instructions are merged into description), `custom_extensions`, `label`, `version` on individual entities
- **Relationships**: `ai_context` and `custom_extensions` on relationships are not supported and are dropped with warnings
- **Expression dialects**: Only `SNOWFLAKE` or `ANSI_SQL` dialects are recognized. Other dialects (e.g., `MDX`, `TABLEAU`) are skipped with warnings.

### Snowflake → OSI Import

- **Vendor-specific Snowflake fields** (e.g., `warehouse`, `database`, `schema` at the model level, or any unknown table/field attributes) are preserved in `custom_extensions` with `vendor_name: SNOWFLAKE` for round-trip fidelity, preventing silent data loss.
- **Expressions**: All Snowflake expressions are wrapped with `dialect: SNOWFLAKE` when imported to OSI. To support cross-dialect execution, manually add `ANSI_SQL` dialect variants if needed.
- **Measures vs Facts**: Snowflake `measures` are imported as OSI fields with no `dimension` property. Users should annotate fields appropriately in the OSI model.
- **Missing expressions**: Fields with empty or missing `expr` are skipped with warnings.

## Round-Trip Behavior

Round-tripping (OSI → Snowflake → OSI or Snowflake → OSI → Snowflake) preserves the core model structure (datasets, fields, relationships, metrics) but may lose:

- **String-only `ai_context`** in relationships (dropped in Snowflake export, cannot be restored)
- **`label` fields** (dropped in export, no Snowflake equivalent)
- **Multi-dialect expressions**: Only the selected dialect is preserved; other dialects are lost

Use `custom_extensions` to preserve vendor-specific metadata that must survive round-tripping.
