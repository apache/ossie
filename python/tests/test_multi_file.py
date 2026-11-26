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

"""Multi-file models: split model documents, dataset documents, resolve and split."""

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml
from pydantic import ValidationError

import ossie
from ossie import (
    OssieDataset,
    OssieDatasetDocument,
    OssieDatasetReference,
    OssieModelDocument,
    OssieSemanticModel,
)

_REPO = Path(__file__).parents[2]
_CORPUS = _REPO / "validation" / "tests" / "fixtures" / "multi_file"
_IRIS = json.loads((_CORPUS / "iris.json").read_text(encoding="utf-8"))
_CASES = sorted(p.name for p in _CORPUS.iterdir() if (p / "case.json").is_file())
_VERSION = "0.3.0.dev0"

# Shared with validation/tests/test_multi_file.py: one phrase per resolution error category.
_PHRASES = {
    "bad_iri": "is not a file name in the model document's folder",
    "unreadable": "could not be loaded",
    "unparsable": "does not parse as a document",
    "not_dataset_document": "is not a dataset document",
    "dataset_schema": "is not a valid dataset document",
    "version_mismatch": "declares version",
    "name_mismatch": "declares dataset",
    "duplicate_dataset": "duplicate dataset name",
}


def _case(name: str) -> dict:
    return json.loads((_CORPUS / name / "case.json").read_text(encoding="utf-8"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _files(folder: Path) -> dict[str, str]:
    return {p.name: _text(p) for p in folder.iterdir() if p.is_file()}


def _from_memory(folder: Path, model_file: str) -> OssieSemanticModel:
    files = _files(folder)
    document = OssieModelDocument.from_ossie_yaml(files[model_file])
    return document.resolve(lambda iri: files[unquote(iri, errors="strict")], model_label=model_file)


def _from_path(folder: Path, model_file: str) -> OssieSemanticModel:
    return OssieSemanticModel.from_ossie_path(folder / model_file)


_RESOLVERS = [pytest.param(_from_memory, id="resolve"), pytest.param(_from_path, id="from_ossie_path")]


def _assert_reported(message: str, category: str, names: list[str]) -> None:
    assert _PHRASES[category] in message.lower(), message
    for name in names:
        assert name in message, (name, message)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


def test_the_new_types_are_exported() -> None:
    for name in ("OssieDatasetReference", "OssieDatasetDocument", "OssieModelDocument"):
        assert name in ossie.__all__


def test_a_dataset_reference_holds_a_name_and_an_iri() -> None:
    reference = OssieDatasetReference(name="orders", iri="orders.dataset.yaml")

    assert (reference.name, reference.iri) == ("orders", "orders.dataset.yaml")
    with pytest.raises(ValidationError):
        reference.name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "data",
    [
        {"name": "orders"},
        {"iri": "orders.dataset.yaml"},
        {"name": "", "iri": "orders.dataset.yaml"},
        {"name": "orders", "iri": ""},
        {"name": "orders", "iri": "orders.dataset.yaml", "version": _VERSION},
    ],
)
def test_a_dataset_reference_rejects(data: dict) -> None:
    with pytest.raises(ValidationError):
        OssieDatasetReference.model_validate(data)


def test_a_dataset_document_reads_and_writes() -> None:
    text = _text(_CORPUS / "single_ref" / "orders.dataset.yaml")

    document = OssieDatasetDocument.from_ossie_yaml(text)

    assert document.version == _VERSION
    assert isinstance(document.dataset, OssieDataset)
    assert document.dataset.name == "orders"
    assert OssieDatasetDocument.from_ossie_yaml(document.to_ossie_yaml()) == document
    assert OssieDatasetDocument.from_ossie_json(document.to_ossie_json()) == document
    assert list(yaml.safe_load(document.to_ossie_yaml())) == ["version", "dataset"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("version"),
        lambda d: d.pop("dataset"),
        lambda d: d.update(name="orders"),
        lambda d: d["dataset"].pop("source"),
    ],
)
def test_a_dataset_document_rejects(mutate: Callable[[dict], object]) -> None:
    data = yaml.safe_load(_text(_CORPUS / "single_ref" / "orders.dataset.yaml"))
    mutate(data)

    with pytest.raises(ValueError):
        OssieDatasetDocument.from_ossie_yaml(yaml.safe_dump(data))


