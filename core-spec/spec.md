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
7. [Name Resolution](#name-resolution)
8. [Examples](#examples)

---

## Enumerations

Standard enumeration values used throughout the specification.

### Dialects

The `dialect` field is a free-form string, allowing any expression language to be used
without requiring changes to the core specification.

`OSSIE_SQL_2026` is the only dialect whose semantics this specification defines (Ossie's
portable SQL expression language, see `expression_language.md`). All other values identify
vendor or third-party expression languages, which Ossie treats as opaque and implementations
MAY ignore. Registering a dialect does not mean Ossie supports querying in it (see
[Model Declaration vs. Querying](expression_language.md#model-declaration-vs-querying)).

For a non-normative catalog of dialects well-known tools use, see
[extensions/dialects.md](../extensions/dialects.md).

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
| `metrics` | array | No | Model-scoped metrics: for aggregations that span datasets or need to be aware of cross-dataset concepts. See [Metric Scoping](#metric-scoping). |
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
| `primary_key` | array | No | Dataset fields that uniquely identify rows |
| `unique_keys` | array of arrays | No | Array of unique key definitions, each a list of dataset fields |
| `description` | string | No | Human-readable description |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms, common terms) |
| `fields` | array | No | Row-level attributes for grouping, filtering, and metric expressions |
| `metrics` | array | No | Dataset-scoped metrics that semantically belong to this dataset; expressions reference its declared fields by single-part name and resolve entirely within it. See [Metric Scoping](#metric-scoping). |
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
    fields:
      - name: order_id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_id
      - name: order_number
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_number
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
| `from_columns` | array | Yes | Array of field names in the "from" dataset (foreign key fields) |
| `to_columns` | array | Yes | Array of field names in the "to" dataset (primary or unique key fields) |
| `ai_context` | string/object | No | Additional context for AI tools |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Important Notes

- Despite their names, `from_columns` and `to_columns` contain names of fields declared in the `from` and `to` datasets, not columns of those datasets' sources. See [Name Resolution](#name-resolution).
- The order of fields in `from_columns` must correspond to the order in `to_columns`
- Both arrays must have the same number of fields
- For simple relationships, use a single field: `[field1]`
- For composite relationships, use multiple fields: `[field1, field2]`

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

Within a dataset, field names and dataset-scoped metric names share one namespace, so a field name MUST NOT collide with a dataset-scoped metric name of the same dataset (see [Metric Scoping](#metric-scoping)).

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the field within the dataset |
| `expression` | object | Yes | Expression definition with dialect support |
| `dimension` | object | No | Dimension metadata (e.g., `is_time` flag) |
| `label` | string | No | Label for categorization |
| `display_label` | string | No | Human-readable display name for UI and AI interaction |
| `description` | string | No | Human-readable description |
| `datatype` | string (enum) | No | Logical data type for this field. See [Data types](#data-types). |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `semantic_type` | string | No | High-level semantic classification (see [Semantic Type](#semantic-type)) |
| `measurement` | object | No | Unit and quantity metadata (see [Measurement](#measurement)) |
| `display_format` | string | No | Excel-compatible, locale-invariant format pattern (e.g., `#,##0.00`, `0.0%`). See [Display Format](#display-format). |
| `default_aggregation` | string | No | Default aggregation when used as a measure: `sum`, `avg`, `min`, `max`, `count`, `count_distinct` |
| `default_sort` | object | No | Default sorting behavior (see [Default Sort](#default-sort)) |
| `default_time_granularity` | string | No | Default time bucket for temporal fields: `day`, `week`, `month`, `quarter`, `year` |
| `glossary_references` | array | No | References to concepts in external glossaries or ontologies (see [Glossary References](#glossary-references)) |
| `hidden` | boolean | No | Whether this field should be hidden from consumer UIs |
| `group_labels` | array of strings | No | Organizational grouping labels for UI presentation. A field may belong to more than one group. |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Expression Object

The expression object supports multiple SQL dialects for cross-platform compatibility. Each field can define expressions in different dialects.

**Structure:**

```yaml
expression:
  sql: "customer_id"  # Primary, portable OSSIE_SQL_2026 form
  dialects:
    - dialect: ANSI_SQL  # Free-form string; OSSIE_SQL_2026 is the only dialect the spec defines
      expression: "customer_id"  # Scalar SQL expression
```

**Key Points:**

- Use scalar SQL expressions (no aggregations)
- Can be simple column references (e.g., `customer_id`) or computed expressions (e.g., `first_name || ' ' || last_name`)
- The optional top-level `sql` is the primary, portable form, written in OSSIE_SQL_2026
- At least one of the top-level `sql` or `dialects` MUST be present; either MAY stand alone
- If both the top-level `sql` and an `OSSIE_SQL_2026` entry in `dialects` are present, the top-level `sql` is authoritative and consumers MUST use it as the OSSIE_SQL_2026 form
- Identifiers refer to columns of the dataset's `source`, not to other fields. See [Name Resolution](#name-resolution).
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

There are two types of metrics:

- **Dataset-scoped** (`datasets[].metrics`): aggregations that belong to a single dataset. The expression references that dataset's declared fields by single-part name and resolves entirely within it.
- **Model-scoped** (the model-root `metrics`): aggregations that span datasets across the model. The expression references fields by multi-part `dataset.field` name and may combine fields across datasets.

Metric expressions refer to dataset fields. They cannot refer to columns of a dataset's `source` or to other metrics. See [Metric Scoping](#metric-scoping) for the rules governing each and [Name Resolution](#name-resolution) for how names resolve.

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for the metric |
| `expression` | object | Yes | Expression definition with dialect support |
| `description` | string | No | Human-readable description of what the metric measures |
| `datatype` | string (enum) | No | Logical data type for this metric. See [Data types](#data-types). |
| `ai_context` | string/object | No | Additional context for AI tools (e.g., synonyms) |
| `display_label` | string | No | Human-readable display name for UI and AI interaction |
| `semantic_type` | string | No | High-level semantic classification (see [Semantic Type](#semantic-type)) |
| `measurement` | object | No | Unit and quantity metadata (see [Measurement](#measurement)) |
| `display_format` | string | No | Excel-compatible, locale-invariant format pattern (e.g., `#,##0.00`, `0.0%`). See [Display Format](#display-format). |
| `desired_direction` | string | No | KPI polarity: `higher_is_better`, `lower_is_better`, `neutral` |
| `default_sort` | object | No | Default sorting behavior (see [Default Sort](#default-sort)) |
| `glossary_references` | array | No | References to concepts in external glossaries or ontologies (see [Glossary References](#glossary-references)) |
| `hidden` | boolean | No | Whether this metric should be hidden from consumer UIs |
| `group_labels` | array of strings | No | Organizational grouping labels for UI presentation. A field may belong to more than one group. |
| `custom_extensions` | array | No | Vendor-specific attributes |

### Expression Object

The expression object supports multiple dialects

```yaml
expression:
  sql: "SUM(order.sales) / COUNT(DISTINCT order.customer_id)"  # Primary, portable OSSIE_SQL_2026 form
  dialects:
  - dialect: ANSI_SQL  # Default
    expression: "SUM(order.sales) / COUNT(DISTINCT order.customer_id)"
```

At least one of the top-level `sql` or `dialects` MUST be present; either MAY stand alone. When both a top-level `sql` and an `OSSIE_SQL_2026` entry in `dialects` are present, the top-level `sql` is authoritative.

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

A metric may be defined on an individual dataset or at the model level. Both placements use the identical metric structure; they differ in what the expression may reference and in what the metric is for:

- A **dataset-scoped** metric (`datasets[].metrics`) belongs to a single dataset: it aggregates that dataset's own fields, at the dataset's row level, so the dataset carries its own measures and stays queryable on its own.
- A **model-scoped** metric (the model-root `metrics`) spans datasets across the model: it combines fields from more than one dataset.

| | Dataset-scoped (`datasets[].metrics`) | Model-scoped (model-root `metrics`) |
|---|---|---|
| Expression references | This dataset's declared fields, single-part: `field` | Fields of any dataset, multi-part: `dataset.field` |
| Spans datasets | No | Yes, via relationships |
| Referenced as | `dataset_name.metric_name` | `metric_name` |

**Rules**

1. A dataset-scoped metric's expression MUST reference only the declared fields of the dataset that declares it, written by single-part (unqualified) name: `SUM(net_amount)`. It MUST NOT reference a field of another dataset; a metric that must do so is model-scoped and MUST be declared in the model-root `metrics`.
2. Within a dataset, declared field names and dataset-scoped metric names share one namespace and MUST all be unique across the two.
3. A model-scoped metric's expression references fields by multi-part name, `dataset.field`, and MAY combine fields from more than one dataset.

> Referencing a column of a dataset's `source` that is not a declared field is a future extension. Today a dataset-scoped expression reaches only the dataset's declared fields.

**Example: dataset-scoped metrics**

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    primary_key: [order_id]
    fields:
      - name: order_id
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: order_id
        description: Order identifier
      - name: net_amount
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: amount - discount
        description: Order amount after discount

    metrics:
      - name: total_net_amount
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: SUM(net_amount)
        description: Total order amount after discount
        datatype: Decimal

      - name: order_count
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: COUNT(order_id)
        description: Number of orders
        datatype: Integer
```

Referenced from a consumer as `orders.total_net_amount` and `orders.order_count`.

**Example: invalid dataset-scoped metric**

```yaml
datasets:
  - name: orders
    source: sales.public.orders
    fields:
      - name: net_amount
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: amount - discount
    metrics:
      # INVALID (rule 1): references the customers dataset, so this metric is
      # model-scoped and belongs in the model-root metrics
      - name: revenue_per_customer
        expression:
          dialects:
            - dialect: OSSIE_SQL_2026
              expression: SUM(net_amount) / COUNT(DISTINCT customers.id)
```

---

## Extended Metadata Types

The following types are used by the extended metadata fields on both fields and metrics. All extended metadata is **optional** and **non-executional** — it does not affect query execution but enables consumers (BI tools, AI agents, developers) to correctly interpret, render, and present data.

`default_aggregation`, `default_sort`, and `default_time_granularity` are hints for tools that build queries. An implementation MUST NOT change query results because they are set: it does not supply an aggregate, add an ordering, or bucket a time field on the author's behalf. Likewise, `display_label` and `group_labels` are presentation strings, not names; expressions and queries reference fields and metrics by `name` only.

### Semantic Type

High-level classification of a field or metric value. Accepts either a **well-known token** or an **absolute URI** pointing to an external type system. Using URIs keeps the field registry-agnostic and allows governed external type systems to carry the long tail of domain-specific types.

**Well-known tokens:**

| Value | Description |
|-------|-------------|
| `categorical` | Unordered categorical values (e.g., status, color) |
| `quantitative` | Numeric values representing quantities |
| `monetary` | Currency/financial values |
| `temporal` | Time or date values |
| `geographic` | Location-related values (country, lat/lng, region) |
| `ordinal` | Ordered categorical values (e.g., Low/Medium/High, ratings) |
| `identifier` | Unique identifiers (e.g., IDs, codes) |

**URI examples** (for regulated or domain-specific types):

```yaml
semantic_type: https://www.iso20022.org/glossary/LEI
semantic_type: https://fpml.org/types/ISIN
semantic_type: https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/UPI
```

A URI value MUST include a scheme (for example `https:` or `urn:`). This keeps the URI form distinguishable from the token form, so a value that is neither a well-known token nor a scheme-qualified URI — such as a misspelled `monetory` — is rejected rather than silently accepted as an opaque type reference.

### Measurement

Describes what a numeric value represents. Enables unit-aware reasoning and formatting.

| Field | Type | Description |
|-------|------|-------------|
| `quantity_kind` | string | The kind of quantity (e.g., `currency`, `length`, `weight`, `temperature`, `percentage`, `duration`) |
| `unit` | string | Specific unit. For currency, use ISO 4217 codes (e.g., `usd`, `eur`, `gbp`). For others, use UCUM or descriptive strings (e.g., `meters`, `kg`, `celsius`) |
| `unit_system` | string | Unit system: `si`, `imperial`, `custom` |

All three properties are optional, but a `measurement` object MUST contain at least one of them.

When `quantity_kind` is `currency`, `unit` is the authoritative currency of the value. Consumers SHOULD derive the currency symbol and its placement from `unit` and the viewer's locale rather than from a symbol written into `display_format` (see [Display Format](#display-format)).

**Example:**

```yaml
measurement:
  quantity_kind: currency
  unit: usd
  unit_system: custom
```

### Default Sort

Defines default sorting behavior for a field or metric.

| Field | Type | Description |
|-------|------|-------------|
| `direction` | string | Sort direction: `asc`, `desc` |
| `nulls` | string | Null positioning: `first`, `last` |
| `by_field` | string | Name of a different field or metric to sort by (e.g., sort month names by month number). A name, not an expression. When omitted, the sort is on the owning field or metric. |

**Example:**

```yaml
default_sort:
  direction: desc
  nulls: last
```

### Glossary References

References a field or metric to concepts in an external glossary, ontology, or standard using a SKOS-based predicate. The predicate vocabulary is **intentionally open** — the SKOS predicates provide a well-understood baseline, but non-standard predicates (e.g., `DISTINCT_FROM` for regulatory disambiguation) are permitted and can be expressed via extensions without being schema-invalid.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | string (URI) | Yes | URI of the external concept |
| `predicate` | string | No | Relationship type. Defaults to `exactMatch`. SKOS baseline predicates listed below. Vocabulary is open. |
| `provenance` | string | No | Origin of this reference (e.g., `"manual"`, `"FIBO 4.1"`, a tool name) |

**SKOS baseline predicates:**

| Predicate | Meaning |
|-----------|--------|
| `exactMatch` | Concepts are sufficiently similar to be used interchangeably (default) |
| `closeMatch` | Concepts are similar enough to be useful in some contexts |
| `broadMatch` | Target concept is broader (more general) |
| `narrowMatch` | Target concept is narrower (more specific) |
| `relatedMatch` | Concepts are associatively related |

**Example — standard alignment:**

```yaml
glossary_references:
  - target: https://schema.org/MonetaryAmount
    predicate: exactMatch
    provenance: manual
```

**Example — regulatory disambiguation (open predicate):**

```yaml
glossary_references:
  - target: https://spec.edmcouncil.org/fibo/ontology/DER/RateDerivatives/IRSwaps/Counterparty
    predicate: exactMatch
    provenance: FIBO 4.1
  - target: https://www.esma.europa.eu/emir/Counterparty
    predicate: DISTINCT_FROM
    provenance: EMIR-vs-MiFIR review 2024
```

### Display Format

The `display_format` string follows Excel-compatible custom number format conventions. Consumers MAY support a subset, but interoperability is improved when adhering to common patterns.

The value is a format pattern. It is not an expression, and named presets such as `currency` or `percent` are not part of the vocabulary.

**The pattern is locale-invariant.** In a pattern, `.` denotes the decimal separator and `,` the grouping separator; they are placeholders, not literal characters. Consumers render them with the viewer's locale, so one pattern serves every locale: `#,##0.00` displays as `1,234.56` for `en-AU` and `1 234,56` for `sv-SE`. This is the convention of LDML/ICU number patterns, and how Excel number formats behave across regional settings. A separator meant literally is quoted or escaped, as in Excel.

**Currency symbols.** A currency symbol written into the pattern, such as the `$` in `$#,##0.00`, is a literal and renders the same in every locale. For currency values, prefer recording the currency in `measurement.unit` and omitting the symbol from the pattern, so that consumers can write the symbol in the viewer's locale.

**Common Patterns:**

| Pattern | Description | Example Output |
|---------|-------------|----------------|
| `$#,##0.00` | Literal `$`, 2 decimals (renders `$` in every locale) | $1,234.56 |
| `#,##0.00` | 2 decimals; pair with `measurement.unit` for currency | 1,234.56 |
| `#,##0` | Integer with grouping | 12,345 |
| `0.0%` | Percentage with 1 decimal | 12.3% |
| `#,##0.00;(#,##0.00)` | Positive/negative | 1,234.56 or (1,234.56) |
| `0.00E+00` | Scientific notation | 1.23E+04 |
| `yyyy-mm-dd` | Date format | 2024-01-15 |

Example outputs are shown for `en-US`.

### Localization

The string properties of a field or metric (`display_label`, `description`, `ai_context`, `group_labels`) are written in a single language, the language the model is authored in. This version of the specification does not define localized or audience-specific variants of them. That is proposed separately as presentation overlays.

Rendering a value for a viewer's locale changes how it is written, not what it is. A consumer MUST NOT present a value in a currency other than its `measurement.unit`, for example by showing `usd` amounts with a `€` symbol. An amount in another currency is a different value and is defined as a separate metric.

---

## Extended Metadata Examples

**Field with full extended metadata:**

```yaml
- name: sales_amount
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: sales_amount
  display_label: "Sales Amount"
  semantic_type: monetary
  measurement:
    quantity_kind: currency
    unit: usd
  display_format: "#,##0.00"
  default_aggregation: sum
  default_sort:
    direction: desc
    nulls: last
  glossary_references:
    - target: https://schema.org/MonetaryAmount
      predicate: exactMatch
  group_labels:
    - "Revenue"
    - "Financial Metrics"
```

**Metric with extended metadata:**

```yaml
- name: total_sales
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.sales_amount)
  display_label: "Total Sales"
  description: Total revenue from all completed orders
  semantic_type: monetary
  measurement:
    quantity_kind: currency
    unit: usd
  display_format: "#,##0.00"
  desired_direction: higher_is_better
  default_sort:
    direction: desc
  group_labels:
    - "Revenue"
    - "Financial Metrics"
  glossary_references:
    - target: https://schema.org/MonetaryAmount
      predicate: exactMatch
```

**Temporal field with time granularity:**

```yaml
- name: order_date
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: order_date
  dimension:
    is_time: true
  display_label: "Order Date"
  semantic_type: temporal
  default_time_granularity: month
  default_sort:
    direction: desc
```

**Hidden field used only in expressions:**

```yaml
- name: internal_cost_basis
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: raw_cost * adjustment_factor
  hidden: true
  description: Internal cost calculation used by margin metrics
```

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

For a non-normative catalog of `vendor_name` values well-known tools use, see
[extensions/vendors.md](../extensions/vendors.md).

### Example

```yaml
- vendor_name: DB_VENDOR
  data: '{"some_setting": "value", "nested": {"option": true}}'
```

The `data` payload is an opaque string that Ossie carries but does not
interpret. For vendor-specific extension examples and the known-vendor catalog,
see [extensions/vendors.md](../extensions/vendors.md).

---

## Name Resolution

An Ossie model has three levels of names:

1. **Source columns.** The columns of the table, view, or query named by a
   dataset's `source`. The Ossie document does not declare them.
2. **Dataset fields.** The fields declared in a dataset's `fields`. Each field
   is defined in terms of that dataset's source columns, just as a view's
   `SELECT` list can only reference columns from its `FROM` clause. A dataset's
   fields are its public interface.
3. **Metrics.** Dataset-scoped metrics (`datasets[].metrics`) and model-scoped
   metrics (the model-root `metrics`), defined in terms of dataset fields.

Metrics and relationships do not reach through a dataset to its source
columns, so a dataset that declares no fields exposes nothing to them.

| Where the name appears | What it refers to |
|------------------------|-------------------|
| Field `expression` | A column of the same dataset's `source` |
| `datasets[].primary_key`, `datasets[].unique_keys` | A field of the same dataset |
| `relationships[].from_columns` | A field of the `from` dataset |
| `relationships[].to_columns` | A field of the `to` dataset |
| Dataset-scoped metric `expression` (`datasets[].metrics`) | A field of the same dataset, unqualified |
| Model-scoped metric `expression` (model-root `metrics`) | A field of the named dataset, qualified as `dataset.field` |

For example, a dataset can expose the source column `o_totalprice` as the
field `total_amount`, and derive `total_usd` from `total_cents`:

```yaml
datasets:
  - name: orders
    source: sales.public.orders   # columns: o_orderkey, o_custkey, o_totalprice, total_cents
    primary_key: [order_id]       # field name, not o_orderkey
    fields:
      - name: order_id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: o_orderkey      # source column
      - name: customer_id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: o_custkey
      - name: total_amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: o_totalprice
      - name: total_usd
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: total_cents / 100.0

relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]   # field of orders, not o_custkey
    to_columns: [id]              # field of customers (dataset not shown)

metrics:
  - name: total_revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.total_usd)   # field, not a source column
```

In this model, these references are invalid:

- `SUM(orders.o_totalprice)` and `from_columns: [o_custkey]`, because
  `o_totalprice` and `o_custkey` are source columns, not fields of `orders`.
- `SUM(total_usd)` in a model-scoped metric, because model-scoped metric
  expressions must qualify field names with the dataset name, even when only
  one dataset has a field with that name.
- `SUM(o_totalprice)` in a metric declared on `orders`, because dataset-scoped
  metrics reach only the dataset's declared fields, not its source columns.

If a field and a source column share a name (as in a field `amount` defined as
`amount`), the name refers to the source column inside that dataset's field
expressions and to the field everywhere else.

Consumers that generate SQL against the source tables, rather than against a
layer that exposes the fields, must replace each field reference with the
field's expression. For example, `SUM(orders.total_amount)` compiles to
`SUM(o_totalprice)` over `sales.public.orders`.

This version of the specification does not support:

- a field expression that refers to another field, in the same or another
  dataset;
- a metric expression that refers to another metric;
- a dataset that uses another dataset as its `source`.

Future versions may add these.

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
              expression: SUM(amount)
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

    # Dataset-scoped: resolves entirely within the customers dataset
    metrics:
      - name: customer_count
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(DISTINCT id)
        description: Total number of customers
        datatype: Integer
        ai_context:
          synonyms:
            - "total customers"
            - "customer base"

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
  - Breaking: metric expressions, `primary_key`, `unique_keys`, and relationship `from_columns`/`to_columns` refer to dataset fields, not columns of the dataset's `source`. Model-scoped metric expressions must use qualified `dataset.field` references; dataset-scoped metric expressions use unqualified field names. Earlier versions did not specify this, and some converters read these names as source columns; those converters must now resolve them as fields. See [Name Resolution](#name-resolution).
- **0.1.1** (2025-12-11): Initial release
  - Core semantic model structure
  - Support for datasets, relationships, fields, and metrics
  - Multi-dialect metric expressions
  - Vendor extensibility framework
  - Context for agents

---

## License

See LICENSE file for details.
