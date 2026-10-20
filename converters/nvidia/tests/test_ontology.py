# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Tests for the Ossie ontology and mapping read off an Auto Ontology export."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from ossie_nvidia_auto_ontology import (
    convert_auto_ontology_to_embedded_ossie_ontology,
    convert_auto_ontology_to_ossie,
    convert_auto_ontology_to_ossie_ontology,
)
from ossie_nvidia_auto_ontology.converter import main
from ossie_nvidia_auto_ontology.ontology import build_ontology_documents

FIXTURES = Path(__file__).parent / "fixtures"
REPOSITORY = Path(__file__).resolve().parents[3]
VALIDATOR = REPOSITORY / "validation" / "validate.py"
COLUMN_TYPES = {
    "customer_id": "NUMBER(38,0)",
    "order_id": "NUMBER(38,0)",
    "name": "TEXT",
    "order_date": "DATE",
    "subtotal": "NUMBER(12,2)",
    "discount": "NUMBER(12,2)",
}


def _native(**primary_keys: list[str]) -> dict[str, Any]:
    """The sales fixture with column types, and *primary_keys* by table."""
    native = yaml.safe_load(
        (FIXTURES / "sales.auto_ontology.yaml").read_text(encoding="utf-8")
    )
    for table in native["data_layer"]["databases"][0]["schemas"][0]["tables"]:
        for column in table["columns"]:
            column["type"] = COLUMN_TYPES[column["name"]]
        table["pk"] = primary_keys.get(table["name"], table["pk"])
    return native


def _documents(native: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ontology, mapping = convert_auto_ontology_to_ossie_ontology(yaml.safe_dump(native))
    return yaml.safe_load(ontology), yaml.safe_load(mapping)


def _concept(ontology: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in ontology["ontology"] if item["concept"] == name)


def _relationships(concept: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in concept.get("relationships") or []}


def _concept_mapping(mapping: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (item for item in mapping["concept_mappings"] if item["concept"] == name),
        None,
    )


def test_terms_become_concepts_identified_by_their_key() -> None:
    ontology, _ = _documents(_native())

    customers = _concept(ontology, "Customers")
    relationships = _relationships(customers)

    assert customers["identify_by"] == ["customer_id"]
    assert relationships["customer_id"]["multiplicity"] == "OneToOne"
    assert relationships["customer_id"]["roles"] == [{"concept": "Integer"}]
    assert relationships["customer_name"]["multiplicity"] == "ManyToOne"
    assert relationships["customer_name"]["verbalizes"] == [
        "{Customers} has customer name {String}"
    ]


def test_a_foreign_key_links_two_concepts_instead_of_holding_a_value() -> None:
    ontology, mapping = _documents(_native())

    relationships = _relationships(_concept(ontology, "Orders"))

    assert relationships["customers"] == {
        "name": "customers",
        "roles": [{"concept": "Customers"}],
        "multiplicity": "ManyToOne",
        "verbalizes": ["{Orders} has {Customers}"],
    }
    # The foreign-key column is the link, not a value of the order.
    assert "customer_id" not in relationships
    children = _concept_mapping(mapping, "Orders")["link_mappings"][0]["children"]
    assert {
        "object_mapping": {
            "concept": "Customers",
            "expression": "orders.customer_id",
        },
        "relationship": "customers",
    } in children


def test_a_simple_identifier_maps_objects_with_one_expression() -> None:
    _, mapping = _documents(_native())

    orders = _concept_mapping(mapping, "Orders")

    assert orders["object_mappings"] == [{"expression": "orders.order_id"}]
    assert orders["link_mappings"][0]["object_mapping"] == {
        "expression": "orders.order_id"
    }


def test_a_key_through_a_link_maps_objects_through_the_linked_concept() -> None:
    _, mapping = _documents(_native(orders=["customer_id", "order_id"]))

    assert _concept_mapping(mapping, "Orders")["object_mappings"] == [
        {
            "referent_mappings": [
                {
                    "relationship": "customers",
                    "referent_mappings": [
                        {
                            "relationship": "customer_id",
                            "expression": "orders.customer_id",
                        }
                    ],
                },
                {"relationship": "order_id", "expression": "orders.order_id"},
            ]
        }
    ]


def test_a_concept_without_a_key_is_declared_but_not_mapped() -> None:
    native = _native(orders=[])
    for table in native["data_layer"]["databases"][0]["schemas"][0]["tables"]:
        for column in table["columns"]:
            column["is_unique"] = False

    ontology, mapping = _documents(native)

    assert "identify_by" not in _concept(ontology, "Orders")
    assert _concept_mapping(mapping, "Orders") is None
    assert _concept_mapping(mapping, "Customers") is not None


def test_a_field_without_a_portable_type_is_left_out_of_the_ontology() -> None:
    ontology, _ = _documents(_native())

    # net_total is computed, and Auto Ontology holds no type for it.
    assert "net_total" not in _relationships(_concept(ontology, "Orders"))


@pytest.mark.parametrize(
    ("document", "schema"),
    [
        ("semantic_model", "core-spec/ossie-schema.json"),
        ("ontology", "ontology/ontology.json"),
        ("mapping", "ontology/mapping.json"),
    ],
)
def test_cli_writes_three_documents_that_pass_official_validation(
    tmp_path: Path, document: str, schema: str
) -> None:
    source = tmp_path / "sales.auto_ontology.yaml"
    source.write_text(yaml.safe_dump(_native()), encoding="utf-8")
    paths = {
        name: tmp_path / "out" / f"sales.{name}.yaml"
        for name in ("semantic_model", "ontology", "mapping")
    }
    paths["semantic_model"].parent.mkdir()

    main(
        [
            "import",
            "--input",
            str(source),
            "--output",
            str(paths["semantic_model"]),
            "--ontology-output",
            str(paths["ontology"]),
            "--mapping-output",
            str(paths["mapping"]),
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--schema",
            str(REPOSITORY / schema),
            str(paths[document]),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    mapping = yaml.safe_load(paths["mapping"].read_text(encoding="utf-8"))
    assert mapping["ontology_ref"] == {
        "name": "analytics_ontology",
        "iri": "./sales.ontology.yaml",
    }
    assert mapping["semantic_model_ref"] == {
        "name": "analytics",
        "iri": "./sales.semantic_model.yaml",
    }


def test_cli_requires_the_ontology_and_mapping_outputs_together(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "import",
                "--input",
                str(FIXTURES / "sales.auto_ontology.yaml"),
                "--ontology-output",
                str(tmp_path / "ontology.yaml"),
            ]
        )

    assert raised.value.code == 2


def test_mapping_expressions_quote_only_names_sql_cannot_read_bare() -> None:
    semantic_model = {
        "version": "0.2.0.dev0",
        "name": "shop",
        "datasets": [
            {
                "name": "order lines",
                "source": "shop.public.order_lines",
                "primary_key": ["select"],
                "fields": [
                    {
                        "name": "select",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "ID"}]
                        },
                        "datatype": "Integer",
                    }
                ],
            }
        ],
    }

    _, mapping = build_ontology_documents(semantic_model)

    assert mapping["concept_mappings"][0]["object_mappings"] == [
        {"expression": '"order lines"."select"'}
    ]


