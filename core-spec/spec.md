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

# Apache Ossie - Core Metadata Specification

> **DRAFT version** — in development, schema may change before 0.2.0 is released.

**Version:** 0.2.0.dev0

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
7. [Metric Scoping](#metric-scoping)
8. [Examples](#examples)

---

## Enumerations

Standard enumeration values used throughout the specification.

### Dialects

Dialects in which metric and field expressions can be declared.

| Dialect | Description |
|---------|-------------|
| `ANSI_SQL` | Standard SQL dialect |
| `SNOWFLAKE` | Snowflake SQL |
| `MDX` | Multi-Dimensional Expressions |
| `TABLEAU` | Tableau calculations |
| `DATABRICKS` | Databricks SQL |
| `MAQL` | GoodData MAQL (Multi-Dimensional Analytical Query Language) |
| `BIGQUERY` | Google BigQuery (GoogleSQL) |
| `SIGMA` | Sigma Computing's spreadsheet-style formula language |
| `THOUGHTSPOT` | ThoughtSpot formula language |
| `DAX` | Data Analysis Expressions (Power BI / Analysis Services) |
| `HOLISTICS_AQL` | Holistics AQL (Analytical Query Language) |
| `OSSIE_SQL_2026` | Ossie's portable SQL expression language, defined in `expression_language.md` |

`OSSIE_SQL_2026` is the only dialect whose semantics this specification defines. The other values
identify vendor or third-party expression languages. Ossie treats expressions in those dialects as
opaque, and implementations MAY ignore them (see
[Dialect Extensions](expression_language.md#dialect-extensions)). Registering a dialect makes it
valid for declaring a model; it does not mean Ossie supports querying in that dialect (see
[Model Declaration vs. Querying](expression_language.md#model-declaration-vs-querying)).

### Data types

`DataType` declares the logical value type of a field or metric independently
of its role and physical representation. The shared names align with the
ontology specification's built-in value types; `Time`, `DateTimeTz`, and
`Opaque` are additional core data types.

| DataType | Description |
|----------|-------------|
| `String` | Variable-length Unicode character data; length and collation are unspecified. |
| `Integer` | Exact integral number; width and signedness are unspecified. |
| `Decimal` | Exact base-10 number; precision and scale are unspecified. |
| `Float` | Approximate floating-point number. |
| `Boolean` | Logical two-valued truth type. |
| `Date` | Calendar date with no time-of-day component. |
| `Time` | Time-of-day with no date or timezone. |
| `DateTime` | Local/civil date and time with no timezone or offset. |
| `DateTimeTz` | Date and time with sufficient offset or timezone context to identify an instant. Preservation of a named timezone identifier is not guaranteed. |
| `Opaque` | Known type outside the portable vocabulary; use `custom_extensions` for vendor-specific refinement. Omit `datatype` when the type is unknown or unspecified. |

## Semantic Model

Each JSON or YAML document represents exactly one semantic model.

A standalone document must contain `version`, `name`, and a non-empty `datasets`
array. For bulk exchange, use separate model documents. This specification does
not define a bundle format or cross-model references.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | Yes | Apache Ossie specification version (`0.2.0.dev0`) |
| `name` | string | Yes | Unique identifier for the semantic model |
| `description` | string | No | Human-readable description |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., custom instructions) |
| `datasets` | array | Yes | Collection of logical datasets (fact and dimension tables) |
| `relationships` | array | No | Defines how logical datasets are connected |
| `metrics` | array | No | Model-scoped metrics: aggregate expressions that may span multiple datasets and traverse relationships. See [Metric Scoping](#metric-scoping). |
| `custom_extensions` | array | No | Vendor-specific attributes for extensibility |

### Example

```yaml
version: 0.2.0.dev0
name: sales_analytics
description: Sales and customer analytics model
ai_context:
  instructions: "Use this model for sales analysis and customer insights"
datasets:
  - name: orders
    source: sales.public.orders
relationships: []
metrics: []
custom_extensions:
  - vendor_name: DBT
    data: '{"project_name": "tpcds_analytics", "models_path": "models/semantic"}'
```

The same document structure in JSON:

```json
{
  "version": "0.2.0.dev0",
  "name": "sales_analytics",
  "datasets": [
    {"name": "orders", "source": "sales.public.orders"}
  ]
}
```

### Migrating earlier document shapes

This is a breaking change in the unreleased `0.2.0.dev0` specification. Earlier
releases and earlier development snapshots use a `semantic_model` array. The
current schema accepts only the flat document shape; it does not accept the array
or an object-valued wrapper.

To migrate a document containing one model, move that model's properties to the
root and remove `semantic_model`. Use `version: 0.2.0.dev0` for the migrated
document. Remove any root-level `dialects` and `vendors` declarations; preserve
per-expression dialects and vendor information in `custom_extensions`. For
multiple models, create one document per model and validate each result. An empty
model array cannot produce a valid model document. Preserve model contents and
custom extensions; never silently select only the first model or overwrite a file
when splitting a document.

For [ontology maps](../ontology/ontology.md#ontology-mappings), each embedded
model must be a complete core document.

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
| `metrics` | array | No | Dataset-scoped metrics whose expressions resolve entirely within this dataset. See [Metric Scoping](#metric-scoping). |
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
| `expression` | object | Yes | Expression definition with dialect support |
| `dimension` | object | No | Dimension metadata (e.g., `is_time` flag) |
| `label` | string | No | Label for categorization |
| `description` | string | No | Human-readable description |
| `datatype` | string (enum) | No | Logical data type for this field. See [Data types](#data-types). |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Expression Object

The expression object supports multiple SQL dialects for cross-platform compatibility. Each field can define expressions in different dialects.

**Structure:**

```yaml
expression:
  dialects:
    - dialect: ANSI_SQL  # Must be one of the dialects enum values
      expression: "customer_id"  # Scalar SQL expression
```

**Key Points:**

- Use scalar SQL expressions (no aggregations)
- Can be simple column references (e.g., `customer_id`) or computed expressions (e.g., `first_name || ' ' || last_name`)
- Multiple dialect versions can be provided for the same field

### Dimension Object

| Field | Type | Description |
|-------|------|-------------|
| `is_time` | boolean | Temporal-role marker. When `true`, consumers that distinguish time dimensions (e.g. for time-series analysis or temporal filtering) should treat this field as a time dimension. This is a *role* flag, independent of the field's data type. See [DataType and `is_time`: type vs. role](#datatype-and-is_time-type-vs-role). |

### Examples

**Simple Column Reference for a Dimension:**

```yaml
- name: customer_id
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: customer_id
  description: Customer identifier
  dimension:
    is_time: false
```

**Computed Field:**

```yaml
- name: full_name
  expression:
    dialects:
      - dialect: ANSI_SQL
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
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: order_date
  datatype: Date
  dimension:
    is_time: true
  description: Date when order was placed
  ai_context:
    synonyms:
      - "purchase date"
      - "transaction date"
```

**Multi-Dialect Field:**

```yaml
- name: email_normalized
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: LOWER(email)
      - dialect: SNOWFLAKE
        expression: LOWER(email)::VARCHAR
      - dialect: BIGQUERY
        expression: SAFE_CAST(LOWER(email) AS STRING)
  description: Normalized email address
```

### DataType and `is_time`: type vs. role

`datatype` and `dimension.is_time` are independent properties that answer different questions:

- **`datatype`** describes the *data type* of the field (e.g. `Date`, `Integer`, `String`, `DateTimeTz`): what kind of values the field holds.
- **`dimension.is_time`** is a *temporal-role marker*: whether the field should be treated as a time dimension for time-series analysis or temporal filtering, regardless of its data type.

**Default for `is_time`.** When `is_time` is not set explicitly, it defaults to `true` if `datatype` is one of `Date`, `Time`, `DateTime`, `DateTimeTz`, and `false` otherwise. Explicit `is_time` always wins. Set `is_time: false` on a temporal-typed column (e.g. an audit `created_at` you don't want on the time axis) to opt out of the default.

Common combinations:

| Column example | `datatype` | `is_time` | Effective role | Why |
|---|---|---|---|---|
| `d_date` (calendar date) | `Date` | omitted | time dimension | Temporal `datatype`; `is_time` defaults to `true`. |
| `order_timestamp` | `DateTimeTz` | omitted | time dimension | Same. |
| `created_at` (audit timestamp) | `DateTime` | `false` | regular dimension | Explicit opt-out of the temporal default. |
| `d_year` (integer year grain) | `Integer` | `true` | time dimension | Non-temporal `datatype`; `is_time: true` makes the role explicit. |
| `d_quarter_name` (e.g. `"Q1"`) | `String` | `true` | time dimension | String-valued temporal grain. |
| `customer_id` | `Integer` | omitted | regular dimension | Non-temporal `datatype`; `is_time` defaults to `false`. |

> **Precedent.** This type/role separation mirrors [Snowflake Semantic Views' YAML authoring form](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec), which has a structural `time_dimensions:` collection whose entries can carry any `data_type`. The published example annotates `order_year` with `data_type: NUMBER`. LookML supports a similar split via its [`dimension_group`](https://cloud.google.com/looker/docs/reference/param-field-dimension-group), whose `datatype` enum covers `date`, `datetime`, `timestamp`, plus the integer-encoded forms `epoch` and `yyyymmdd`.

**Consumer guidance.**

- For *data-type* questions (casting, serialization, downstream type inference): prefer `datatype` when present. If only `is_time: true` is set, do not infer a specific scalar type from it.
- For *role* questions (classifying time dimensions in a query UI, generating time-series output sections, choosing time-aware aggregations): treat the field as a time dimension when `is_time` resolves to `true`, whether explicitly set or defaulted from a temporal `datatype`.

---

## Metrics

Quantitative measures defined on business data, representing key calculations like sums, averages, ratios, etc.

Metrics may be defined in two placements, using the same structure in both:

- **Model-scoped** (`semantic_model.metrics`) — may span multiple datasets and traverse relationships.
- **Dataset-scoped** (`datasets[].metrics`) — must resolve entirely within a single dataset.

See [Metric Scoping](#metric-scoping) for the rules governing each.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the metric |
| `expression` | object | Yes | Expression definition with dialect support |
| `description` | string | No | Human-readable description of what the metric measures |
| `datatype` | string (enum) | No | Logical data type for this metric. See [Data types](#data-types). |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Expression Object

The expression object supports multiple dialects

```yaml
expression:
  dialects:
  - dialect: ANSI_SQL  # Default
    expression: "SUM(order.sales) / COUNT(DISTINCT order.customer_id)"
```

### Examples

**Simple Aggregation:**

```yaml
- name: total_revenue
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
  description: Total revenue across all orders
  datatype: Decimal
  ai_context:
    synonyms:
      - "total sales"
      - "revenue"
```

**Cross-Dataset Metric:**

```yaml
- name: avg_orders
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount) / COUNT(DISTINCT customers.id)
  description: Average orders
  datatype: Decimal
  ai_context:
    synonyms:
      - "Order Average by customer"
```

### Metric Scoping

A metric may be defined at the semantic model level or on an individual dataset. Both placements use the identical metric structure; only the resolution rules differ.

| | Model-scoped (`semantic_model.metrics`) | Dataset-scoped (`datasets[].metrics`) |
|---|---|---|
| May reference fields from | Any dataset in the model | Only its own dataset |
| May traverse relationships | Yes | No |
| Name uniqueness | Unique across the semantic model | Unique within its dataset |
| Referenced as | `metric_name` | `dataset_name.metric_name` |

**Rules**

1. A dataset-scoped metric's expression MUST only reference fields of the dataset that declares it. It MUST NOT traverse relationships or reference fields belonging to another dataset. A metric that needs to span datasets MUST be model-scoped.
2. Dataset-scoped metric names MUST be unique within their dataset. Two different datasets MAY each declare a metric with the same name (e.g. `orders.item_count` and `shipments.item_count`).
3. A dataset-scoped metric name MUST NOT collide with the name of any model-scoped metric in the same semantic model. This keeps an unqualified metric reference unambiguous.
4. Dataset-scoped metrics are referenced from outside their dataset using `dataset_name.metric_name`, mirroring how a dataset's fields are already referenced in metric expressions (e.g. `SUM(orders.amount)`).

**Choosing a placement**

Prefer dataset-scoped for simple aggregations that belong conceptually to one entity — they keep the metric next to the fields it depends on and make the dataset independently interpretable. Use model-scoped for anything requiring a join.

**Example — dataset-scoped metrics**

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    primary_key: [order_id]
    fields:
      - name: amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: amount
        description: Order amount

    metrics:
      # Valid: resolves entirely within the orders dataset
      - name: total_amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount)
        description: Total order amount
        datatype: Decimal

      # Also valid: unqualified reference to a field of the declaring dataset
      - name: order_count
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(order_id)
        description: Number of orders
        datatype: Integer
```

Referenced from a consumer as `orders.total_amount` and `orders.order_count`.

**Example — invalid dataset-scoped metric**

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    metrics:
      # INVALID: references the customers dataset, so it must be model-scoped
      - name: revenue_per_customer
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount) / COUNT(DISTINCT customers.id)
```

**Prior art**

Separating an aggregation anchored to a single entity from a calculation that spans entities is established practice, though systems differ in where the single-entity metric lives, how names resolve, and how strictly scope is enforced.

| System | Single-entity metric | Cross-entity mechanism | Name uniqueness | Reference form |
|---|---|---|---|---|
| **Snowflake semantic views** | `tables[].metrics` | Top-level `metrics` (derived) | Per logical table | Qualified — `table.metric` |
| **AtScale SML** | Standalone `metric` object bound to one `dataset` + `column` | Separate `metric_calc` object type | Global across all repositories | Bare `unique_name` |
| **dbt MetricFlow** (v1.12+) | Metrics inside a semantic model | Top-level `metrics` | Global across the project | Bare name |
| **Cube** | Measures within cubes | Calculated measures referencing other measures | Per cube | Qualified — `cube.member` |
| **Databricks UC metric views** | Measures in the view (one flat scope) | Joins declared inside the view | Per metric view | `MEASURE(name)` |

Notes on each:

- **Snowflake semantic views** are the closest structural analogue. Table-level metrics are "scoped to a specific logical table, aggregating data within that table," while top-level derived metrics are "view-level metrics not tied to a specific table" that "combine metrics from multiple tables," referenced with qualified names such as `orders.total_revenue / customers.customer_count`.
- **AtScale SML** demonstrates a third pattern: a metric is a standalone, globally-named object that nonetheless declares its binding by property. Both `dataset` and `column` are required, so a plain SML metric is a single `calculation_method` over a single column of a single fact dataset. Anything combining metrics is a distinct object type (`metric_calc`) with an `expression` and no dataset binding at all.
- **dbt MetricFlow** reserves in-model metrics for those using dimensions from a single semantic model ("recommended for simple metrics") and top-level metrics for those spanning semantic models — it explicitly disallows simple metrics at the top level. Its namespace is flat, so names must be globally unique.
- **Cube** has no model-level measure concept; every measure belongs to a cube and must be unique within it.
- **Databricks UC metric views** take a different approach: one `source` plus optional `joins`, with all measures in a single flat scope. Cross-table access happens through joins declared inside the view rather than a separate cross-entity placement.

**How this proposal relates**

Four of the five systems above structurally distinguish a single-entity aggregation from a cross-entity calculation. Ossie currently provides only the cross-entity placement, which is the gap this section addresses.

On naming, Ossie follows Snowflake and Cube — scoped uniqueness with qualified `dataset.metric` references — rather than the global flat namespace used by SML and MetricFlow. This matches how Ossie already treats fields: field names are unique within a dataset, and metric expressions already reference them as `dataset.field` (e.g. `SUM(orders.amount)`).

On strictness, this proposal sits between the two extremes. SML is more restrictive: a plain metric binds to exactly one column with one aggregation method. Snowflake is more permissive: a table-level metric may traverse relationships, and `using_relationships` exists specifically to disambiguate when multiple join paths connect two logical tables. Ossie permits an arbitrary expression over the declaring dataset's fields, but no traversal.

The rationale for disallowing traversal is that a strict boundary makes the guarantee legible: a dataset-scoped metric is verifiably self-contained, so a dataset together with its metrics can be reasoned about, reused, or exchanged without resolving the surrounding join graph. Relaxing this later would be backward compatible; tightening it would not. Whether Ossie should eventually adopt a `using_relationships` equivalent is left as an open question.

**Consumer guidance: flattening to a single metric namespace**

Consumers whose native model has only model-level metrics do not need to represent the two placements separately. Because a dataset-scoped metric's expression resolves entirely within its declaring dataset, that expression is already valid as a model-scoped metric — hoisting requires no expression rewriting.

The one concern when flattening is naming. Two datasets may each declare a metric with the same local name, so a flat target namespace requires qualification; use the canonical `dataset_name.metric_name` form, or an equivalent encoding if the target namespace disallows dots.

Consumers that read only `semantic_model.metrics` remain valid, but will not observe dataset-scoped metrics. Producers requiring maximum compatibility with such consumers may continue declaring all metrics at the model level.

---

## Custom Extensions

Custom extensions allow vendors to add platform-specific metadata without breaking core compatibility. Each extension includes a vendor name and arbitrary JSON data.

### Schema

```yaml
custom_extensions:
  - vendor_name: string  # Free-form string identifying the vendor
    data: string         # JSON string containing vendor-specific data
```

### Vendor Names

The `vendor_name` field is a free-form string, allowing any vendor or organization to
define custom extensions without requiring changes to the core specification.

The following are well-known examples:

| Vendor | Description |
|--------|-------------|
| `COMMON` | Common/standard extensions |
| `SNOWFLAKE` | Snowflake-specific attributes |
| `SALESFORCE` | Salesforce/Tableau-specific attributes |
| `DBT` | dbt-specific attributes |
| `DATABRICKS` | Databricks-specific attributes |
| `GOODDATA` | GoodData-specific attributes |
| `HONEYDEW` | Honeydew-specific attributes |
| `WISDOM` | WisdomAI-specific attributes |
| `POWER_BI` | Microsoft Power BI-specific attributes |
| `SIGMA` | Sigma Computing-specific attributes |
| `THOUGHTSPOT` | ThoughtSpot-specific attributes |
| `HEX` | Hex-specific attributes |

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
version: 0.2.0.dev0
name: ecommerce_analytics
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
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_id
        description: Order identifier

      - name: customer_id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: customer_id
        description: Customer identifier

      - name: order_date
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_date
        datatype: Date
        dimension:
          is_time: true
        description: Order date

      - name: amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: amount
        description: Order amount

    # Dataset-scoped: resolves entirely within the orders dataset
    metrics:
      - name: total_amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(orders.amount)
        description: Total order amount
        datatype: Decimal

  - name: customers
    source: sales.public.customers
    primary_key: [id]
    description: Customer information
    fields:
      - name: id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: id
        description: Customer identifier

      - name: email
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: email
        description: Customer email

relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]

metrics:
  # Model-scoped: spans orders and customers via the relationship
  - name: revenue_per_customer
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.amount) / COUNT(DISTINCT customers.id)
    description: Average revenue per customer
    datatype: Decimal

  - name: customer_count
    expression:
      dialects:
        - dialect: ANSI_SQL
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

- **0.2.0.dev0** (Unreleased): In-development next minor release. Schema is mutable; do not depend on this version in production.
  - Breaking: each standalone document contains one model directly at the root; the `semantic_model` array is removed.
- **0.1.1** (2025-12-11): Initial release
  - Core semantic model structure
  - Support for datasets, relationships, fields, and metrics
  - Multi-dialect metric expressions
  - Vendor extensibility framework
  - Context for agents

---

## License

See LICENSE file for details.