def test_a_model_document_reads_and_writes() -> None:
    document = OssieModelDocument.from_ossie_yaml(_text(_CORPUS / "refs_only" / "model.yaml"))

    assert document.datasets is None
    assert [(r.name, r.iri) for r in document.dataset_refs] == [
        ("orders", "orders.dataset.yaml"),
        ("customers", "customers.dataset.yaml"),
    ]
    assert document.relationships is not None and document.metrics is not None
    assert OssieModelDocument.from_ossie_yaml(document.to_ossie_yaml()) == document


def test_a_model_document_may_hold_inline_datasets() -> None:
    document = OssieModelDocument.from_ossie_yaml(_text(_CORPUS / "inline_and_refs" / "model.yaml"))

    assert document.datasets is not None
    assert [d.name for d in document.datasets] == ["customers"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("dataset_refs"),
        lambda d: d.pop("version"),
        lambda d: d.pop("name"),
        lambda d: d.update(dataset_refs=[]),
        lambda d: d.update(datasets=[]),
        lambda d: d.update(owner="finance"),
        lambda d: d["dataset_refs"][0].pop("iri"),
    ],
)
def test_a_model_document_rejects(mutate: Callable[[dict], object]) -> None:
    data = yaml.safe_load(_text(_CORPUS / "refs_only" / "model.yaml"))
    mutate(data)

    with pytest.raises(ValueError):
        OssieModelDocument.from_ossie_yaml(yaml.safe_dump(data))


def test_a_semantic_model_still_rejects_dataset_refs() -> None:
    data = yaml.safe_load(_text(_CORPUS / "refs_only" / "resolved.yaml"))
    data["dataset_refs"] = [{"name": "orders", "iri": "orders.dataset.yaml"}]

    with pytest.raises(ValueError):
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(data))


# ---------------------------------------------------------------------------
# The shared corpus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resolver", _RESOLVERS)
@pytest.mark.parametrize("name", [n for n in _CASES if _case(n)["valid"] and "resolved" in _case(n)])
def test_a_valid_case_resolves_to_its_resolved_document(name: str, resolver) -> None:
    case = _case(name)
    expected = OssieSemanticModel.from_ossie_yaml(_text(_CORPUS / name / case["resolved"]))

    resolved = resolver(_CORPUS / name, case["model"])

    assert isinstance(resolved, OssieSemanticModel)
    assert resolved == expected


@pytest.mark.parametrize("resolver", _RESOLVERS)
@pytest.mark.parametrize("name", [n for n in _CASES if "semantic" in _case(n)])
def test_semantic_errors_are_left_to_the_validator(name: str, resolver) -> None:
    assert isinstance(resolver(_CORPUS / name, _case(name)["model"]), OssieSemanticModel)


@pytest.mark.parametrize("resolver", _RESOLVERS)
@pytest.mark.parametrize("name", [n for n in _CASES if "errors" in _case(n)])
def test_an_error_case_raises_naming_the_documents_involved(name: str, resolver) -> None:
    case = _case(name)

    with pytest.raises(ValueError) as raised:
        resolver(_CORPUS / name, case["model"])

    message = str(raised.value)
    for error in case["errors"]:
        # A model document the schema rejects fails as it is read, before resolution.
        if error["category"] != "model_schema":
            _assert_reported(message, error["category"], error["names"])


@pytest.mark.parametrize("resolver", _RESOLVERS)
@pytest.mark.parametrize("name", ["version_mismatch", "version_mismatch_and_schema_error"])
def test_a_version_mismatch_is_reported_once(name: str, resolver) -> None:
    with pytest.raises(ValueError) as raised:
        resolver(_CORPUS / name, "model.yaml")

    message = str(raised.value)
    assert message.count("0.2.0") == 1
    _assert_reported(message, "version_mismatch", ["model.yaml", "orders.dataset.yaml", "0.2.0", _VERSION])
    if name == "version_mismatch_and_schema_error":
        _assert_reported(message, "dataset_schema", ["orders.dataset.yaml", "source"])