def test_keys_naming_fields_or_columns_read_as_the_same_ontology() -> None:
    native = _native()
    customers = next(
        term
        for term in native["semantic_layer"]["terms"]
        if term["name"] == "customers"
    )
    customers["columns_attributes"][0]["name"] = "Customer Number"
    by_field = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    by_column = yaml.safe_load(yaml.safe_dump(by_field))
    for dataset in by_column["datasets"]:
        if dataset["name"] == "customers":
            dataset["primary_key"] = ["customer_id"]
    by_column["relationships"][0]["to_columns"] = ["customer_id"]

    assert build_ontology_documents(by_field) == build_ontology_documents(by_column)


def test_the_embedded_form_carries_the_mapping_and_its_semantic_model() -> None:
    native = yaml.safe_dump(_native())
    ontology, mapping = (
        yaml.safe_load(document)
        for document in convert_auto_ontology_to_ossie_ontology(native)
    )

    embedded = yaml.safe_load(convert_auto_ontology_to_embedded_ossie_ontology(native))

    assert embedded.pop("ontology_mappings") == [
        {
            "name": mapping["name"],
            "description": mapping["description"],
            "semantic_model": yaml.safe_load(convert_auto_ontology_to_ossie(native)),
            "concept_mappings": mapping["concept_mappings"],
        }
    ]
    assert embedded == ontology


def test_cli_writes_an_embedded_ontology_that_passes_official_validation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sales.auto_ontology.yaml"
    source.write_text(yaml.safe_dump(_native()), encoding="utf-8")
    embedded = tmp_path / "sales.ontology.embedded.yaml"

    main(
        [
            "import",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "sales.semantic_model.yaml"),
            "--embedded-ontology-output",
            str(embedded),
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--schema",
            str(REPOSITORY / "ontology" / "ontology.json"),
            str(embedded),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    text = embedded.read_text(encoding="utf-8")
    # Repeated mappings are written out, not as YAML aliases a reader may refuse.
    assert "&id" not in text
    assert yaml.safe_load(text)["ontology_mappings"][0]["concept_mappings"]
