"""Tests for the OBML → OSI **ontology** converter (OBMLtoOSIOntology).

Validates that the derived ontology document passes the converter's semantic
checks (unique concepts, reference integrity), that OBML join cardinality maps
to OSI multiplicity, and that the documented gaps (many-to-many, composite keys,
missing PK) surface as warnings.
"""

from __future__ import annotations

from typing import Any

import ossie_orionbelt.converter as conv

_OBML: dict[str, Any] = {
    "version": 1.0,
    "description": "Sales model",
    "dataObjects": {
        "Customers": {
            "code": "CUSTOMERS",
            "database": "WH",
            "schema": "PUBLIC",
            "columns": {
                "Customer ID": {
                    "code": "CUSTOMER_ID",
                    "abstractType": "string",
                    "primaryKey": True,
                },
                "Name": {"code": "NAME", "abstractType": "string"},
            },
        },
        "Products": {
            "code": "PRODUCTS",
            "columns": {
                "Product ID": {"code": "PRODUCT_ID", "abstractType": "string", "primaryKey": True},
            },
        },
        "Orders": {
            "code": "ORDERS",
            "columns": {
                "Order ID": {"code": "ORDER_ID", "abstractType": "string", "primaryKey": True},
                "Customer Ref": {"code": "CUSTOMER_ID", "abstractType": "string"},
                "Product Ref": {"code": "PRODUCT_ID", "abstractType": "string"},
            },
            "joins": [
                {
                    "joinType": "many-to-one",
                    "joinTo": "Customers",
                    "columnsFrom": ["Customer Ref"],
                    "columnsTo": ["Customer ID"],
                },
                {
                    "joinType": "many-to-one",
                    "joinTo": "Products",
                    "columnsFrom": ["Product Ref"],
                    "columnsTo": ["Product ID"],
                },
            ],
        },
    },
}


class TestOntologyStructure:
    def test_entities_and_relationships(self) -> None:
        doc = conv.OBMLtoOSIOntology(_OBML, model_name="sales").convert()

        assert doc["version"] == "0.2.0.dev0"
        assert doc["name"] == "sales"

        names = {c["concept"]["name"] for c in doc["ontology"]}
        assert names == {"Customers", "Products", "Orders"}
        assert all(c["concept"]["type"] == "EntityType" for c in doc["ontology"])

        orders = next(c for c in doc["ontology"] if c["concept"]["name"] == "Orders")
        rels = {r["name"]: r for r in orders["relationships"]}
        assert set(rels) == {"Orders_to_Customers", "Orders_to_Products"}
        assert rels["Orders_to_Customers"]["multiplicity"] == "ManyToOne"
        assert rels["Orders_to_Customers"]["roles"] == [{"concept": "Customers"}]
        assert rels["Orders_to_Customers"]["verbalizes"]  # non-empty stub

    def test_embeds_core_semantic_model(self) -> None:
        doc = conv.OBMLtoOSIOntology(_OBML, model_name="sales").convert()
        omap = doc["ontology_mappings"][0]
        assert omap["name"] == "sales_map"
        assert "semantic_model" in omap
        assert omap["semantic_model"]["name"] == "sales"
        assert {d["name"] for d in omap["semantic_model"]["datasets"]} == {
            "Customers",
            "Products",
            "Orders",
        }

    def test_concept_mappings_bind_keys_and_fks(self) -> None:
        doc = conv.OBMLtoOSIOntology(_OBML, model_name="sales").convert()
        cms = {cm["concept"]: cm for cm in doc["ontology_mappings"][0]["concept_mappings"]}

        # Entity identity from primary key.
        assert cms["Customers"]["object_mappings"] == [{"expression": "CUSTOMERS.CUSTOMER_ID"}]

        # Relationship link mapping binds to the FK column in the from-table.
        links = {lm["relationship"]: lm for lm in cms["Orders"]["link_mappings"]}
        assert links["Orders_to_Customers"]["object_mapping"] == {
            "concept": "Customers",
            "expression": "ORDERS.CUSTOMER_ID",
        }

    def test_passes_ontology_semantic_validation(self) -> None:
        doc = conv.OBMLtoOSIOntology(_OBML, model_name="sales").convert()
        result = conv.validate_osi_ontology(doc)
        assert result.valid, result.semantic_errors
        assert not result.semantic_errors

    def test_one_to_one_multiplicity(self) -> None:
        obml = {
            "version": 1.0,
            "dataObjects": {
                "A": {
                    "code": "A",
                    "columns": {"Id": {"code": "ID", "primaryKey": True}},
                    "joins": [
                        {
                            "joinType": "one-to-one",
                            "joinTo": "B",
                            "columnsFrom": ["Id"],
                            "columnsTo": ["Id"],
                        }
                    ],
                },
                "B": {"code": "B", "columns": {"Id": {"code": "ID", "primaryKey": True}}},
            },
        }
        doc = conv.OBMLtoOSIOntology(obml).convert()
        a = next(c for c in doc["ontology"] if c["concept"]["name"] == "A")
        assert a["relationships"][0]["multiplicity"] == "OneToOne"
        assert conv.validate_osi_ontology(doc).valid


class TestOntologyGaps:
    def test_many_to_many_skipped_with_warning(self) -> None:
        obml = {
            "version": 1.0,
            "dataObjects": {
                "A": {
                    "code": "A",
                    "columns": {"Id": {"code": "ID", "primaryKey": True}},
                    "joins": [
                        {
                            "joinType": "many-to-many",
                            "joinTo": "B",
                            "columnsFrom": ["Id"],
                            "columnsTo": ["Id"],
                        }
                    ],
                },
                "B": {"code": "B", "columns": {"Id": {"code": "ID", "primaryKey": True}}},
            },
        }
        c = conv.OBMLtoOSIOntology(obml)
        doc = c.convert()
        a = next(comp for comp in doc["ontology"] if comp["concept"]["name"] == "A")
        assert "relationships" not in a  # m2m join skipped
        assert any("many-to-many" in w for w in c.warnings)
        assert conv.validate_osi_ontology(doc).valid

    def test_missing_primary_key_warns_and_uses_first_column(self) -> None:
        obml = {
            "version": 1.0,
            "dataObjects": {
                "A": {"code": "A", "columns": {"Label": {"code": "LABEL"}}},
            },
        }
        c = conv.OBMLtoOSIOntology(obml)
        doc = c.convert()
        cm = doc["ontology_mappings"][0]["concept_mappings"][0]
        assert cm["object_mappings"] == [{"expression": "A.LABEL"}]
        assert any("no primary key" in w for w in c.warnings)
