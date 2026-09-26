"""Tests for OSI to Legend converter."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from legend_osi import osi_to_legend_dict, osi_to_legend_json, osi_to_legend_pure, OsiToLegendConversionError


class TestBasicConversion:
    """Test basic OSI to Legend conversion."""

    def test_simple_model_conversion(self, simple_osi_model):
        """Test conversion of simple OSI model to Legend format."""
        result = osi_to_legend_dict(simple_osi_model)

        assert result is not None
        assert "version" in result
        assert "databases" in result
        assert len(result["databases"]) == 1

    def test_database_name_and_package(self, simple_osi_model):
        """Test that database name and package are set correctly."""
        result = osi_to_legend_dict(
            simple_osi_model, database_package="org.example.test"
        )
        db = result["databases"][0]

        assert db["name"] == "simple_model"
        assert db["package"] == "org.example.test"
        assert db["description"] == "A simple test model"

    def test_database_defaults_package(self, simple_osi_model):
        """Test that package defaults to org.finos.osi.generated."""
        result = osi_to_legend_dict(simple_osi_model)
        db = result["databases"][0]

        assert db["package"] == "org.finos.osi.generated"


class TestTableConversion:
    """Test conversion of OSI datasets to Legend tables."""

    def test_single_dataset_creates_table(self, simple_osi_model):
        """Test that a single dataset creates a table."""
        result = osi_to_legend_dict(simple_osi_model)
        db = result["databases"][0]

        assert len(db["tables"]) == 1
        table = db["tables"][0]
        assert table["name"] == "users"
        assert table["schema"] == "public"
        assert table["database"] == "mydb"

    def test_source_parsing_three_parts(self, simple_osi_model):
        """Test parsing source string with database.schema.table format."""
        result = osi_to_legend_dict(simple_osi_model)
        table = result["databases"][0]["tables"][0]

        assert table["database"] == "mydb"
        assert table["schema"] == "public"
        assert table["name"] == "users"

    def test_source_parsing_two_parts(self):
        """Test parsing source string with schema.table format."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "test",
                    "datasets": [
                        {
                            "name": "test_table",
                            "source": "myschema.mytable",
                            "fields": [],
                        }
                    ],
                }
            ],
        }
        result = osi_to_legend_dict(osi)
        table = result["databases"][0]["tables"][0]

        assert table["database"] == "default"
        assert table["schema"] == "myschema"
        assert table["name"] == "mytable"

    def test_source_parsing_one_part(self):
        """Test parsing source string with just table name."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "test",
                    "datasets": [
                        {
                            "name": "simple_table",
                            "source": "mytable",
                            "fields": [],
                        }
                    ],
                }
            ],
        }
        result = osi_to_legend_dict(osi)
        table = result["databases"][0]["tables"][0]

        assert table["database"] == "default"
        assert table["schema"] == "public"
        assert table["name"] == "mytable"

    def test_columns_from_fields(self, simple_osi_model):
        """Test that dataset fields become table columns."""
        result = osi_to_legend_dict(simple_osi_model)
        table = result["databases"][0]["tables"][0]

        assert len(table["columns"]) == 3
        column_names = {c["name"] for c in table["columns"]}
        assert column_names == {"user_id", "email", "created_at"}


class TestFieldTypeInference:
    """Test type inference for dataset fields."""

    def test_default_type_varchar(self, simple_osi_model):
        """Test that fields default to VARCHAR(256)."""
        result = osi_to_legend_dict(simple_osi_model)
        table = result["databases"][0]["tables"][0]

        email_col = next(c for c in table["columns"] if c["name"] == "email")
        assert email_col["type"] == "VARCHAR(256)"

    def test_time_dimension_becomes_timestamp(self, simple_osi_model):
        """Test that time dimensions become TIMESTAMP type."""
        result = osi_to_legend_dict(simple_osi_model)
        table = result["databases"][0]["tables"][0]

        created_at_col = next(c for c in table["columns"] if c["name"] == "created_at")
        assert created_at_col["type"] == "TIMESTAMP"

    def test_custom_extension_type_hint(self, osi_with_custom_extensions):
        """Test that FINOS custom extension type hints are honored."""
        result = osi_to_legend_dict(osi_with_custom_extensions)
        table = result["databases"][0]["tables"][0]

        tx_id_col = next(
            c for c in table["columns"] if c["name"] == "transaction_id"
        )
        assert tx_id_col["type"] == "BIGINT"

        price_col = next(c for c in table["columns"] if c["name"] == "price")
        assert price_col["type"] == "DECIMAL(18,4)"

    def test_field_description_preserved(self, simple_osi_model):
        """Test that field descriptions are preserved in columns."""
        result = osi_to_legend_dict(simple_osi_model)
        table = result["databases"][0]["tables"][0]

        user_id_col = next(c for c in table["columns"] if c["name"] == "user_id")
        assert user_id_col["description"] == "Unique user identifier"


class TestRelationConversion:
    """Test conversion of OSI datasets to Legend relations."""

    def test_single_dataset_creates_relation(self, simple_osi_model):
        """Test that a single dataset creates a relation."""
        result = osi_to_legend_dict(simple_osi_model)
        db = result["databases"][0]

        assert len(db["relations"]) == 1
        relation = db["relations"][0]
        assert relation["name"] == "users"
        assert relation["primaryTable"] == "users"

    def test_relation_column_mappings(self, simple_osi_model):
        """Test that relation includes column mappings."""
        result = osi_to_legend_dict(simple_osi_model)
        relation = result["databases"][0]["relations"][0]

        assert "columnMappings" in relation
        mappings = relation["columnMappings"]
        assert len(mappings) == 3

        mapping_names = {m["relationField"] for m in mappings}
        assert mapping_names == {"user_id", "email", "created_at"}

    def test_relation_preserves_description(self, simple_osi_model):
        """Test that relation description is preserved from dataset."""
        result = osi_to_legend_dict(simple_osi_model)
        relation = result["databases"][0]["relations"][0]

        assert relation["description"] == "User accounts"


class TestJoinConversion:
    """Test conversion of OSI join_paths to Legend joins."""

    def test_join_paths_become_joins(self, complex_osi_model):
        """Test that join_paths are converted to joins."""
        result = osi_to_legend_dict(complex_osi_model)
        db = result["databases"][0]

        assert len(db["joins"]) == 2

    def test_join_structure(self, complex_osi_model):
        """Test that join structure is correct."""
        result = osi_to_legend_dict(complex_osi_model)
        joins = result["databases"][0]["joins"]

        orders_customers_join = next(
            j for j in joins if j["name"] == "orders_to_customers"
        )
        assert orders_customers_join["fromRelation"] == "orders"
        assert orders_customers_join["toRelation"] == "customers"
        assert orders_customers_join["fromColumns"] == ["customer_id"]
        assert orders_customers_join["toColumns"] == ["customer_id"]
        assert orders_customers_join["joinType"] == "INNER"

    def test_multiple_joins(self, complex_osi_model):
        """Test handling of multiple joins."""
        result = osi_to_legend_dict(complex_osi_model)
        joins = result["databases"][0]["joins"]

        join_names = {j["name"] for j in joins}
        assert "orders_to_customers" in join_names
        assert "orders_to_products" in join_names


class TestJsonOutput:
    """Test JSON output format."""

    def test_to_json_returns_valid_json(self, simple_osi_model):
        """Test that osi_to_legend_json returns valid JSON."""
        json_str = osi_to_legend_json(simple_osi_model)

        # Should not raise
        parsed = json.loads(json_str)
        assert parsed is not None
        assert "version" in parsed
        assert "databases" in parsed

    def test_json_contains_all_elements(self, complex_osi_model):
        """Test that JSON output contains tables, relations, and joins."""
        json_str = osi_to_legend_json(complex_osi_model)
        parsed = json.loads(json_str)

        db = parsed["databases"][0]
        assert len(db["tables"]) > 0
        assert len(db["relations"]) > 0
        assert len(db["joins"]) > 0


class TestErrorHandling:
    """Test error handling and validation."""

    def test_missing_semantic_model(self):
        """Test error when neither semantic_model nor ontology is present."""
        osi = {"version": "0.1.1"}

        with pytest.raises(
            OsiToLegendConversionError,
            match="at least one of 'semantic_model' or top-level 'ontology' must be provided",
        ):
            osi_to_legend_dict(osi)

    def test_empty_semantic_model_list(self):
        """Test error when semantic_model is empty and ontology is absent."""
        osi = {"version": "0.1.1", "semantic_model": []}

        with pytest.raises(
            OsiToLegendConversionError,
            match="at least one of 'semantic_model' or top-level 'ontology' must be provided",
        ):
            osi_to_legend_dict(osi)

    def test_missing_semantic_model_allowed_with_ontology(self):
        """Test ontology-only YAML is accepted when semantic_model is omitted."""
        osi = {
            "version": "0.1.2",
            "ontology": [
                {
                    "concept": "Person",
                    "relationships": [
                        {
                            "name": "earns",
                            "roles": [{"player": "Salary"}],
                            "multiplicity": "ManyToOne",
                        }
                    ],
                },
                {"concept": "Salary", "extends": ["Decimal"]},
            ],
        }

        result = osi_to_legend_dict(osi)

        assert result["databases"][0]["name"] == "ontology_model"
        assert len(result["databases"][0]["ontology"]) == 2

    def test_missing_model_name(self):
        """Test error when semantic model has no name."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [{"datasets": []}],
        }

        with pytest.raises(OsiToLegendConversionError):
            osi_to_legend_dict(osi)

    def test_unsupported_version_warning(self):
        """Test warning for unsupported OSI version."""
        osi = {
            "version": "0.2.0",
            "semantic_model": [
                {
                    "name": "test",
                    "datasets": [],
                }
            ],
        }

        with pytest.warns(UserWarning, match="may not be fully supported"):
            osi_to_legend_dict(osi)

    def test_multiple_models_warning(self):
        """Test warning when multiple semantic models are provided."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {"name": "model1", "datasets": []},
                {"name": "model2", "datasets": []},
            ],
        }

        with pytest.warns(UserWarning, match="Multiple semantic models"):
            osi_to_legend_dict(osi)

    def test_invalid_join_path_warning(self):
        """Test warning for invalid join_path definitions."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "test",
                    "datasets": [{"name": "t1", "source": "t1", "fields": []}],
                    "join_paths": [{"name": "incomplete_join"}],  # Missing from/to
                }
            ],
        }

        with pytest.warns(UserWarning, match="Incomplete join_path"):
            result = osi_to_legend_dict(osi)
            # Should still create model without joins
            assert len(result["databases"][0].get("joins", [])) == 0


