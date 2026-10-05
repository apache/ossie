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

"""Tests for the spec DTOs in ossie_ontology.spec."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ossie_ontology.spec import DatasetField, Metric, OssieSpec, SemanticModel

# tests/ -> ontology -> converters -> <repo root>
_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"


def test_dataset_field_accepts_datatype():
    field = DatasetField.model_validate({
        "name": "ss_quantity",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_quantity"}]},
        "datatype": "Integer",
    })
    assert field.datatype == "Integer"


def test_metric_accepts_datatype():
    metric = Metric.model_validate({
        "name": "total_sales",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(ss_sales_price)"}]},
        "datatype": "Decimal",
    })
    assert metric.datatype == "Decimal"


def test_tpcds_example_parses():
    example_path = _EXAMPLES_DIR / "tpcds_semantic_model.yaml"
    if not example_path.is_file():
        pytest.skip(f"canonical example not present at {example_path}")

    doc = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    # A standalone core document carries `version` at the root alongside the
    # semantic model contents. Keep it once SemanticModel declares it (#441).
    if "version" not in SemanticModel.model_fields:
        doc.pop("version", None)
    model = SemanticModel.model_validate(doc)

    assert model.name == "tpcds_retail_model"
    assert any(f.datatype for d in model.datasets for f in d.fields)
    assert any(m.datatype for m in model.metrics)


def test_foaf_example_keeps_custom_properties():
    """`custom_properties` carries imported data the spec does not model, so it
    must parse at every level that declares it and survive a dump unchanged."""
    example_path = _EXAMPLES_DIR / "foaf_owl_import.yaml"
    if not example_path.is_file():
        pytest.skip(f"canonical example not present at {example_path}")

    doc = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    spec = OssieSpec.model_validate(doc)

    assert spec.custom_properties == {"dc:title": "Friend of a Friend (FOAF) vocabulary"}
    person = spec.ontology[0]
    assert person.custom_properties["owl:equivalentClass"] == "http://schema.org/Person"
    assert person.relationships[0].custom_properties["rdfs:subPropertyOf"] == "rdfs:label"

    dumped = OssieSpec.load_yaml(spec.dump_yaml()).dump_dict()
    assert dumped["custom_properties"] == doc["custom_properties"]
    assert dumped["ontology"][0]["custom_properties"] == doc["ontology"][0]["custom_properties"]
    assert dumped["ontology"][0]["relationships"][1]["custom_properties"] == (
        doc["ontology"][0]["relationships"][1]["custom_properties"]
    )


def test_role_accepts_custom_properties():
    relationship = OssieSpec.model_validate({
        "name": "test",
        "ontology": [{
            "concept": "Person",
            "type": "EntityType",
            "relationships": [{
                "name": "knows",
                "roles": [{"concept": "Person", "name": "acquaintance", "custom_properties": {"source": "foaf"}}],
            }],
        }],
    }).ontology[0].relationships[0]
    assert relationship.roles[0].custom_properties == {"source": "foaf"}
