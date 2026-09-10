# 🎯 FINOS Legend Converter - Quick Start Guide

## What You Now Have

A complete, production-ready Python converter that translates OSI semantic models to FINOS Legend Database format.

```
┌─────────────────────────────────────────────────────────────┐
│                    OSI Semantic Model (YAML)                │
│  • semantic_model: name, description, datasets, join_paths  │
│  • datasets: name, source, fields, primary_key              │
│  • fields: name, expression, dimension, custom_extensions   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ osi_to_legend_dict()
                           │ osi_to_legend_json()
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              FINOS Legend Database (JSON)                    │
│  • Database: name, package, tables, relations, joins        │
│  • Tables: physical tables with columns                      │
│  • Relations: logical abstractions over tables               │
│  • Joins: relationships between relations                    │
└─────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
converters/legend/                    ← Your new converter
├── 📖 README.md                       User guide
├── 🏗️ DESIGN.md                       Architecture & mapping docs
├── 📋 IMPLEMENTATION_SUMMARY.md       This quick reference
├── 📦 pyproject.toml                  Package config (hatchling)
├── 📝 requirements.txt                Dependencies (pyyaml)
│
├── src/
│   ├── 🖥️ cli.py                     Command-line interface
│   └── legend_osi/
│       ├── __init__.py                Public API exports
│       ├── 🔧 models.py               FINOS Legend data models
│       └── ⚙️ osi_to_legend.py        Conversion engine
│
└── tests/
    ├── conftest.py                    Test fixtures (3 example models)
    ├── ✅ test_osi_to_legend.py       30+ comprehensive tests
    ├── fixtures/
    │   └── 📊 tpcds_osi.yaml          Example e-commerce model
    └── __init__.py
```

## Quick Setup (3 Steps)

### 1️⃣ Install Dependencies
```bash
cd converters/legend
pip install -r requirements.txt
```

### 2️⃣ Verify with Tests
```bash
pytest tests/ -v
```
Expected: **30+ tests pass** ✅

### 3️⃣ Try Converting a Model
```bash
# Using Python API
python -c "
import yaml
from legend_osi import osi_to_legend_json

with open('tests/fixtures/tpcds_osi.yaml') as f:
    osi = yaml.safe_load(f)

legend_json = osi_to_legend_json(osi)
print(legend_json)
"

# OR using CLI
python src/cli.py -i tests/fixtures/tpcds_osi.yaml -o /tmp/legend_output.json
```

## Core Mapping (At a Glance)

### OSI → FINOS Legend Translation

```yaml
# Input: OSI Semantic Model
semantic_model:
  name: my_model                    ──→ Database.name
  description: ...                 ──→ Database.description
  
  datasets:                         ──→ Tables + Relations
    - name: customers
      source: db.schema.customers  ──→ Table(db, schema, customers)
      fields:
        - name: id                  ──→ Column + RelationColumnMapping
          dimension:
            is_time: true           ──→ Type = TIMESTAMP
  
  join_paths:                       ──→ Joins
    - from: orders
      to: customers
      from_columns: [cust_id]
      to_columns: [id]
```

**Result**: FINOS Legend Database with dual logical/physical representation

### Source String Parsing

```
"db.schema.table"     → database="db",      schema="schema",  table="table"
"schema.table"        → database="default", schema="schema",  table="table"
"table"               → database="default", schema="public",  table="table"
```

### Type Inference Priority

1. **FINOS Custom Extension** (explicit)
   ```yaml
   custom_extensions:
     - vendor_name: FINOS
       data: '{"type": "DECIMAL(18,2)"}'
   ```

2. **Dimension Metadata** (is_time → TIMESTAMP)
   ```yaml
   dimension:
     is_time: true
   ```

3. **ANSI_SQL Expression** (pattern matching)
   ```yaml
   expression:
     dialects:
       - dialect: ANSI_SQL
         expression: "CAST(qty AS INT)"
   ```

4. **Default** → VARCHAR(256)

## Python API - 3 Ways to Use

### Method 1: Direct Conversion (Simple)
```python
from legend_osi import osi_to_legend_json
import yaml

osi_model = yaml.safe_load(open("model.yaml"))
legend_json = osi_to_legend_json(osi_model)
print(legend_json)
```

### Method 2: Get Python Dict (For Inspection)
```python
from legend_osi import osi_to_legend_dict

legend_dict = osi_to_legend_dict(osi_model, database_package="org.example.db")
db = legend_dict["databases"][0]
print(f"Tables: {len(db['tables'])}")
print(f"Relations: {len(db['relations'])}")
print(f"Joins: {len(db.get('joins', []))}")
```

### Method 3: Error Handling (Production)
```python
from legend_osi import osi_to_legend_dict, OsiToLegendConversionError

try:
    legend = osi_to_legend_dict(osi_model)
except OsiToLegendConversionError as e:
    print(f"Conversion failed: {e}")
    # Handle gracefully
```

## Command-Line Usage

```bash
# Basic conversion
python src/cli.py -i input.yaml -o output.json

# With custom package
python src/cli.py \
  -i input.yaml \
  -o output.json \
  -p "com.mycompany.analytics"

# Batch conversion
for f in osi_models/*.yaml; do
  python src/cli.py -i "$f" -o "legend_$(basename $f .yaml).json"
done
```

**Output**: 
```
✓ Converted input.yaml → output.json
  Database: my_model
  Tables: 3
  Relations: 3
  Joins: 2
```