def test_resolve_names_the_model_by_its_default_label() -> None:
    files = _files(_CORPUS / "name_mismatch")
    document = OssieModelDocument.from_ossie_yaml(files["model.yaml"])

    with pytest.raises(ValueError, match="<model document>"):
        document.resolve(files.__getitem__)


def test_resolve_wraps_what_load_raises() -> None:
    document = OssieModelDocument.from_ossie_yaml(_text(_CORPUS / "single_ref" / "model.yaml"))

    def load(iri: str) -> str:
        raise RuntimeError("storage offline")

    with pytest.raises(ValueError) as raised:
        document.resolve(load, model_label="model.yaml")

    _assert_reported(str(raised.value), "unreadable", ["model.yaml", "orders.dataset.yaml", "storage offline"])


def test_resolve_passes_the_iri_as_written_to_load() -> None:
    document = OssieModelDocument.from_ossie_yaml(_text(_CORPUS / "percent_encoded_space" / "model.yaml"))
    text = _text(_CORPUS / "percent_encoded_space" / "order lines.dataset.yaml")
    seen = []

    def load(iri: str) -> str:
        seen.append(iri)
        return text

    document.resolve(load)

    assert seen == ["order%20lines.dataset.yaml"]


# ---------------------------------------------------------------------------
# The same-folder rule
# ---------------------------------------------------------------------------


def _model_data_with_iri(iri: str) -> dict:
    data = yaml.safe_load(_text(_CORPUS / _IRIS["base"] / "model.yaml"))
    data["dataset_refs"][0]["iri"] = iri
    return data


@pytest.mark.parametrize("iri", _IRIS["accepted"])
def test_an_iri_naming_a_file_in_the_folder_is_accepted(iri: str, tmp_path: Path) -> None:
    folder = tmp_path / "case"
    shutil.copytree(_CORPUS / _IRIS["base"], folder)
    (folder / "model.yaml").write_text(yaml.safe_dump(_model_data_with_iri(iri)), encoding="utf-8")

    for resolver in (_from_memory, _from_path):
        assert [d.name for d in resolver(folder, "model.yaml").datasets] == ["orders"]


@pytest.mark.parametrize("iri", _IRIS["rejected"])
def test_an_iri_outside_the_same_folder_rule_is_rejected_before_load(iri: str) -> None:
    calls = []

    def load(requested: str) -> str:
        calls.append(requested)
        return _text(_CORPUS / _IRIS["base"] / "orders.dataset.yaml")

    document = OssieModelDocument.model_validate(_model_data_with_iri(iri))

    with pytest.raises(ValueError) as raised:
        document.resolve(load, model_label="model.yaml")

    assert calls == []
    names = ["model.yaml"] if "\\" in iri else ["model.yaml", iri]
    _assert_reported(str(raised.value), "bad_iri", names)


def _symlink(link: Path, target: str) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")


def _linked_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "case"
    shutil.copytree(_CORPUS / _IRIS["base"], folder)
    (folder / "model.yaml").write_text(yaml.safe_dump(_model_data_with_iri("link.dataset.yaml")), encoding="utf-8")
    return folder


def test_a_symlink_to_a_sibling_file_is_followed(tmp_path: Path) -> None:
    folder = _linked_folder(tmp_path)
    _symlink(folder / "link.dataset.yaml", "orders.dataset.yaml")

    assert [d.name for d in _from_path(folder, "model.yaml").datasets] == ["orders"]


def _escaping(folder: Path) -> None:
    outside = folder.parent / "outside"
    outside.mkdir()
    shutil.copy(folder / "orders.dataset.yaml", outside / "orders.dataset.yaml")
    _symlink(folder / "link.dataset.yaml", "../outside/orders.dataset.yaml")


