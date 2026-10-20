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
import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ossie_microsoft import tom


def test_validation_result_reports_and_raises():
    valid = tom.TomValidationResult((), True)
    assert valid.is_valid
    assert valid.raise_for_errors() is None

    invalid = tom.TomValidationResult(
        (tom.TomValidationIssue("missing", "table 'Sales'"),),
        True,
    )
    assert not invalid.is_valid
    with pytest.raises(tom.TomValidationError, match="table 'Sales': missing"):
        invalid.raise_for_errors()


def test_assembly_directory_uses_argument_environment_and_default(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    assert tom._assembly_directory(explicit) == explicit.resolve()

    configured = tmp_path / "configured"
    monkeypatch.setenv("OSSIE_MICROSOFT_TOM_ASSEMBLIES", str(configured))
    assert tom._assembly_directory(None) == configured.resolve()

    monkeypatch.delenv("OSSIE_MICROSOFT_TOM_ASSEMBLIES")
    monkeypatch.chdir(tmp_path)
    assert tom._assembly_directory(None) == tmp_path / ".tom" / "assemblies"


def test_load_tom_explains_missing_extra(tmp_path, monkeypatch):
    tom._load_tom.cache_clear()
    monkeypatch.setitem(sys.modules, "clr_loader", None)
    with pytest.raises(tom.TomUnavailableError, match="optional 'tom' extra"):
        tom._load_tom(tmp_path)


def _fake_pythonnet(monkeypatch, runtime_info=None):
    runtime = object()
    clr_loader = ModuleType("clr_loader")
    clr_loader.get_coreclr = lambda **_kwargs: runtime
    pythonnet = ModuleType("pythonnet")
    pythonnet.get_runtime_info = lambda: runtime_info
    pythonnet.set_runtime = lambda value: setattr(pythonnet, "runtime", value)
    monkeypatch.setitem(sys.modules, "clr_loader", clr_loader)
    monkeypatch.setitem(sys.modules, "pythonnet", pythonnet)
    return pythonnet, runtime


def test_load_tom_explains_missing_assemblies(tmp_path, monkeypatch):
    tom._load_tom.cache_clear()
    _fake_pythonnet(monkeypatch)
    with pytest.raises(tom.TomUnavailableError, match="restore_tom.py"):
        tom._load_tom(tmp_path)


def test_load_tom_initializes_coreclr_and_assemblies(tmp_path, monkeypatch):
    for name in (*tom._ASSEMBLIES, "dotnet.runtimeconfig.json"):
        (tmp_path / name).touch()
    pythonnet, runtime = _fake_pythonnet(monkeypatch)
    clr = ModuleType("clr")
    clr.references = []
    clr.AddReference = clr.references.append
    monkeypatch.setitem(sys.modules, "clr", clr)

    microsoft = ModuleType("Microsoft")
    analysis_services = ModuleType("Microsoft.AnalysisServices")
    tabular = ModuleType("Microsoft.AnalysisServices.Tabular")
    microsoft.AnalysisServices = analysis_services
    analysis_services.Tabular = tabular
    monkeypatch.setitem(sys.modules, "Microsoft", microsoft)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", analysis_services)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tabular)

    tom._load_tom.cache_clear()
    assert tom._load_tom(tmp_path) is tabular
    assert pythonnet.runtime is runtime
    assert clr.references == [os.fspath(tmp_path / name) for name in tom._ASSEMBLIES]
    assert os.fspath(tmp_path) in sys.path


def test_validate_tmsl_handles_success_errors_and_bad_input(tmp_path, monkeypatch):
    class JsonSerializer:
        @staticmethod
        def DeserializeDatabase(raw):  # noqa: N802 - mirrors TOM's managed API
            document = json.loads(raw)
            if document.get("deserialize_error"):
                error = RuntimeError("fallback")
                error.Message = "bad JSON property"
                raise error
            errors = [
                SimpleNamespace(Message=item, Source="model")
                for item in document.get("errors", [])
            ]
            model = SimpleNamespace(Validate=lambda: SimpleNamespace(Errors=errors))
            return SimpleNamespace(Model=model)

    monkeypatch.setattr(
        tom,
        "_load_tom",
        lambda _directory: SimpleNamespace(JsonSerializer=JsonSerializer),
    )

    valid = tom.validate_tmsl({}, assembly_dir=tmp_path)
    assert valid.is_valid
    invalid = tom.validate_tmsl({"errors": ["dangling reference"]}, assembly_dir=tmp_path)
    assert invalid.errors == (tom.TomValidationIssue("dangling reference", "model"),)
    deserialize_error = tom.validate_tmsl({"deserialize_error": True}, assembly_dir=tmp_path)
    assert not deserialize_error.deserialized
    assert deserialize_error.errors[0].message == "DeserializeDatabase: bad JSON property"
    with pytest.raises(TypeError, match="mapping or JSON string"):
        tom.validate_tmsl([], assembly_dir=tmp_path)


def test_serialize_tmdl_returns_a_single_document(tmp_path, monkeypatch):
    database = object()

    class JsonSerializer:
        @staticmethod
        def DeserializeDatabase(raw):  # noqa: N802 - mirrors TOM's managed API
            assert json.loads(raw) == {"name": "Sales"}
            return database

    class TmdlSerializer:
        @staticmethod
        def SerializeDatabase(value):  # noqa: N802
            assert value is database
            return "database Sales\n\n\tmodel Model\n"

    monkeypatch.setattr(
        tom,
        "_load_tom",
        lambda _directory: SimpleNamespace(
            JsonSerializer=JsonSerializer,
            TmdlSerializer=TmdlSerializer,
        ),
    )

    assert tom.serialize_tmdl({"name": "Sales"}, assembly_dir=tmp_path) == (
        "database Sales\n\n\tmodel Model\n"
    )
    with pytest.raises(TypeError, match="mapping or JSON string"):
        tom.serialize_tmdl([], assembly_dir=tmp_path)


def test_split_tmdl_documents_separates_a_nested_model():
    document = (
        "database Sales\r\n"
        "\tcompatibilityLevel: 1567\r\n"
        "\r\n"
        "\t/// a model\r\n"
        "\tmodel Model\r\n"
        "\t\tculture: en-US\r\n"
    )
    assert tom._split_tmdl_documents(document) == [
        ("database.tmdl", "database Sales\n\tcompatibilityLevel: 1567\n"),
        ("model.tmdl", "/// a model\nmodel Model\n\tculture: en-US\n"),
    ]


def test_split_tmdl_documents_separates_a_sibling_model():
    document = (
        "database Sales\n"
        "\tcompatibilityLevel: 1567\n"
        "\n"
        "model Model\n"
        "\tculture: en-US\n"
    )
    assert tom._split_tmdl_documents(document) == [
        ("database.tmdl", "database Sales\n\tcompatibilityLevel: 1567\n"),
        ("model.tmdl", "model Model\n\tculture: en-US\n"),
    ]


def test_split_tmdl_documents_passes_a_model_rooted_document_through():
    document = "model Model\n\tculture: en-US\n"
    assert tom._split_tmdl_documents(document) == [("model.tmdl", document)]


def test_validate_bim_reads_a_bom(tmp_path, monkeypatch):
    model = tmp_path / "model.bim"
    model.write_text('{"name": "Sales"}', encoding="utf-8-sig")
    monkeypatch.setattr(
        tom,
        "validate_tmsl",
        lambda raw, assembly_dir=None: (raw, assembly_dir),
    )
    assert tom.validate_bim(model, assembly_dir=tmp_path) == (
        '{"name": "Sales"}',
        tmp_path,
    )
