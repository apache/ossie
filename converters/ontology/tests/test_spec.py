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

from ossie_ontology.spec import DatasetField, Metric, SemanticModel

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
