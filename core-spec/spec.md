# OSI - Core Metadata Specification

**Version:** 1.1
**Status:** DRAFT

## Goals

- **Standardization**: Establish uniform language and structure for semantic model definitions, ensuring consistency and ease of interpretation across various tools and systems.
- **Extensibility**: Support domain-specific extensions while maintaining core compatibility.
- **Interoperability**: Enable exchange and reuse across different AI and BI applications.

## Table of Contents

1. [Enumerations](#enumerations)
2. [Semantic Model](#semantic-model)
3. [Datasets](#datasets)
4. [Relationships](#relationships)
5. [Fields](#fields)
6. [Metrics](#metrics)
7. [Examples](#examples)

---

## Enumerations

Standard enumeration values used throughout the specification.

### Dialects

Supported SQL and expression language dialects for the semantic model.

| Dialect | Description                                     |
|---------|-------------------------------------------------|
| `ANSI_SQL` | Standard SQL dialect (default if not specified) |
| `SNOWFLAKE` | Snowflake SQL                                   |
| `MDX` | Multi-Dimensional Expressions                   |
| `TABLEAU` | Tableau calculations                            |
| `DATABRICKS` | Databricks SQL                                  |

### Vendors

Supported vendors for custom extensions and integrations.

| Vendor | Description |
|--------|-------------|
| `COMMON` | Common/standard extensions |
| `SNOWFLAKE` | Snowflake-specific attributes |
| `SALESFORCE` | Salesforce/Tableau-specific attributes |
| `DBT` | dbt-specific attributes |
| `DATABRICKS` | Databricks-specific attributes |

## Semantic Model

The top-level container that represents a complete semantic model, including datasets, relationships, and  metrics.

### Schema

| Field               | Type          | Required | Description                                                                                                    |
|---------------------|---------------|----------|----------------------------------------------------------------------------------------------------------------|
| `name`              | string        | Yes      | Unique identifier for the semantic model                                                                       |
| `description`       | string        | No       | Human-readable description                                                                                     |
| `dialect`           | string        | No       | Expression langauge dialect used for all expression in this document (defaults to `ANSI_SQL` if not specified) |
| `ai_context`        | string/object | No       | Additional context for AI tools (e.g., custom instructions)                                                    |
| `datasets`          | array         | Yes      | Collection of logical datasets (fact and dimension tables)                                                     |
| `relationships`     | array         | No       | Defines how logical datasets are connected                                                                     |
| `metrics`           | array         | No       | Quantifiable measures defined as aggregate expessions on fields from logical datsets                           |
| `custom_extensions` | array         | No       | Vendor-specific attributes for extensibility                                                                   |

### Example

```yaml
semantic_model:
  - name: sales_analytics
    description: Sales and customer analytics model
    dialect: ANSI_SQL
    ai_context:
      instructions: "Use this model for sales analysis and customer insights"
    datasets: []
    relationships: []
    metrics: []
    custom_extensions:
      - vendor_name: DBT
        data: '{"project_name": "tpcds_analytics", "models_path": "models/semantic"}'
```

---

## Datasets

Logical datasets represent business entities or concepts (fact and dimension tables). They contain fields and define the structure of the data.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the dataset |
| `source` | string | Yes | Reference to underlying physical table/view (e.g., `database.schema.table`) or query |
| `primary_key` | array | No | Primary key columns that uniquely identify rows (single or composite) |
| `unique_keys` | array of arrays | No | Array of unique key definitions (each can be single or composite) |
| `description` | string | No | Human-readable description |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms, common terms) |
| `fields` | array | No | Row-level attributes for grouping, filtering, and metric expressions |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Primary Key Examples

```yaml
# Simple primary key
primary_key: [customer_id]

# Composite primary key
primary_key: [order_id, line_number]
```

### Unique Keys Examples

```yaml
# Multiple unique keys (each can be simple or composite)
unique_keys:
  - [email]                    # Simple unique key
  - [first_name, last_name]    # Composite unique key
```

### Example

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    primary_key: [order_id]
    unique_keys:
      - [order_id]
      - [order_number]
    description: Order transactions
    ai_context:
      synonyms:
        - "purchases"
        - "sales"
    fields: []
    custom_extensions:
      - vendor_name: DBT
        data: '{"materialized": "table"}'
```

---

## Relationships

Relationships define how logical datasets are connected through foreign key constraints. They support both simple and composite keys.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the relationship |
| `from` | string | Yes | The logical dataset on the many side of the relationship |
| `to` | string | Yes | The logical dataset on the one side of the relationship |
| `from_columns` | array | Yes | Array of column names in the "from" dataset (foreign key columns) |
| `to_columns` | array | Yes | Array of column names in the "to" dataset (primary or unique key columns) |
| `ai_context` | string/object | No | Additional context for AI tools |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Important Notes

- The order of columns in `from_columns` must correspond to the order in `to_columns`
- Both arrays must have the same number of columns
- For simple relationships, use a single column: `[column1]`
- For composite relationships, use multiple columns: `[column1, column2]`

### Examples

**Simple Relationship:**

```yaml
- name: orders_to_customers
  from: orders
  to: customers
  from_columns: [customer_id]
  to_columns: [id]
```

**Composite Relationship:**

```yaml
# order_lines.product_id = products.id AND order_lines.variant_id = products.variant_id
- name: order_lines_to_products
  from: order_lines
  to: products
  from_columns: [product_id, variant_id]
  to_columns: [id, variant_id]
```

---

## Fields

Fields represent row-level attributes that can be used for grouping, filtering, and in metric expressions. They can be simple column references or computed expressions.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the field within the dataset |
| `dimension` | object | No | Dimension metadata (e.g., `is_time` flag) |
| `label` | string | No | Label for categorization |
| `description` | string | No | Human-readable description |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Expression String

The expression is a simple string containing the expression in the dialect specified at the semantic model level.  
Expressions are expected to be expressible as strings, in non-SQL dialects there must be a string representations of it.  
It should be human-readable.

**Key Points:**
- Use scalar SQL expressions (no aggregations)
- Can be simple column references (e.g., `customer_id`) or computed expressions (e.g., `first_name || ' ' || last_name`)
- One dialect for all the expression in a document

### Dimension Object

| Field | Type | Description |
|-------|------|-------------|
| `is_time` | boolean | Indicates if this is a time-based dimension for temporal filtering |

### Examples

**Simple Column Reference for a Dimension:**

```yaml
- name: customer_id
  expression: customer_id
  description: Customer identifier
  dimension: 
    is_time: false
```

**Computed Field:**

```yaml
- name: full_name
  expression: first_name || ' ' || last_name
  description: Customer full name
  ai_context:
    synonyms:
      - "name"
      - "customer name"
```

**Time Dimension:**

```yaml
- name: order_date
  expression: order_date
  dimension:
    is_time: true
  description: Date when order was placed
  ai_context:
    synonyms:
      - "purchase date"
      - "transaction date"
```

---

## Metrics

Quantitative measures defined on business data, representing key calculations like sums, averages, ratios, etc. Metrics are defined at the semantic model level and can  span multiple datasets.

### Schema

| Field | Type          | Required | Description |
|-------|---------------|----------|-------------|
| `name` | string        | Yes | Unique identifier for the metric |
| `expression` | string        | Yes | Expression definition with dialect support |
| `description` | string        | No | Human-readable description of what the metric measures |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `custom_extensions` | array         | No | Vendor-specific attributes |

### Expression 
The expression is a simple string containing the expression in the dialect specified at the semantic model level.  
It should have aggregation functions wrapping any row data so it would be able to perform as a rollup.

```yaml
expression: "SUM(order.sales) / COUNT(DISTINCT order.customer_id)"
```


### Examples

**Simple Aggregation:**

```yaml
- name: total_revenue
  expression: SUM(orders.amount)
  description: Total revenue across all orders
  ai_context:
    synonyms:
      - "total sales"
      - "revenue"
```

**Cross-Dataset Metric:**

```yaml
- name: avg_orders
  expression: SUM(orders.amount) / COUNT(DISTINCT customers.id)
  description: Average orders
  ai_context:
    synonyms:
      - "Order Average by customer"
```

---

## Custom Extensions

Custom extensions allow vendors to add platform-specific metadata without breaking core compatibility. Each extension includes a vendor name and arbitrary JSON data.

### Schema

```yaml
custom_extensions:
  - vendor_name: string  # Must be from vendors enum
    data: string         # JSON string containing vendor-specific data
```

### Examples

**Snowflake Extension:**

```yaml
- vendor_name: SNOWFLAKE
  data: '{
    "warehouse": "ANALYTICS_WH",
    "database": "PROD",
    "schema": "PUBLIC"
  }'
```

**Salesforce Extension:**

```yaml
- vendor_name: SALESFORCE
  data: '{
    "tableau_workbook_id": "sales_dashboard",
    "einstein_enabled": true,
    "crm_sync": {
      "enabled": true,
      "sync_frequency": "daily"
    }
  }'
```

**DBT Extension:**

```yaml
- vendor_name: DBT
  data: '{
    "project_name": "analytics",
    "materialized": "table",
    "tags": ["daily", "core"]
  }'
```

**Databricks Extension:**

```yaml
- vendor_name: Databricks
  data: '{
    "default_catalog": "finance",
    "default_schema": "gold"
  }'
```

---

## Complete Example

Here's a complete semantic model example showing all components working together:

```yaml
semantic_model:
  - name: ecommerce_analytics
    description: E-commerce sales and customer analytics
    ai_context:
      instructions: "Use this model for analyzing sales trends, customer behavior, and product performance"

    datasets:
      - name: orders
        source: sales.public.orders
        primary_key: [order_id]
        description: Customer orders
        fields:
          - name: order_id
            expression: order_id
            description: Order identifier
          
          - name: customer_id
            expression: customer_id
            description: Customer identifier
          
          - name: order_date
            expression: order_date
            dimension:
              is_time: true
            description: Order date
          
          - name: amount
            expression: amount
            description: Order amount

      - name: customers
        source: sales.public.customers
        primary_key: [id]
        description: Customer information
        fields:
          - name: id
            expression: id
            description: Customer identifier
          
          - name: email
            expression: email
            description: Customer email

    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]

    metrics:
      - name: total_revenue
        expression: SUM(orders.amount)
        description: Total revenue from all orders
        ai_context:
          synonyms:
            - "total sales"
            - "revenue"

      - name: customer_count
        expression: COUNT(DISTINCT customers.id)
        description: Total number of customers
        ai_context:
          synonyms:
            - "total customers"
            - "customer base"

    custom_extensions:
      - vendor_name: SNOWFLAKE
        data: '{"warehouse": "ANALYTICS_WH"}'
```

---

## AI Context Structure

The `ai_context` field can be either a simple string or a structured object with specific keys:

**Simple String:**

```yaml
ai_context: "orders, purchases, sales"
```

**Structured Object:**

```yaml
ai_context:
  instructions: "Use this for sales analysis"
  synonyms:
    - "orders"
    - "purchases"
    - "sales"
  examples:
    - "Show total sales last month"
    - "What's the revenue by region?"
```

### Recommended AI Context Fields

| Field | Type | Description |
|-------|------|-------------|
| `instructions` | string | Instructions for AI on how to use this entity |
| `synonyms` | array | Alternative names and terms |
| `examples` | array | Sample questions or use cases |

---

## Version History

- **1.1** (2026-01-29): Dialect simplification
  - **Breaking Change** Moved dialect from the expression level to the document level
  - Each semantic model now specifies a single dialect
  
- **1.0** (2024-12-11): Initial release
  - Core semantic model structure
  - Support for datasets, relationships, fields, and metrics
  - Multi-dialect metric expressions
  - Vendor extensibility framework
  - Context for agents

---

## License

See LICENSE file for details.

