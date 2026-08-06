# FINOS Legend Converter - Design Document

## Overview

The FINOS Legend converter translates OSI (Open Semantic Interchange) semantic models into FINOS Legend Database representations. This converter enables interoperability between the two specification standards and allows OSI models to be imported into the FINOS Legend platform.

## Architecture

### Directory Structure

```
converters/legend/
├── README.md                          # User-facing documentation
├── pyproject.toml                     # Python project configuration
├── requirements.txt                   # Runtime dependencies
├── src/
│   ├── cli.py                         # Command-line interface
│   └── legend_osi/
│       ├── __init__.py                # Public API exports
│       ├── models.py                  # FINOS Legend data models
│       └── osi_to_legend.py           # Main conversion logic
└── tests/
    ├── __init__.py
    ├── conftest.py                    # Test fixtures
    ├── test_osi_to_legend.py          # Comprehensive test suite
    └── fixtures/
        └── tpcds_osi.yaml             # Example OSI model
```

### Core Components

#### 1. **Models** (`models.py`)
Defines the FINOS Legend data model hierarchy:

- `Column`: Represents a physical table column
- `Table`: Physical table with schema, database, and columns
- `Relation`: Logical relation pointing to a physical table
- `RelationColumnMapping`: Maps logical fields to physical columns
- `Join`: Join path between relations
- `LegendDatabase`: Top-level database containing tables, relations, and joins
- `LegendModel`: Wrapper for the complete Legend model (version + databases)

Each model includes `to_dict()` method for JSON serialization.

#### 2. **Conversion Logic** (`osi_to_legend.py`)
Main conversion pipeline:

```
OSI YAML
   ↓
osi_to_legend_dict() or osi_to_legend_json()
   ↓
_convert_osi_to_legend_model()
   ↓
_convert_semantic_model_to_database()
   ├→ _convert_dataset_to_table_and_relation() [per dataset]
   ├→ _convert_join_path_to_join() [per join_path]
   └→ _infer_field_type() [for each field]
   ↓
LegendModel
   ↓
JSON output
```

#### 3. **Public API** (`__init__.py`)
Exposes three main entry points:

- `osi_to_legend_json(osi_model, database_package)` → JSON string
- `osi_to_legend_dict(osi_model, database_package)` → Python dict
- `OsiToLegendConversionError` → Exception class

#### 4. **CLI** (`cli.py`)
Command-line interface for batch conversion:

```bash
python src/cli.py -i osi_model.yaml -o legend_model.json -p org.example.pkg
```

## Mapping Strategy

### Semantic Model → Database

| OSI | FINOS Legend | Notes |
|---|---|---|
| `semantic_model.name` | `Database.name` | Primary identifier |
| `semantic_model.description` | `Database.description` | Optional metadata |
| `semantic_model.datasets` | `Database.tables` + `Database.relations` | Dual representation |
| `semantic_model.join_paths` | `Database.joins` | Direct mapping |

**Rationale**: A FINOS Legend Database is the semantic model abstraction. It contains both physical table definitions and logical relations that abstract over them.

### Dataset → Table + Relation

Each OSI dataset produces **two** FINOS Legend artifacts:

#### Physical Table (Logical Layer)
```
Table {
  name: dataset.name,
  schema: (parsed from dataset.source),
  database: (parsed from dataset.source),
  columns: [Column(field) for field in dataset.fields]
}
```

#### Logical Relation (Semantic Layer)
```
Relation {
  name: dataset.name,
  primaryTable: table.name,
  columnMappings: [RelationColumnMapping(field → column)]
}
```

**Why Dual Representation?**
- **Physical Table**: Describes the actual data structure and storage location
- **Logical Relation**: Provides a semantic abstraction over the physical table, enabling field-level mappings and business metadata

This separation aligns with FINOS Legend's core principle of separating logical from physical modeling.

### Source String Parsing