class TestComplexScenarios:
    """Test complex real-world scenarios."""

    def test_ecommerce_model_complete(self, complex_osi_model):
        """Test complete e-commerce model conversion."""
        result = osi_to_legend_dict(complex_osi_model)
        db = result["databases"][0]

        # Should have 3 datasets -> 3 tables and 3 relations
        assert len(db["tables"]) == 3
        assert len(db["relations"]) == 3
        assert len(db["joins"]) == 2

        # Table names
        table_names = {t["name"] for t in db["tables"]}
        assert table_names == {"customers", "orders", "products"}

        # Relation names
        relation_names = {r["name"] for r in db["relations"]}
        assert relation_names == {"customers", "orders", "products"}

    def test_composite_join_paths(self):
        """Test join paths with composite keys."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "test",
                    "datasets": [
                        {
                            "name": "line_items",
                            "source": "db.line_items",
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]},
                                },
                                {
                                    "name": "variant_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "variant_id"}]},
                                },
                            ],
                        },
                        {
                            "name": "products",
                            "source": "db.products",
                            "fields": [
                                {
                                    "name": "id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "id"}]},
                                },
                                {
                                    "name": "variant_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "variant_id"}]},
                                },
                            ],
                        },
                    ],
                    "join_paths": [
                        {
                            "name": "line_to_products",
                            "from": "line_items",
                            "to": "products",
                            "from_columns": ["order_id", "variant_id"],
                            "to_columns": ["id", "variant_id"],
                        }
                    ],
                }
            ],
        }

        result = osi_to_legend_dict(osi)
        join = result["databases"][0]["joins"][0]

        assert len(join["fromColumns"]) == 2
        assert len(join["toColumns"]) == 2
        assert join["fromColumns"] == ["order_id", "variant_id"]
        assert join["toColumns"] == ["id", "variant_id"]


class TestPureDslOutput:
    """Test FINOS Legend Pure DSL text generation."""

    def test_pure_basic_syntax(self, simple_osi_model):
        """Test that Pure DSL output has valid basic syntax."""
        pure_text = osi_to_legend_pure(simple_osi_model)

        assert pure_text is not None
        assert isinstance(pure_text, str)
        assert "###Relational" in pure_text
        assert "Database org::finos::osi::generated::simple_model" in pure_text
        assert "Schema public" in pure_text
        assert "Table users" in pure_text

    def test_pure_table_declaration(self, simple_osi_model):
        """Test that tables are declared with columns."""
        pure_text = osi_to_legend_pure(simple_osi_model)

        assert "Table users (" in pure_text
        assert "user_id: VARCHAR(256)" in pure_text
        assert "email: VARCHAR(256)" in pure_text
        assert "created_at: TIMESTAMP" in pure_text

    def test_pure_primary_key_marking(self):
        """Test that primary key columns are marked in Pure."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "test_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "db.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]},
                                },
                                {
                                    "name": "customer_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        
        pure_text = osi_to_legend_pure(osi)

        assert "order_id: VARCHAR(256) PRIMARY KEY" in pure_text
        assert "customer_id: VARCHAR(256)" in pure_text

    def test_pure_multiple_schemas(self, complex_osi_model):
        """Test that multiple tables can be declared."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert "Table customers (" in pure_text
        assert "Table orders (" in pure_text
        assert "Table products (" in pure_text

    def test_pure_associations(self, complex_osi_model):
        """Test that legacy Association section is not emitted."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert "###Association" not in pure_text
        assert "orders_to_customers" in pure_text
        assert "orders_to_products" in pure_text

    def test_pure_association_structure(self, complex_osi_model):
        """Test that relationship functions use join expression form."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert "->join(" in pure_text
        assert "JoinKind.INNER" in pure_text
        assert "$x.customer_id == $y.customer_id" in pure_text

    def test_pure_valid_pure_syntax(self, simple_osi_model):
        """Test that generated Pure follows FINOS Legend conventions."""
        pure_text = osi_to_legend_pure(simple_osi_model)

        # Check fundamental FINOS Pure structure
        lines = pure_text.split("\n")
        
        # Should start with ###Relational
        assert any("###Relational" in line for line in lines)
        
        # Should have Database keyword
        assert any("Database " in line for line in lines)
        
        # Should have Schema keyword
        assert any("Schema " in line for line in lines)
        
        # Should have Table keyword
        assert any("Table " in line for line in lines)

    def test_pure_composite_keys(self):
        """Test Pure output with composite primary keys."""
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "composite_test",
                    "datasets": [
                        {
                            "name": "order_lines",
                            "source": "db.order_lines",
                            "primary_key": ["order_id", "line_number"],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]},
                                },
                                {
                                    "name": "line_number",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "line_number"}]},
                                },
                                {
                                    "name": "amount",
                                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        
        pure_text = osi_to_legend_pure(osi)

        assert "order_id: VARCHAR(256) PRIMARY KEY" in pure_text
        assert "line_number: VARCHAR(256) PRIMARY KEY" in pure_text
        assert "amount: VARCHAR(256)" in pure_text

    def test_pure_generates_relation_function_per_dataset(self, complex_osi_model):
        """Test that each dataset relation gets a Relation-returning Pure function."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert (
            "function model::osi::semantic_model::ecommerce::dataset::customers(): Relation<Any>[1]"
            in pure_text
        )
        assert (
            "function model::osi::semantic_model::ecommerce::dataset::orders(): Relation<Any>[1]"
            in pure_text
        )
        assert (
            "function model::osi::semantic_model::ecommerce::dataset::products(): Relation<Any>[1]"
            in pure_text
        )

    def test_pure_relation_function_selects_relation_fields(self, simple_osi_model):
        """Test that the generated dataset function projects the relation fields."""
        pure_text = osi_to_legend_pure(simple_osi_model)

        assert "function model::osi::semantic_model::simple_model::dataset::users(): Relation<Any>[1]" in pure_text
        assert "#>{org::finos::osi::generated::simple_model.public.users}#" in pure_text
        assert "->select(~[user_id, email, created_at])" in pure_text

    def test_pure_generates_relation_function_per_relationship(self, complex_osi_model):
        """Test that each relationship/join gets a Relation-returning Pure function."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert (
            "function model::osi::semantic_model::ecommerce::relationship::orders_to_customers(): Relation<Any>[1]"
            in pure_text
        )
        assert (
            "function model::osi::semantic_model::ecommerce::relationship::orders_to_products(): Relation<Any>[1]"
            in pure_text
        )

    def test_pure_relationship_function_uses_join_expression(self, complex_osi_model):
        """Test that relationship function body uses table refs and join predicate."""
        pure_text = osi_to_legend_pure(complex_osi_model)

        assert "#>{org::finos::osi::generated::ecommerce.public.orders}#" in pure_text
        assert "->join(#>{org::finos::osi::generated::ecommerce.public.customers}#, JoinKind.INNER," in pure_text
        assert "{x,y| $x.customer_id == $y.customer_id}" in pure_text


class TestOntologySupport:
    """Test ontology conversion to Pure classes."""

    def test_ontology_present_in_dict_output(self, ontology_osi_model):
        """Test ontology concepts are included in dict representation when provided."""
        result = osi_to_legend_dict(ontology_osi_model)
        db = result["databases"][0]

        assert "ontology" in db
        assert len(db["ontology"]) == 3

    def test_pure_generates_classes_for_ontology_concepts(self, ontology_osi_model):
        """Test ontology concepts map to Pure class declarations."""
        pure_text = osi_to_legend_pure(ontology_osi_model)

        assert "###Pure" in pure_text
        assert "Class model::osi::semantic_model::hr_model::ontology::Person extends Any" in pure_text
        assert "Class model::osi::semantic_model::hr_model::ontology::Employee extends Person" in pure_text
        assert "Class model::osi::semantic_model::hr_model::ontology::Salary extends Decimal" in pure_text

    def test_relationships_map_to_class_properties(self, ontology_osi_model):
        """Test concept relationships are emitted as class properties."""
        pure_text = osi_to_legend_pure(ontology_osi_model)

        assert "earns: Salary[0..1];" in pure_text
        assert "parent_of: Person[*];" in pure_text
        assert "works_in_Department: Department[*];" in pure_text
        assert "works_in_region: Region[0..1];" in pure_text

    def test_identify_by_marks_key_property(self):
        """Test identify_by adds the equality.Key stereotype to the matching property."""
        osi = {
            "version": "0.1.1",
            "ontology": [
                {
                    "concept": "StoreSales",
                    "identify_by": ["id"],
                    "relationships": [
                        {
                            "name": "id",
                            "roles": [{"player": "StoreSalesNr"}],
                            "verbalizes": [
                                "{StoreSales} is identified by {StoreSalesNr}",
                                "{StoreSalesNr} identifies {StoreSales}",
                            ],
                        }
                    ],
                },
                {"concept": "StoreSalesNr", "extends": ["Integer"]},
            ],
        }

        pure_text = osi_to_legend_pure(osi)

        assert "<<equality.Key>>" in pure_text
        assert "id: StoreSalesNr[*];" in pure_text

    def test_entities_generate_mapping_block(self):
        """Test ontology entities are translated to a FINOS Legend Mapping section."""
        base_dir = Path(__file__).parent
        osi = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "tpcds_retail_model",
                    "description": "TPC-DS retail semantic model for sales and customer analytics",
                    "datasets": [
                        {
                            "name": "store_sales",
                            "source": "tpcds.public.store_sales",
                            "primary_key": ["ss_item_sk", "ss_ticket_number"],
                            "fields": [
                                {"name": "ss_sold_date_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}]}, "dimension": {"is_time": False}},
                                {"name": "ss_item_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_item_sk"}]}, "dimension": {"is_time": False}},
                                {"name": "ss_customer_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_customer_sk"}]}, "dimension": {"is_time": False}},
                                {"name": "ss_store_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_store_sk"}]}, "dimension": {"is_time": False}},
                                {"name": "ss_quantity", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_quantity"}]}},
                                {"name": "ss_sales_price", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sales_price"}]}},
                                {"name": "ss_ext_sales_price", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_ext_sales_price"}]}},
                                {"name": "ss_net_profit", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_net_profit"}]}},
                            ],
                        },
                        {
                            "name": "date_dim",
                            "source": "tpcds.public.date_dim",
                            "primary_key": ["d_date_sk"],
                            "fields": [
                                {"name": "d_date_sk", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "d_date_sk"}]}, "dimension": {"is_time": False}},
                                {"name": "d_date", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "d_date"}]}, "dimension": {"is_time": True}},
                                {"name": "d_year", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "d_year"}]}, "dimension": {"is_time": True}},
                                {"name": "d_quarter_name", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "d_quarter_name"}]}, "dimension": {"is_time": True}},
                                {"name": "d_month_name", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "d_month_name"}]}, "dimension": {"is_time": True}},
                            ],
                        },
                    ],
                    "relationships": [
                        {
                            "name": "store_sales_to_date",
                            "from": "store_sales",
                            "to": "date_dim",
                            "from_columns": ["ss_sold_date_sk"],
                            "to_columns": ["d_date_sk"],
                        }
                    ],
                }
            ],
            "ontology": [
                {
                    "concept": "StoreSales",
                    "identify_by": ["id"],
                    "relationships": [
                        {"name": "id", "roles": [{"player": "StoreSalesNr"}]},
                        {"name": "ticket", "roles": [{"player": "TicketNr"}]},
                    ],
                    "entities": [
                        {
                            "entity": [
                                {"role": "StoreSales.id", "value": "store_sales.ss_item_sk"},
                                {"role": "StoreSales.ticket", "value": "store_sales.ss_sold_date_sk"},
                            ]
                        }
                    ],
                },
                {"concept": "StoreSalesNr", "extends": ["Integer"]},
                {"concept": "TicketNr", "extends": ["Integer"]},
            ],
        }
        with open(base_dir / "example.pure", "r", encoding="utf-8") as f:
            expected = f.read()

        actual = osi_to_legend_pure(osi)

        assert actual == expected

    def test_entities_present_in_dict_output(self):
        """Test generated dict contains ontologyMappings when entities are provided."""
        osi = {
            "version": "0.1.1",
            "ontology": [
                {
                    "concept": "StoreSales",
                    "relationships": [
                        {"name": "id", "roles": [{"player": "StoreSalesNr"}]},
                    ],
                    "entities": [
                        {
                            "entity": [
                                {"role": "StoreSales.id", "value": "store_sales.ss_item_sk"},
                            ]
                        }
                    ],
                },
                {"concept": "StoreSalesNr", "extends": ["Integer"]},
            ],
        }

        result = osi_to_legend_dict(osi)
        db = result["databases"][0]

        assert "ontologyMappings" in db
        assert len(db["ontologyMappings"]) == 1
        assert db["ontologyMappings"][0]["conceptName"] == "StoreSales"
        assert db["ontologyMappings"][0]["sourceDataset"] == "store_sales"