## Test Suite Overview

| Category | Tests | Coverage |
|---|---|---|
| Basic Conversion | 3 | Database setup, names, packages |
| Table Conversion | 5 | Source parsing, schemas, columns |
| Type Inference | 5 | Defaults, time dims, extensions |
| Relation Creation | 3 | Mappings, descriptions |
| Join Conversion | 3 | Simple & composite keys |
| JSON Output | 2 | Valid format, structure |
| Error Handling | 7 | Validation, warnings, edge cases |
| Complex Scenarios | 2 | E-commerce, composite keys |
| **TOTAL** | **30+** | **Comprehensive** |

Run all tests:
```bash
pytest tests/ -v                    # Verbose
pytest tests/ --cov=legend_osi      # With coverage
pytest tests/ -k TableConversion    # Specific category
```

## Example Conversion

### Input: OSI Model (`tpcds_osi.yaml`)
```yaml
version: "0.1.1"
semantic_model:
  - name: tpcds_analytics
    description: TPC-DS e-commerce analytics
    datasets:
      - name: orders
        source: tpcds.public.orders
        fields:
          - name: order_id
            dimension: {is_time: false}
          - name: order_date
            dimension: {is_time: true}
      - name: customers
        source: tpcds.public.customers
    join_paths:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
```

### Output: FINOS Legend JSON
```json
{
  "version": "1.0.0",
  "databases": [
    {
      "name": "tpcds_analytics",
      "package": "org.finos.osi.generated",
      "description": "TPC-DS e-commerce analytics",
      "tables": [
        {
          "name": "orders",
          "schema": "public",
          "database": "tpcds",
          "columns": [
            {"name": "order_id", "type": "VARCHAR(256)", "nullable": true},
            {"name": "order_date", "type": "TIMESTAMP", "nullable": true}
          ]
        },
        {
          "name": "customers",
          "schema": "public",
          "database": "tpcds",
          "columns": [...]
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
        },
        {
          "name": "customers",
          "primaryTable": "customers",
          "columnMappings": [...]
        }
      ],
      "joins": [
        {
          "name": "orders_to_customers",
          "fromRelation": "orders",
          "toRelation": "customers",
          "fromColumns": ["customer_id"],
          "toColumns": ["id"],
          "joinType": "INNER"
        }
      ]
    }
  ]
}
```

## Key Features ✨

✅ **Complete Mapping**: semantic_model → database, dataset → table+relation, join_paths → joins

✅ **Smart Type Inference**: 4-level priority system (custom extension → dimension → expression → default)

✅ **Dual Logical/Physical Model**: Follows FINOS Legend's core architectural principle

✅ **Flexible Source Parsing**: Handles 1, 2, or 3-part source strings

✅ **Composite Key Support**: Multiple join columns fully supported

✅ **CLI + Python API**: Both command-line and programmatic interfaces

✅ **Comprehensive Testing**: 30+ tests covering edge cases and error conditions

✅ **Production Ready**: Error handling, validation, warnings, clear messages

✅ **Well Documented**: README, DESIGN guide, inline comments, type hints

## Current Limitations

⚠️ **Metrics**: OSI metrics not yet mapped to Legend equivalents (future: v0.2)

⚠️ **Ontologies**: OSI ontology concepts (v0.1.2+) not supported (future: v0.3)

⚠️ **Pure DSL**: JSON output only (future: v0.3 for Pure DSL generation)

⚠️ **Bi-directional**: One-way conversion only (Legend → OSI future: v1.0)

## Documentation Files

| File | Purpose |
|---|---|
| **README.md** | User guide, features, setup, usage patterns |
| **DESIGN.md** | Architecture, mapping strategy, data flow, extension points |
| **IMPLEMENTATION_SUMMARY.md** | What was built, file structure, status |
| **tests/fixtures/tpcds_osi.yaml** | Real example: e-commerce model |

## Troubleshooting

### Issue: "Unsupported OSI version"
```
⚠️ Warning: OSI version '0.2.0' may not be fully supported. Tested with: 0.1.1
```
**Fix**: Use OSI version 0.1.1; version 0.2.0+ has additions but core conversion still works

### Issue: "Incomplete join_path definition"
```
⚠️ Warning: Incomplete join_path definition: {...}. Skipping.
```
**Fix**: Ensure join_path has: name, from, to, from_columns, to_columns

### Issue: Type not detected correctly
```python
# Add explicit type hint:
custom_extensions:
  - vendor_name: FINOS
    data: '{"type": "DECIMAL(18,4)"}'
```

## Next Steps

1. ✅ **Verify Installation**: `pytest tests/ -v`
2. ✅ **Try Example**: `python src/cli.py -i tests/fixtures/tpcds_osi.yaml -o /tmp/out.json`
3. ✅ **Integrate**: Import in your code: `from legend_osi import osi_to_legend_dict`
4. 📝 **Read Docs**: Check README.md and DESIGN.md for deep dives

## Support Resources

- **Quick Setup**: This document
- **User Guide**: README.md (features, usage patterns)
- **Architecture**: DESIGN.md (mapping strategy, data flow)
- **Code**: Fully commented source with type hints
- **Tests**: 30+ examples showing every feature
- **Examples**: `tests/fixtures/tpcds_osi.yaml`

---

**Status**: ✅ Complete & Ready for Production  
**Version**: 0.1.0  
**Location**: `converters/legend/`

🚀 **You're ready to convert OSI models to FINOS Legend!**