The `dataset.source` string is parsed into database/schema/table components:

```
Format: "database.schema.table"
        ↓
database="database", schema="schema", table="table"

Format: "schema.table"
        ↓
database="default", schema="schema", table="table"

Format: "table"
        ↓
database="default", schema="public", table="table"
```

This flexible parsing accommodates various source naming conventions.

### Join Paths → Joins

OSI join_paths map directly to FINOS Legend joins:

```yaml
# OSI join_path
- name: orders_to_customers
  from: orders
  to: customers
  from_columns: [customer_id]
  to_columns: [customer_id]

# FINOS Legend Join
{
  "name": "orders_to_customers",
  "fromRelation": "orders",
  "toRelation": "customers",
  "fromColumns": ["customer_id"],
  "toColumns": ["customer_id"],
  "joinType": "INNER"
}
```

**Composite Keys**: Fully supported via parallel column arrays:

```yaml
from_columns: [order_id, variant_id]
to_columns: [id, variant_id]
```

### Field → Column Type Inference

Type inference uses a priority hierarchy:

1. **FINOS Custom Extension**
   ```yaml
   custom_extensions:
     - vendor_name: FINOS
       data: '{"type": "DECIMAL(18,2)"}'
   ```

2. **Dimension Metadata**
   ```yaml
   dimension:
     is_time: true  # → TIMESTAMP
   ```

3. **Expression Heuristics** (ANSI_SQL)
   ```yaml
   expression:
     dialects:
       - dialect: ANSI_SQL
         expression: "CAST(qty AS INT)"  # → INTEGER
   ```

4. **Default**
   ```
   VARCHAR(256)
   ```

**Type Mapping Examples:**

| OSI | FINOS Legend Type |
|---|---|
| `is_time: true` | `TIMESTAMP` |
| ANSI_SQL contains "DATE", "TIME" | `TIMESTAMP` |
| ANSI_SQL contains "INT" | `INTEGER` |
| ANSI_SQL contains "FLOAT", "DECIMAL" | `DECIMAL(18,2)` |
| ANSI_SQL contains "BOOL" | `BOOLEAN` |
| No hints | `VARCHAR(256)` |

## Data Flow Example

### Input: OSI Model

```yaml
version: "0.1.1"
semantic_model:
  - name: orders_model
    datasets:
      - name: orders
        source: warehouse.public.orders
        fields:
          - name: order_id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: order_id
          - name: order_date
            dimension:
              is_time: true
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: order_date
    join_paths: []
```

### Conversion Process

1. **Parse OSI**: Load YAML, validate structure
2. **Create Database**: `LegendDatabase(name="orders_model")`
3. **Convert Dataset**:
   - Parse source: `warehouse.public.orders`
   - Create Table: `{name: "orders", schema: "public", database: "warehouse"}`
   - Create Relation: `{name: "orders", primaryTable: "orders"}`
4. **Convert Fields**:
   - `order_id` → Column(type=VARCHAR) → ColumnMapping
   - `order_date` → Column(type=TIMESTAMP) → ColumnMapping
5. **Serialize**: Output JSON representation

### Output: FINOS Legend JSON

```json
{
  "version": "1.0.0",
  "databases": [
    {
      "name": "orders_model",
      "package": "org.finos.osi.generated",
      "tables": [
        {
          "name": "orders",
          "schema": "public",
          "database": "warehouse",
          "columns": [
            {"name": "order_id", "type": "VARCHAR(256)", "nullable": true},
            {"name": "order_date", "type": "TIMESTAMP", "nullable": true}
          ]
        }
      ],
      "relations": [
        {
          "name": "orders",
          "primaryTable": "orders",
          "columnMappings": [
            {"relationField": "order_id", "physicalColumn": "order_id"},
            {"relationField": "order_date", "physicalColumn": "order_date"}
          ]
        }
      ]
    }
  ]
}
```

## Error Handling & Validation

### Validation Rules