def _dangling(folder: Path) -> None:
    _symlink(folder / "link.dataset.yaml", "nowhere.dataset.yaml")


def _to_directory(folder: Path) -> None:
    (folder / "subdir").mkdir()
    _symlink(folder / "link.dataset.yaml", "subdir")


def _directory(folder: Path) -> None:
    (folder / "link.dataset.yaml").mkdir()


def _not_utf8(folder: Path) -> None:
    (folder / "link.dataset.yaml").write_bytes(b"version: \xff\xfe\n")


def _unreadable(folder: Path) -> None:
    link = folder / "link.dataset.yaml"
    shutil.copy(folder / "orders.dataset.yaml", link)
    link.chmod(0)
    if os.access(link, os.R_OK):
        pytest.skip("file permissions are not enforced here")


@pytest.mark.parametrize("arrange", [_escaping, _dangling, _to_directory, _directory, _not_utf8, _unreadable])
def test_an_entry_that_is_not_a_readable_file_in_the_folder_is_an_error(arrange, tmp_path: Path) -> None:
    folder = _linked_folder(tmp_path)
    arrange(folder)

    with pytest.raises(ValueError) as raised:
        _from_path(folder, "model.yaml")

    _assert_reported(str(raised.value), "unreadable", [str(folder / "model.yaml"), "link.dataset.yaml"])


def test_from_ossie_path_reads_a_plain_document() -> None:
    path = _REPO / "examples" / "tpcds_semantic_model.yaml"

    assert OssieSemanticModel.from_ossie_path(path) == OssieSemanticModel.from_ossie_yaml(_text(path))


# ---------------------------------------------------------------------------
# split, and the round trip
# ---------------------------------------------------------------------------


def _roundtrip(model: OssieSemanticModel) -> OssieSemanticModel:
    document, datasets = model.split()
    texts = {ref.iri: d.to_ossie_yaml() for ref, d in zip(document.dataset_refs, datasets, strict=True)}
    return OssieModelDocument.from_ossie_yaml(document.to_ossie_yaml()).resolve(texts.__getitem__)


def _small_model(*names: str, **extra: object) -> OssieSemanticModel:
    datasets = [{"name": n, "source": f"db.{n.replace(' ', '_')}"} for n in names]
    return OssieSemanticModel.model_validate({"name": "small", "datasets": datasets, **extra})


_EXPRESSION = {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}

_ROUNDTRIP_MODELS = [
    pytest.param(lambda: _small_model("orders"), id="one dataset"),
    pytest.param(
        lambda: _small_model(
            "orders",
            "customers",
            description="d",
            ai_context={"instructions": "i"},
            relationships=[{"name": "r", "from": "orders", "to": "customers", "from_columns": ["c"], "to_columns": ["c"]}],
            metrics=[{"name": "n", "expression": _EXPRESSION}],
            nonportable_extensions=[{"vendor_name": "ACME", "data": {"k": 1}}],
        ),
        id="model-level properties",
    ),
    pytest.param(lambda: _small_model("order lines", "café"), id="names needing percent-encoding"),
    pytest.param(
        lambda: _from_memory(_CORPUS / "inline_and_refs", "model.yaml"),
        id="resolved from inline and refs",
    ),
    pytest.param(
        lambda: OssieSemanticModel.from_ossie_yaml(_text(_REPO / "examples" / "tpcds_semantic_model.yaml")),
        id="tpcds",
    ),
]


@pytest.mark.parametrize("build", _ROUNDTRIP_MODELS)
def test_resolving_a_split_gives_back_the_model(build: Callable[[], OssieSemanticModel]) -> None:
    model = build()

    assert _roundtrip(model) == model


def test_split_moves_every_dataset_into_its_own_document() -> None:
    model = _ROUNDTRIP_MODELS[1].values[0]()

    document, datasets = model.split()

    assert isinstance(document, OssieModelDocument)
    assert document.datasets is None
    assert [r.name for r in document.dataset_refs] == ["orders", "customers"]
    assert all(isinstance(d, OssieDatasetDocument) and d.version == model.version for d in datasets)
    assert [d.dataset for d in datasets] == model.datasets
    kept = ("name", "description", "ai_context", "relationships", "metrics", "nonportable_extensions")
    assert {k: getattr(document, k) for k in kept} == {k: getattr(model, k) for k in kept}


