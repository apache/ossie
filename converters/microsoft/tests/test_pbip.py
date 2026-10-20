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

import json
from pathlib import Path

import pytest

from ossie_microsoft._common import ConversionError
from ossie_microsoft.cli import main
from ossie_microsoft.pbip import import_powerbi_project, resolve_definition_dir, tmdl_documents
from ossie_microsoft.tom import TomUnavailableError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path) -> Path:
    definition = root / "mini.SemanticModel" / "definition"
    _write(definition / "database.tmdl", "database mini\n\tcompatibilityLevel: 1567\n")
    _write(definition / "model.tmdl", "model Model\n\nref table Orders\n")
    _write(definition / "tables" / "Orders.tmdl", "table Orders\n")
    _write(definition / "cultures" / "ja-JP.tmdl", "cultureInfo ja-JP\n")
    _write(definition / "diagramLayout.json", "{}\n")
    _write(root / "mini.SemanticModel" / ".pbi" / "localSettings.json", "{}\n")
    report = root / "mini.Report"
    _write(
        report / "definition.pbir",
        json.dumps(
            {"datasetReference": {"byPath": {"path": "../mini.SemanticModel"}}}
        ),
    )
    _write(
        root / "mini.pbip",
        json.dumps({"artifacts": [{"report": {"path": "mini.Report"}}]}),
    )
    return root


def test_pbip_resolves_through_the_report_reference(tmp_path):
    root = _project(tmp_path)
    definition = resolve_definition_dir(root / "mini.pbip")
    assert definition == (root / "mini.SemanticModel" / "definition").resolve()


def test_semantic_model_directory_and_definition_directory_resolve(tmp_path):
    root = _project(tmp_path)
    model = root / "mini.SemanticModel"
    definition = model / "definition"
    assert resolve_definition_dir(model) == definition.resolve()
    assert resolve_definition_dir(definition) == definition.resolve()


def test_tmdl_documents_keep_refs_and_skip_non_tmdl(tmp_path):
    root = _project(tmp_path)
    documents = tmdl_documents(resolve_definition_dir(root / "mini.pbip"))
    assert [path for path, _text in documents] == [
        "database.tmdl",
        "model.tmdl",
        "cultures/ja-JP.tmdl",
        "tables/Orders.tmdl",
    ]
    model = dict(documents)["model.tmdl"]
    orders = dict(documents)["tables/Orders.tmdl"]
    assert "ref table Orders" in model
    # Table body stays in its own file; only the ref line mentions the table name.
    assert orders.strip() not in model.replace("ref table Orders", "")


def test_a_remote_pbip_is_rejected(tmp_path):
    report = tmp_path / "mini.Report"
    _write(
        report / "definition.pbir",
        json.dumps(
            {
                "datasetReference": {
                    "byConnection": {"connectionString": "semanticmodelid=abc"},
                },
            }
        ),
    )
    _write(
        tmp_path / "mini.pbip",
        json.dumps({"artifacts": [{"report": {"path": "mini.Report"}}]}),
    )
    with pytest.raises(ConversionError, match="byConnection"):
        resolve_definition_dir(tmp_path / "mini.pbip")


def test_a_hidden_table_file_is_loaded_and_a_hidden_directory_is_not(tmp_path):
    root = _project(tmp_path)
    definition = root / "mini.SemanticModel" / "definition"
    _write(definition / "tables" / ".Hidden.tmdl", "table '.Hidden'\n")
    _write(definition / ".pbi" / "local.tmdl", "table Local\n")
    paths = [path for path, _text in tmdl_documents(definition)]
    assert "tables/.Hidden.tmdl" in paths
    assert ".pbi/local.tmdl" not in paths


