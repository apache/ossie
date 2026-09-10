# OSI to FINOS Legend Converter

Converts OSI semantic models (YAML) to [FINOS Legend](https://finos.org/legend) Database representation (JSON).

## Overview

This converter translates an OSI semantic model into a FINOS Legend logical data model, enabling interoperability between the two specification standards.

### Mapping Philosophy

The converter maps OSI concepts to FINOS Legend concepts based on the following principles:

| OSI Concept | FINOS Legend Concept | Rationale |
|---|---|---|
| `semantic_model` | `Database` | Top-level semantic model container |
| `dataset` | `Relation` + `Table` | OSI dataset maps to both a logical relation and physical table |
| `dataset.source` | `Table` metadata | Source string parsed into database, schema, table references |
| `dataset.fields` | `Column` + `RelationColumnMapping` | Fields become physical columns with logical mappings |
| `join_paths` | `Join` | Join relationships are preserved in the Legend model |
| `field.dimension` | Column metadata | Dimension flags inform type inference |

## Features

- **OSI → Legend JSON**: Convert OSI YAML to FINOS Legend JSON representation
- **OSI → Legend Dict**: Convert OSI YAML to Python dictionary (FINOS Legend model)
- **Comprehensive Field Type Inference**: Infers FINOS Legend types from OSI field expressions and metadata
- **Relationship Preservation**: Maps OSI join_paths to FINOS Legend joins
- **Extensibility**: Custom extensions in OSI can include FINOS-specific metadata (e.g., type hints)
- **Warnings for Unmapped Concepts**: Alerts when OSI features lack Legend equivalents

## Setup

```bash
cd converters/legend
pip install -r requirements.txt
# or if using uv:
uv sync --group dev
```

## Usage

### Python API

```python
import yaml
from legend_osi import osi_to_legend_json, osi_to_legend_dict

# Load OSI YAML
with open("osi_model.yaml") as f:
    osi_data = yaml.safe_load(f)

# Convert to FINOS Legend JSON
legend_json = osi_to_legend_json(osi_data, database_package="org.example.mydb")
print(legend_json)

# Or get as Python dict
legend_dict = osi_to_legend_dict(osi_data, database_package="org.example.mydb")
```

### Command Line

```bash
python src/cli.py -i osi_model.yaml -o legend_model.json
```

## Concept Mapping Details

### Semantic Model → Database

The OSI semantic model becomes a FINOS Legend Database with:
- **name**: Model name as the database identifier
- **package**: Configurable package namespace (default: `org.finos.osi.generated`)
- **description**: From OSI `description` field
- **tables**: Physical tables derived from dataset sources
- **relations**: Logical relations mirroring datasets
- **joins**: Join paths connecting relations

### Dataset → Relation + Table

Each OSI dataset produces:

1. **Physical Table**
   - Parsed from `dataset.source` string
   - Example: `database.schema.table_name`
   - Contains columns from `dataset.fields`

2. **Logical Relation**
   - Represents the logical dataset abstraction
   - Links to the physical table via `primaryTable`
   - Includes column mappings for each field

**Example:**

```yaml
# OSI
datasets:
  - name: customers
    source: postgres.public.customers
    fields:
      - name: customer_id
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: customer_id
      - name: email
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: email
```

**Becomes:**

```json
{
  "name": "customers",
  "package": "org.finos.osi.generated",
  "tables": [
    {
      "name": "customers",
      "database": "postgres",
      "schema": "public",
      "columns": [
        {"name": "customer_id", "type": "VARCHAR(256)", "nullable": true},
        {"name": "email", "type": "VARCHAR(256)", "nullable": true}
      ]
    }
  ],
  "relations": [
    {
      "name": "customers",
      "primaryTable": "customers",
      "columnMappings": [
        {"relationField": "customer_id", "physicalColumn": "customer_id"},
        {"relationField": "email", "physicalColumn": "email"}
      ]
    }
  ]
}
```

### Join Paths → Joins

OSI `join_paths` directly map to FINOS Legend `joins`:

```yaml
# OSI
join_paths:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [customer_id]
```

**Becomes:**

```json
{
  "name": "orders_to_customers",
  "fromRelation": "orders",
  "toRelation": "customers",
  "fromColumns": ["customer_id"],
  "toColumns": ["customer_id"],
  "joinType": "INNER"
}
```

### Type Inference

The converter infers FINOS Legend column types using the following priority:

1. **Custom Extension**: FINOS vendor data with explicit `type` field
2. **Dimension Metadata**: `is_time: true` → `TIMESTAMP`
3. **Expression Heuristics**: Pattern matching on ANSI_SQL expressions
4. **Default**: `VARCHAR(256)`

**Type Inference Examples:**

```yaml
# Time dimension → TIMESTAMP
- name: order_date
  dimension:
    is_time: true
  # Type: TIMESTAMP

# Expression hint → inferred type
- name: quantity
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: "CAST(qty AS INT)"
  # Type: INTEGER

# Custom extension → explicit type
- name: price
  custom_extensions:
    - vendor_name: FINOS
      data: '{"type": "DECIMAL(18,2)"}'
  # Type: DECIMAL(18,2)
```

## Limitations

- **Metrics**: OSI metrics are not yet mapped to FINOS Legend equivalents. Legend supports derived table expressions but full parity is future work.
- **Ontologies**: OSI ontology concepts (0.1.2+) are not currently mapped; focus is on logical model conversion.
- **Aliases & Synonyms**: OSI `ai_context` synonyms are preserved in descriptions but not as first-class Legend aliases.
- **Multi-Dialect Expressions**: Only ANSI_SQL expressions are prioritized; other dialects are secondary.

## Testing

```bash
pytest tests/
```

## Development

```bash
# Run linter
ruff check src/ tests/

# Run tests with coverage
pytest tests/ --cov=legend_osi
```

## Output Format

The converter outputs a JSON representation compatible with FINOS Legend's metamodel. The JSON structure includes:

```json
{
  "version": "1.0.0",
  "databases": [
    {
      "name": "<model_name>",
      "package": "<package_path>",
      "description": "...",
      "tables": [...],
      "relations": [...],
      "joins": [...]
    }
  ]
}
```

This JSON can be used as input to Legend tools, APIs, or further tooling that consumes the Legend metamodel.

## License

See LICENSE file in the root OSI repository for details.