def test_split_names_files_after_their_datasets() -> None:
    document, _ = _small_model("orders", "order lines", "café").split()

    assert [r.iri for r in document.dataset_refs] == [
        "orders.dataset.yaml",
        "order%20lines.dataset.yaml",
        "caf%C3%A9.dataset.yaml",
    ]


def test_split_takes_file_names_from_the_caller() -> None:
    document, _ = _small_model("a/b", "c").split(file_name=lambda d: f"{d.name.replace('/', '_')}.yaml")

    assert [r.iri for r in document.dataset_refs] == ["a_b.yaml", "c.yaml"]


@pytest.mark.parametrize(
    ("names", "file_name"),
    [
        (("a/b",), None),
        (("a\\b",), None),
        (("Orders", "orders"), None),
        (("a", "b"), lambda d: "same.yaml"),
        (("a", "b"), lambda d: f"{d.name}.YAML" if d.name == "a" else "A.yaml"),
        (("a",), lambda d: "sub/a.yaml"),
        (("a",), lambda d: ".."),
    ],
)
def test_split_rejects_file_names_that_cannot_be_written(names: tuple[str, ...], file_name) -> None:
    with pytest.raises(ValueError):
        _small_model(*names).split(file_name=file_name)


# ---------------------------------------------------------------------------
# to_ossie_folder
# ---------------------------------------------------------------------------


def test_to_ossie_folder_writes_files_from_ossie_path_reads_back(tmp_path: Path) -> None:
    model = _small_model("orders", "order lines")

    model.to_ossie_folder(tmp_path, "small.semantic_model.yaml")

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "order lines.dataset.yaml",
        "orders.dataset.yaml",
        "small.semantic_model.yaml",
    ]
    assert OssieSemanticModel.from_ossie_path(tmp_path / "small.semantic_model.yaml") == model


def test_to_ossie_folder_passes_file_name_to_split(tmp_path: Path) -> None:
    _small_model("orders").to_ossie_folder(tmp_path, "m.yaml", file_name=lambda d: f"{d.name}.yml")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.yaml", "orders.yml"]


@pytest.mark.parametrize("model_file_name", ["sub/m.yaml", "..", "", "ORDERS.dataset.yaml"])
def test_to_ossie_folder_rejects_a_bad_model_file_name_and_writes_nothing(model_file_name: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _small_model("orders").to_ossie_folder(tmp_path, model_file_name)

    assert list(tmp_path.iterdir()) == []


def test_to_ossie_folder_writes_nothing_when_split_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _small_model("Orders", "orders").to_ossie_folder(tmp_path, "m.yaml")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("existing", ["orders.dataset.yaml", "m.yaml"])
def test_to_ossie_folder_refuses_an_existing_target_unless_told_to_overwrite(existing: str, tmp_path: Path) -> None:
    (tmp_path / existing).write_text("keep", encoding="utf-8")
    model = _small_model("orders")

    with pytest.raises(FileExistsError):
        model.to_ossie_folder(tmp_path, "m.yaml")

    assert sorted(p.name for p in tmp_path.iterdir()) == [existing]
    assert _text(tmp_path / existing) == "keep"

    model.to_ossie_folder(tmp_path, "m.yaml", overwrite=True)

    assert OssieSemanticModel.from_ossie_path(tmp_path / "m.yaml") == model


# ---------------------------------------------------------------------------
# The split TPC-DS example
# ---------------------------------------------------------------------------


def test_the_split_example_resolves_to_the_single_file_example() -> None:
    split = OssieSemanticModel.from_ossie_path(_REPO / "examples" / "tpcds_split" / "tpcds.semantic_model.yaml")

    assert split == OssieSemanticModel.from_ossie_path(_REPO / "examples" / "tpcds_semantic_model.yaml")