def test_a_json_array_pbip_is_rejected(tmp_path, capsys):
    pbip = tmp_path / "mini.pbip"
    pbip.write_text("[]\n", encoding="utf-8")
    assert main(["import", "-i", str(pbip)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Error:")
    assert "JSON object" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("file", "payload", "message"),
    [
        ("mini.pbip", "{", "PBIP JSON file"),
        ("mini.pbip", {}, "no report artifact"),
        ("mini.pbip", {"artifacts": 1}, "artifacts must be an array"),
        ("mini.pbip", {"artifacts": [None, {"report": {"path": 1}}]}, "no report artifact"),
        ("mini.Report/definition.pbir", "{", "is not JSON"),
        ("mini.Report/definition.pbir", [], "report JSON object"),
        ("mini.Report/definition.pbir", {"datasetReference": 1}, "datasetReference object"),
        (
            "mini.Report/definition.pbir",
            {"datasetReference": {"byPath": "../mini.SemanticModel"}},
            "byPath.path string",
        ),
        (
            "mini.Report/definition.pbir",
            {"datasetReference": {"byPath": {"path": ""}}},
            "no datasetReference.byPath",
        ),
        (
            "mini.Report/definition.pbir",
            {"datasetReference": {"byPath": {"path": "../Missing.SemanticModel"}}},
            "byPath does not contain",
        ),
    ],
)
def test_invalid_project_metadata_is_a_cli_error(tmp_path, capsys, file, payload, message):
    root = _project(tmp_path)
    _write(root / file, payload if isinstance(payload, str) else json.dumps(payload))

    assert main(["import", "-i", str(root / "mini.pbip")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Error:")
    assert message in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("missing", ["mini.pbip", "mini.Report/definition.pbir"])
def test_missing_project_inputs_are_cli_errors(tmp_path, capsys, missing):
    root = _project(tmp_path)
    (root / missing).unlink()

    assert main(["import", "--strict", "-i", str(root / "mini.pbip")]) == 1
    assert capsys.readouterr().err.startswith("Error:")


def test_cli_single_tmdl_surfaces_tom_failures(tmp_path, monkeypatch, capsys):
    tmdl = tmp_path / "model.tmdl"
    tmdl.write_text("model Model\n", encoding="utf-8")

    def raise_unavailable(_document, *, assembly_dir=None):
        raise TomUnavailableError("TOM assemblies are unavailable")

    monkeypatch.setattr("ossie_microsoft.tom.deserialize_tmdl", raise_unavailable)
    assert main(["import", "-i", str(tmdl)]) == 1
    unavailable = capsys.readouterr()
    assert unavailable.err.startswith("Error:")
    assert "TOM assemblies are unavailable" in unavailable.err
    assert "Traceback" not in unavailable.err

    class DotNetStyleError(Exception):
        def __init__(self):
            self.Message = "Invalid TMDL near line 3"

    def raise_dotnet(_document, *, assembly_dir=None):
        raise DotNetStyleError()

    monkeypatch.setattr("ossie_microsoft.tom.deserialize_tmdl", raise_dotnet)
    assert main(["import", "-i", str(tmdl)]) == 1
    invalid = capsys.readouterr()
    assert invalid.err.startswith("Error: Invalid TMDL near line 3")
    assert "Traceback" not in invalid.err


def test_a_directory_without_a_semantic_model_is_rejected(tmp_path):
    with pytest.raises(ConversionError, match="database.tmdl"):
        resolve_definition_dir(tmp_path)


def test_import_powerbi_project_hands_documents_to_tom_then_ossie(tmp_path, monkeypatch):
    root = _project(tmp_path)
    seen = {}

    def fake_deserialize(documents, *, assembly_dir=None):
        seen["paths"] = [path for path, _text in documents]
        return {"name": "mini", "model": {"tables": []}}

    def fake_convert(semantic_model):
        seen["model_name"] = semantic_model["name"]
        return "version: 0.2.0.dev0\nname: mini\n"

    monkeypatch.setattr(
        "ossie_microsoft.pbip.deserialize_tmdl_documents", fake_deserialize
    )
    monkeypatch.setattr(
        "ossie_microsoft.pbip.convert_semantic_model_to_ossie", fake_convert
    )

    assert import_powerbi_project(root / "mini.pbip") == "version: 0.2.0.dev0\nname: mini\n"
    assert seen["paths"][0:2] == ["database.tmdl", "model.tmdl"]
    assert seen["model_name"] == "mini"


def test_cli_import_pbip_surfaces_tom_unavailable_without_traceback(
    tmp_path, monkeypatch, capsys
):
    root = _project(tmp_path)
    pbip = root / "mini.pbip"

    def raise_tom_unavailable(_documents, *, assembly_dir=None):
        raise TomUnavailableError("TOM assemblies are unavailable")

    monkeypatch.setattr(
        "ossie_microsoft.pbip.deserialize_tmdl_documents", raise_tom_unavailable
    )
    assert main(["import", "-i", str(pbip)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Error:")
    assert "TOM assemblies are unavailable" in captured.err
    assert captured.out == ""


def test_cli_import_pbip_surfaces_dotnet_message_without_traceback(
    tmp_path, monkeypatch, capsys
):
    root = _project(tmp_path)
    pbip = root / "mini.pbip"

    class DotNetStyleError(Exception):
        def __init__(self):
            self.Message = "Invalid TMDL near line 3"

    def raise_dotnet(_documents, *, assembly_dir=None):
        raise DotNetStyleError()

    monkeypatch.setattr(
        "ossie_microsoft.pbip.deserialize_tmdl_documents", raise_dotnet
    )
    assert main(["import", "-i", str(pbip)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Error: Invalid TMDL near line 3")
    assert captured.out == ""


def test_cli_import_dispatches_a_pbip(tmp_path, monkeypatch, capsys):
    pbip = tmp_path / "mini.pbip"
    pbip.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "ossie_microsoft.cli.import_powerbi_project",
        lambda path: f"from:{Path(path).name}\n",
    )
    assert main(["import", "-i", str(pbip)]) == 0
    assert capsys.readouterr().out == "from:mini.pbip\n"


def test_cli_import_still_reads_model_bim_json(tmp_path, monkeypatch, capsys):
    bim = tmp_path / "model.bim"
    bim.write_text('{"name": "Sales"}\n', encoding="utf-8")

    def fake_convert(semantic_model):
        assert semantic_model == {"name": "Sales"}
        return "name: Sales\n"

    monkeypatch.setattr(
        "ossie_microsoft.cli.convert_semantic_model_to_ossie", fake_convert
    )
    assert main(["import", "-i", str(bim)]) == 0
    assert capsys.readouterr().out == "name: Sales\n"
