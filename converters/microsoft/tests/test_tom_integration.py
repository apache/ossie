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

import copy
import json
import os
from pathlib import Path

import pytest
import yaml

pytest.importorskip("pythonnet", reason="optional TOM extra is not installed")
pytest.importorskip("clr_loader", reason="optional TOM extra is not installed")

from ossie_microsoft import (  # noqa: E402
    convert_ossie_to_semantic_model,
    convert_semantic_model_to_ossie,
    validate_bim,
    validate_tmsl,
)

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "sales_model.bim"
ASSEMBLIES = Path(
    os.environ.get(
        "OSSIE_MICROSOFT_TOM_ASSEMBLIES",
        Path.cwd() / ".tom" / "assemblies",
    )
)
if not (ASSEMBLIES / "Microsoft.AnalysisServices.Tabular.dll").is_file():
    pytest.skip("optional TOM assemblies have not been restored", allow_module_level=True)


def test_sales_fixture_and_round_trip_are_tom_clean():
    original = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
    assert validate_tmsl(original, assembly_dir=ASSEMBLIES).is_valid

    ossie = convert_semantic_model_to_ossie(original)
    exported = convert_ossie_to_semantic_model(ossie)
    assert validate_tmsl(exported, assembly_dir=ASSEMBLIES).is_valid


def test_tpcds_export_is_tom_clean():
    example = HERE.parents[2] / "examples" / "tpcds_semantic_model.yaml"
    exported = convert_ossie_to_semantic_model(example.read_text(encoding="utf-8"))
    assert validate_tmsl(exported, assembly_dir=ASSEMBLIES).is_valid


def test_tmdl_export_is_a_single_document(monkeypatch):
    example = HERE.parents[2] / "examples" / "tpcds_semantic_model.yaml"
    monkeypatch.setenv("OSSIE_MICROSOFT_TOM_ASSEMBLIES", os.fspath(ASSEMBLIES))

    tmdl = convert_ossie_to_semantic_model(
        example.read_text(encoding="utf-8"), output_format="TMDL"
    )

    assert isinstance(tmdl, str)
    assert tmdl.startswith("database ")
    assert "model Model" in tmdl
    assert "table store_sales" in tmdl


def test_a_tmdl_document_imports_back_to_an_equivalent_ossie_model(monkeypatch):
    monkeypatch.setenv("OSSIE_MICROSOFT_TOM_ASSEMBLIES", os.fspath(ASSEMBLIES))
    original = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))

    from_tmsl = yaml.safe_load(convert_semantic_model_to_ossie(original))
    tmdl = convert_ossie_to_semantic_model(
        yaml.safe_dump(from_tmsl), output_format="TMDL"
    )
    from_tmdl = yaml.safe_load(convert_semantic_model_to_ossie(tmdl))

    expected = from_tmsl["semantic_model"][0]
    received = from_tmdl["semantic_model"][0]
    assert received["name"] == expected["name"]
    assert received["description"] == expected["description"]
    assert [d["name"] for d in received["datasets"]] == [
        d["name"] for d in expected["datasets"]
    ]
    assert [m["name"] for m in received["metrics"]] == [
        m["name"] for m in expected["metrics"]
    ]
    assert [r["name"] for r in received["relationships"]] == [
        r["name"] for r in expected["relationships"]
    ]


def test_tom_validation_catches_a_dangling_hierarchy_reference(tmp_path):
    model = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
    broken = copy.deepcopy(model)
    local_date_table = next(
        table
        for table in broken["model"]["tables"]
        if table["name"] == "LocalDateTable_9f2a1b3c"
    )
    local_date_table.pop("hierarchies")
    path = tmp_path / "broken.bim"
    path.write_text(json.dumps(broken), encoding="utf-8")

    result = validate_bim(path, assembly_dir=ASSEMBLIES)
    assert not result.is_valid
    assert any("DefaultHierarchy" in error.message for error in result.errors)
    with pytest.raises(ValueError, match="DefaultHierarchy"):
        result.raise_for_errors()