1. **Semantic Model Required**: Must contain non-empty `semantic_model` list
2. **Model Name Required**: Each semantic model must have a `name`
3. **Join Path Completeness**: Must have `name`, `from`, `to`, `from_columns`, `to_columns`

### Warnings

- **Unsupported OSI Version**: Logged if version ≠ 0.1.1
- **Multiple Semantic Models**: Only first is converted
- **Invalid Join Paths**: Skipped with warning
- **Missing Dataset Fields**: Logged and skipped

## Testing Strategy

### Test Coverage

**Unit Tests** (`test_osi_to_legend.py`):
- Basic conversion (name, package, description)
- Table conversion (source parsing, column creation)
- Type inference (defaults, hints, heuristics)
- Relation creation and mappings
- Join conversion (simple and composite)
- JSON output validation
- Error handling and validation
- Complex scenarios (e-commerce model, composite keys)

**Fixtures** (`conftest.py`):
- `simple_osi_model`: Single dataset
- `complex_osi_model`: Multi-dataset with joins (e-commerce)
- `osi_with_custom_extensions`: Custom FINOS type hints

### Test Execution

```bash
pytest tests/                    # Run all tests
pytest tests/ --cov=legend_osi  # With coverage
pytest -v tests/                # Verbose output
```

## Extension Points

### Future Enhancements

1. **Metrics Conversion** (v0.2)
   - Map OSI `metrics` to FINOS Legend derived tables or expressions
   - Handle multi-dialect metric definitions

2. **Ontology Support** (v0.2+)
   - Map OSI ontology concepts to Legend semantic concepts
   - Support for relationships and role mappings

3. **Custom Mappings** (v0.2)
   - Allow users to define custom transformation rules
   - Plugin architecture for field type inference

4. **Pure DSL Output** (v0.3)
   - Generate FINOS Pure DSL code directly
   - Full Legend Studio compatibility

5. **Bi-Directional Conversion** (v1.0)
   - Legend → OSI reverse conversion
   - Round-trip fidelity validation

## Dependencies

- `pyyaml>=6.0.1`: YAML parsing
- `pytest>=8.3.5`: Testing (dev)
- `pytest-cov>=6.0.0`: Coverage (dev)
- `ruff>=0.11.5`: Linting (dev)

## Usage Patterns

### Pattern 1: Direct Python API

```python
import yaml
from legend_osi import osi_to_legend_dict

with open("model.yaml") as f:
    osi = yaml.safe_load(f)

legend = osi_to_legend_dict(osi, database_package="org.my.company")
print(json.dumps(legend, indent=2))
```

### Pattern 2: CLI Batch Conversion

```bash
for f in osi_models/*.yaml; do
  python src/cli.py -i "$f" -o "legend_models/$(basename $f .yaml).json"
done
```

### Pattern 3: Programmatic Integration

```python
from legend_osi import osi_to_legend_dict, OsiToLegendConversionError

def convert_and_upload(osi_path, legend_api):
    try:
        osi_model = load_osi(osi_path)
        legend_dict = osi_to_legend_dict(osi_model)
        legend_api.create_database(legend_dict)
    except OsiToLegendConversionError as e:
        logger.error(f"Conversion failed: {e}")
        raise
```

## Performance Considerations

- **Time Complexity**: O(n) where n = total fields + joins
- **Space Complexity**: O(n) for model storage
- **Bottleneck**: YAML parsing (library-dependent, not converter)
- **Scalability**: Tested with models up to ~100 datasets

## Compatibility

- **Python**: ≥3.12
- **FINOS Legend**: ≥ 2024 (assumes standard metamodel)
- **OSI**: 0.1.1 (primary), 0.1.2 (with warnings)
- **YAML**: Full YAML 1.2 support via PyYAML

## License

Follows OSI project license (see root LICENSE file).

---

**Version**: 0.1.0  
**Last Updated**: 2026-05-18
