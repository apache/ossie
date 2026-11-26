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

"""Multi-file models in validate.py: a split model document resolved from its dataset documents."""

import json
import os
import shutil
import sys
import types
from importlib.metadata import EntryPoint
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate_multi_file", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

_REPO = Path(__file__).parents[2]
_CORPUS = Path(__file__).parent / "fixtures" / "multi_file"
_IRIS = json.loads((_CORPUS / "iris.json").read_text(encoding="utf-8"))
_CASES = sorted(p.name for p in _CORPUS.iterdir() if (p / "case.json").is_file())
_VERSION = "0.3.0.dev0"

# Shared with python/tests/test_multi_file.py: one phrase per resolution error category.
_PHRASES = {
    "bad_iri": "is not a file name in the model document's folder",
    "unreadable": "could not be loaded",
    "unparsable": "does not parse as a document",
    "not_dataset_document": "is not a dataset document",
    "version_mismatch": "declares version",
    "name_mismatch": "declares dataset",
    "duplicate_dataset": "duplicate dataset name",
}


def _case(name: str) -> dict:
    return json.loads((_CORPUS / name / "case.json").read_text(encoding="utf-8"))


def _copy_case(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy a corpus case to tmp_path/case and run from tmp_path, so diagnostics carry short relative paths."""
    shutil.copytree(_CORPUS / name, tmp_path / "case", symlinks=True)
    monkeypatch.chdir(tmp_path)
    return Path("case")


def _run_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["validate.py", *argv])
    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()
    return raised.value.code, capsys.readouterr().out


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines()]


def _error_lines(output: str) -> list[str]:
    return [line for line in _lines(output) if line.startswith("[") and "Warning" not in line]


def _matches(line: str, category: str, names: list[str]) -> bool:
    if category in ("dataset_schema", "model_schema"):
        found = line.startswith("[Schema]")
    else:
        found = _PHRASES[category] in line.lower()
    return found and all(name in line for name in names)


def _assert_reported(output: str, category: str, names: list[str]) -> None:
    assert any(_matches(line, category, names) for line in _lines(output)), (
        f"no {category} diagnostic naming {names} in:\n{output}"
    )


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve(path: Path) -> tuple[Any, list[str]]:
    return _VALIDATE.resolve_split_model(_load(path), path)


# ---------------------------------------------------------------------------
# The shared corpus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _CASES)
def test_corpus_case(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    case = _case(name)
    folder = _copy_case(name, tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(folder / case["model"]))

    assert code == (0 if case["valid"] else 1), output
    if case["valid"]:
        assert "Validation PASSED" in output
    for error in case.get("errors", []):
        _assert_reported(output, error["category"], error["names"])
    if "semantic" in case:
        assert case["semantic"] in output


@pytest.mark.parametrize("name", [n for n in _CASES if "resolved" in _case(n)])
def test_resolution_gives_the_corpus_resolved_document(name: str) -> None:
    case = _case(name)
    expected = _load(_CORPUS / name / case["resolved"])

    resolved, errors = _resolve(_CORPUS / name / case["model"])

    assert errors == []
    assert resolved == expected
    assert list(resolved) == list(expected)


@pytest.mark.parametrize("name", [n for n in _CASES if "errors" in _case(n)])
def test_a_resolution_error_returns_no_document(name: str) -> None:
    case = _case(name)

    resolved, errors = _resolve(_CORPUS / name / case["model"])

    assert resolved is None
    assert errors


def test_every_resolution_error_is_reported(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("multiple_errors", tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(folder / "model.yaml"))

    assert code == 1
    assert "Validation FAILED with 2 error(s)" in output
    assert "[Schema]" not in output
    for error in _case("multiple_errors")["errors"]:
        _assert_reported(output, error["category"], error["names"])


@pytest.mark.parametrize("name", ["version_mismatch", "version_mismatch_and_schema_error"])
def test_a_version_mismatch_is_reported_once(name: str, tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case(name, tmp_path, monkeypatch)

    _, output = _run_main(monkeypatch, capsys, str(folder / "model.yaml"))

    version_lines = [line for line in _error_lines(output) if "version" in line]
    assert len(version_lines) == 1, output
    assert _matches(version_lines[0], "version_mismatch", ["model.yaml", "orders.dataset.yaml", "0.2.0", _VERSION])
    if name == "version_mismatch_and_schema_error":
        _assert_reported(output, "dataset_schema", ["orders.dataset.yaml", "source"])


def test_schema_errors_name_the_file_they_belong_to(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("dataset_schema_error", tmp_path, monkeypatch)

    _, output = _run_main(monkeypatch, capsys, str(folder / "model.yaml"))

    schema_lines = [line for line in _lines(output) if line.startswith("[Schema]")]
    assert schema_lines
    assert all(str(folder / "orders.dataset.yaml") in line for line in schema_lines)


# ---------------------------------------------------------------------------
# The same-folder rule
# ---------------------------------------------------------------------------


def _with_iri(iri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = _copy_case(_IRIS["base"], tmp_path, monkeypatch)
    model = _load(folder / "model.yaml")
    model["dataset_refs"][0]["iri"] = iri
    (folder / "model.yaml").write_text(yaml.safe_dump(model, sort_keys=False), encoding="utf-8")
    return folder / "model.yaml"


@pytest.mark.parametrize("iri", _IRIS["accepted"])
def test_an_iri_naming_a_file_in_the_folder_is_accepted(iri: str, tmp_path, monkeypatch, capsys) -> None:
    model = _with_iri(iri, tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(model))

    assert code == 0, output


@pytest.mark.parametrize("iri", _IRIS["rejected"])
def test_an_iri_outside_the_same_folder_rule_is_rejected(iri: str, tmp_path, monkeypatch, capsys) -> None:
    model = _with_iri(iri, tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(model))

    assert code == 1
    by_rule = [line for line in _lines(output) if _matches(line, "bad_iri", [str(model)])]
    # The split schema may also carry a basic pattern for iri.
    by_schema = [line for line in _lines(output) if line.startswith("[Schema]") and "dataset_refs -> 0 -> iri" in line]
    assert by_rule or by_schema, output
    if by_rule and "\\" not in iri:
        assert any(iri in line for line in by_rule)
    assert "could not be loaded" not in output


def _symlink(link: Path, target: str) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")


def _linked_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _with_iri("link.dataset.yaml", tmp_path, monkeypatch)


def test_a_symlink_to_a_sibling_file_is_followed(tmp_path, monkeypatch, capsys) -> None:
    model = _linked_model(tmp_path, monkeypatch)
    _symlink(model.parent / "link.dataset.yaml", "orders.dataset.yaml")

    code, output = _run_main(monkeypatch, capsys, str(model))

    assert code == 0, output


def _escaping(model: Path) -> None:
    outside = model.parent.parent / "outside"
    outside.mkdir()
    shutil.copy(model.parent / "orders.dataset.yaml", outside / "orders.dataset.yaml")
    _symlink(model.parent / "link.dataset.yaml", "../outside/orders.dataset.yaml")


def _dangling(model: Path) -> None:
    _symlink(model.parent / "link.dataset.yaml", "nowhere.dataset.yaml")


def _to_directory(model: Path) -> None:
    (model.parent / "subdir").mkdir()
    _symlink(model.parent / "link.dataset.yaml", "subdir")


def _directory(model: Path) -> None:
    (model.parent / "link.dataset.yaml").mkdir()


def _not_utf8(model: Path) -> None:
    (model.parent / "link.dataset.yaml").write_bytes(b"version: \xff\xfe\n")


def _unreadable(model: Path) -> None:
    link = model.parent / "link.dataset.yaml"
    shutil.copy(model.parent / "orders.dataset.yaml", link)
    link.chmod(0)
    if os.access(link, os.R_OK):
        pytest.skip("file permissions are not enforced here")


@pytest.mark.parametrize("arrange", [_escaping, _dangling, _to_directory, _directory, _not_utf8, _unreadable])
def test_an_entry_that_is_not_a_readable_file_in_the_folder_is_an_error(arrange, tmp_path, monkeypatch, capsys) -> None:
    model = _linked_model(tmp_path, monkeypatch)
    arrange(model)

    code, output = _run_main(monkeypatch, capsys, str(model))

    assert code == 1
    _assert_reported(output, "unreadable", [str(model), "link.dataset.yaml"])


# ---------------------------------------------------------------------------
# Resolved key order
# ---------------------------------------------------------------------------


def _orders_document() -> dict:
    return _load(_CORPUS / "single_ref" / "orders.dataset.yaml")


def _customers_dataset() -> dict:
    return _load(_CORPUS / "refs_only" / "customers.dataset.yaml")["dataset"]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["version", "name", "dataset_refs", "metrics"], ["version", "name", "datasets", "metrics"]),
        (["dataset_refs", "version", "name"], ["datasets", "version", "name"]),
        (["version", "datasets", "name", "dataset_refs"], ["version", "datasets", "name"]),
        (["version", "name", "dataset_refs", "datasets"], ["version", "name", "datasets"]),
    ],
)
def test_resolved_key_order(keys: list[str], expected: list[str], tmp_path: Path) -> None:
    values = {
        "version": _VERSION,
        "name": "shop",
        "dataset_refs": [{"name": "orders", "iri": "orders.dataset.yaml"}],
        "datasets": [_customers_dataset()],
        "metrics": [{"name": "n", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}],
    }
    model = {key: values[key] for key in keys}
    (tmp_path / "orders.dataset.yaml").write_text(yaml.safe_dump(_orders_document()), encoding="utf-8")

    resolved, errors = _VALIDATE.resolve_split_model(model, tmp_path / "model.yaml")

    assert errors == []
    assert list(resolved) == expected
    inline = [_customers_dataset()] if "datasets" in keys else []
    assert resolved["datasets"] == [*inline, _orders_document()["dataset"]]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_a_dataset_document_on_its_own_fails_with_a_pointer_to_a_model_document(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("single_ref", tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(folder / "orders.dataset.yaml"))

    assert code == 1
    assert "dataset document" in output
    assert "a model document" in output
    assert "dataset_refs" in output
    assert "[Schema]" not in output


def test_a_plain_document_is_not_resolved(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("refs_only", tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, str(folder / "resolved.yaml"))

    assert code == 0, output


def _other_schema(tmp_path: Path) -> Path:
    path = tmp_path / "other-schema.json"
    path.write_text(
        json.dumps(
            {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.com/other.json", "type": "object"}
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("document", ["model.yaml", "orders.dataset.yaml"])
def test_under_another_schema_neither_key_reroutes(document: str, tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("missing_file", tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, "--schema", str(_other_schema(tmp_path)), str(folder / document))

    assert code == 0, output
    assert "could not be loaded" not in output


def test_the_core_schema_named_explicitly_routes(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("missing_file", tmp_path, monkeypatch)

    code, output = _run_main(
        monkeypatch, capsys, "--schema", str(_REPO / "core-spec/ossie-schema.json"), str(folder / "model.yaml")
    )

    assert code == 1
    _assert_reported(output, "unreadable", ["model.yaml", "missing.dataset.yaml"])


def test_a_copy_of_the_core_schema_routes_by_its_id(tmp_path, monkeypatch, capsys) -> None:
    folder = _copy_case("missing_file", tmp_path, monkeypatch)
    copied = tmp_path / "copied-schema.json"
    shutil.copy(_REPO / "core-spec/ossie-schema.json", copied)

    code, output = _run_main(monkeypatch, capsys, "--schema", str(copied), str(folder / "model.yaml"))

    assert code == 1
    _assert_reported(output, "unreadable", ["model.yaml", "missing.dataset.yaml"])


# ---------------------------------------------------------------------------
# --report and --vendor see the resolved document
# ---------------------------------------------------------------------------


def _with_dataset_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = _copy_case("single_ref", tmp_path, monkeypatch)
    document = _load(folder / "orders.dataset.yaml")
    document["dataset"]["nonportable_extensions"] = [{"vendor_name": "ACME", "data": {"tier": 1}}]
    (folder / "orders.dataset.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return folder / "model.yaml"


def test_the_report_counts_a_referenced_datasets_extensions(tmp_path, monkeypatch, capsys) -> None:
    model = _with_dataset_extension(tmp_path, monkeypatch)

    code, output = _run_main(monkeypatch, capsys, "--report", str(model))

    assert code == 0, output
    lines = _lines(output)
    assert "Portability report:" in lines
    assert any(line.startswith("Fields: 3,") for line in lines)
    assert any(line.startswith("nonportable_extensions: 1 entry, ") for line in lines)
    assert "on: dataset 1" in lines
    assert "vendors: ACME 1" in lines


def test_a_vendor_validator_gets_a_referenced_datasets_extensions(tmp_path, monkeypatch, capsys) -> None:
    model = _with_dataset_extension(tmp_path, monkeypatch)
    seen = []

    def check(payload: dict, site: Any) -> list[str]:
        seen.append((payload, site.kind, site.name))
        return []

    module = types.ModuleType("acme_multi_file_validator")
    setattr(module, "check", check)  # noqa: B010
    monkeypatch.setitem(sys.modules, module.__name__, module)
    group = _VALIDATE.VENDOR_VALIDATOR_GROUP
    points = [EntryPoint(name="ACME", value=f"{module.__name__}:check", group=group)]
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda g: points if g == group else [])

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(model))

    assert code == 0, output
    assert seen == [({"tier": 1}, "dataset", "orders")]


# ---------------------------------------------------------------------------
# A mapping document whose semantic_model_ref names a split model document
# ---------------------------------------------------------------------------


def _mapping_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expression: str = "orders.order_id") -> Path:
    folder = _copy_case("single_ref", tmp_path, monkeypatch)
    (folder / "model.yaml").rename(folder / "shop.semantic_model.yaml")
    ontology = {
        "version": _VERSION,
        "name": "shop_ontology",
        "ontology": [{"concept": "Order", "type": "EntityType"}],
    }
    mapping = {
        "version": _VERSION,
        "name": "shop_mapping",
        "ontology_ref": {"name": "shop_ontology", "iri": "./shop.ontology.yaml"},
        "semantic_model_ref": {"name": "shop", "iri": "./shop.semantic_model.yaml"},
        "concept_mappings": [{"concept": "Order", "object_mappings": [{"expression": expression}]}],
    }
    (folder / "shop.ontology.yaml").write_text(yaml.safe_dump(ontology, sort_keys=False), encoding="utf-8")
    (folder / "shop.mapping.yaml").write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return folder


def _validate_mapping(folder: Path, monkeypatch, capsys) -> tuple[int, str]:
    return _run_main(monkeypatch, capsys, "--schema", str(_REPO / "ontology/mapping.json"), str(folder / "shop.mapping.yaml"))


def test_a_mapping_cross_checks_against_a_split_model(tmp_path, monkeypatch, capsys) -> None:
    folder = _mapping_set(tmp_path, monkeypatch)

    code, output = _validate_mapping(folder, monkeypatch, capsys)

    assert code == 0, output
    assert "Warning" not in output


def test_a_mapping_reads_fields_from_a_referenced_dataset(tmp_path, monkeypatch, capsys) -> None:
    folder = _mapping_set(tmp_path, monkeypatch, expression="orders.discount")

    code, output = _validate_mapping(folder, monkeypatch, capsys)

    assert code == 1
    assert "dataset 'orders' declares no field 'discount'" in output


def test_a_split_models_resolution_error_is_labelled_semantic_model_ref(tmp_path, monkeypatch, capsys) -> None:
    folder = _mapping_set(tmp_path, monkeypatch)
    (folder / "orders.dataset.yaml").unlink()

    code, output = _validate_mapping(folder, monkeypatch, capsys)

    assert code == 1
    assert any(
        "semantic_model_ref" in line and _PHRASES["unreadable"] in line and "orders.dataset.yaml" in line
        for line in _lines(output)
    ), output


# ---------------------------------------------------------------------------
# The two new schemas
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-dataset-schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def split_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-split-model-schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def core_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-schema.json").read_text(encoding="utf-8"))


def test_the_new_schemas_are_valid_json_schemas(dataset_schema: dict, split_schema: dict) -> None:
    _VALIDATE.Draft202012Validator.check_schema(dataset_schema)
    _VALIDATE.Draft202012Validator.check_schema(split_schema)


def test_the_dataset_schema_reuses_the_core_dataset_definition(dataset_schema: dict) -> None:
    assert dataset_schema["properties"]["dataset"]["$ref"].endswith("ossie-schema.json#/$defs/Dataset")


def test_the_split_schema_reuses_every_core_root_property(split_schema: dict, core_schema: dict) -> None:
    properties = split_schema["properties"]
    assert set(properties) == set(core_schema["properties"]) | {"dataset_refs"}
    for name in core_schema["properties"]:
        assert "$ref" in properties[name], name


def test_a_dataset_document_validates(dataset_schema: dict) -> None:
    assert _VALIDATE.validate_schema(_orders_document(), dataset_schema) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("version"),
        lambda d: d.pop("dataset"),
        lambda d: d.update(version="0.2.0"),
        lambda d: d.update(name="orders"),
        lambda d: d["dataset"].pop("source"),
        lambda d: d["dataset"].update(relationships=[]),
        lambda d: d.update(semantic_model_ref={"name": "shop", "iri": "model.yaml"}),
    ],
)
def test_the_dataset_schema_rejects(dataset_schema: dict, mutate) -> None:
    document = _orders_document()
    mutate(document)

    assert _VALIDATE.validate_schema(document, dataset_schema)


def _split_model() -> dict:
    return _load(_CORPUS / "refs_only" / "model.yaml")


def test_a_split_model_document_validates(split_schema: dict) -> None:
    assert _VALIDATE.validate_schema(_split_model(), split_schema) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(datasets=[_customers_dataset()]),
        lambda d: d.update(ai_context={"instructions": "x"}, nonportable_extensions=[{"vendor_name": "V", "data": {}}]),
    ],
)
def test_the_split_schema_accepts(split_schema: dict, mutate) -> None:
    document = _split_model()
    mutate(document)

    assert _VALIDATE.validate_schema(document, split_schema) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("dataset_refs"),
        lambda d: d.pop("version"),
        lambda d: d.pop("name"),
        lambda d: d.update(dataset_refs=[]),
        lambda d: d.update(datasets=[]),
        lambda d: d.update(owner="finance"),
        lambda d: d.update(dataset=_customers_dataset()),
        lambda d: d["dataset_refs"][0].pop("iri"),
        lambda d: d["dataset_refs"][0].pop("name"),
        lambda d: d["dataset_refs"][0].update(iri=""),
        lambda d: d["dataset_refs"][0].update(name=""),
        lambda d: d["dataset_refs"][0].update(version=_VERSION),
    ],
)
def test_the_split_schema_rejects(split_schema: dict, mutate) -> None:
    document = _split_model()
    mutate(document)

    assert _VALIDATE.validate_schema(document, split_schema)


def test_the_core_schema_still_rejects_dataset_refs(core_schema: dict) -> None:
    document = _load(_CORPUS / "refs_only" / "resolved.yaml")
    document["dataset_refs"] = [{"name": "orders", "iri": "orders.dataset.yaml"}]

    assert _VALIDATE.validate_schema(document, core_schema)


def test_the_core_schema_still_requires_datasets(core_schema: dict) -> None:
    assert _VALIDATE.validate_schema(_split_model(), core_schema)


# ---------------------------------------------------------------------------
# The split TPC-DS example
# ---------------------------------------------------------------------------

_SPLIT_EXAMPLE = _REPO / "examples/tpcds_split/tpcds.semantic_model.yaml"


def test_the_split_example_resolves_to_the_single_file_example() -> None:
    resolved, errors = _resolve(_SPLIT_EXAMPLE)

    assert errors == []
    assert resolved == _load(_REPO / "examples/tpcds_semantic_model.yaml")


def test_the_split_example_validates(monkeypatch, capsys) -> None:
    code, output = _run_main(monkeypatch, capsys, str(_SPLIT_EXAMPLE))

    assert code == 0, output


@pytest.mark.parametrize("path", ["Makefile", ".github/workflows/validation-ci.yml"])
def test_the_split_example_is_validated_by(path: str) -> None:
    assert "examples/tpcds_split/tpcds.semantic_model.yaml" in (_REPO / path).read_text(encoding="utf-8")
