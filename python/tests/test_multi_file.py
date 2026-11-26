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
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import ossie
from ossie import (
    OssieDataset,
    OssieDatasetDocument,
    OssieDatasetReference,
    OssieSemanticModel,
    OssieSplitSemanticModel,
)

_REPO = Path(__file__).parents[2]
_VERSION = "0.3.0.dev0"
_FOLDER_RULE = "is not a file name in the model document's folder"


def _orders(name: str = "orders") -> dict:
    return {
        "name": name,
        "source": "shop.public.orders",
        "fields": [{"name": n, "expression": {"sql": n}} for n in ("order_id", "amount")],
    }


def _customers() -> dict:
    return {"name": "customers", "source": "shop.public.customers"}


def _dataset_document(dataset: dict, version: str = _VERSION) -> dict:
    return {"version": version, "dataset": dataset}


def _ref(name: str = "orders", iri: str = "orders.dataset.yaml") -> dict:
    return {"name": name, "iri": iri}


def _split_model(*refs: dict, **properties) -> dict:
    return {"version": _VERSION, "name": "shop", "dataset_refs": list(refs) or [_ref()], **properties}


def _yaml(document: object) -> str:
    return document if isinstance(document, str) else yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _resolve(model: dict, documents: dict | None = None) -> OssieSemanticModel:
    """Resolve model in memory, documents mapping each iri to the document it loads."""
    documents = {"orders.dataset.yaml": _dataset_document(_orders())} if documents is None else documents
    texts = {iri: _yaml(document) for iri, document in documents.items()}
    return OssieSplitSemanticModel.from_ossie_yaml(_yaml(model)).resolve(texts.__getitem__, model_label="model.yaml")


def _write_split(folder: Path, model: dict | None = None, documents: dict | None = None) -> Path:
    """Write a model document and its dataset documents, keyed by file name, and return the model's path."""
    documents = {"orders.dataset.yaml": _dataset_document(_orders())} if documents is None else documents
    for name, document in documents.items():
        if isinstance(document, bytes):
            (folder / name).write_bytes(document)
        else:
            (folder / name).write_text(_yaml(document), encoding="utf-8")
    path = folder / "model.yaml"
    path.write_text(_yaml(_split_model() if model is None else model), encoding="utf-8")
    return path


def _symlink(link: Path, target: str) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


def test_the_new_types_are_exported() -> None:
    assert {"OssieDatasetReference", "OssieDatasetDocument", "OssieSplitSemanticModel"} <= set(ossie.__all__)


def test_a_dataset_reference_is_frozen() -> None:
    reference = OssieDatasetReference(name="orders", iri="orders.dataset.yaml")

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


def test_a_dataset_document_round_trips() -> None:
    text = _yaml(_dataset_document(_orders()))

    document = OssieDatasetDocument.from_ossie_yaml(text)

    assert isinstance(document.dataset, OssieDataset)
    assert yaml.safe_load(document.to_ossie_yaml()) == yaml.safe_load(text)
    assert OssieDatasetDocument.from_ossie_json(document.to_ossie_json()) == document


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"dataset": _orders()}, "missing the required property: version"),
        ({"version": _VERSION}, "dataset"),
        (dict(_dataset_document(_orders()), name="orders"), "name"),
        (_dataset_document(_customers() | {"source": None}), "set to null"),
    ],
)
def test_a_dataset_document_rejects(data: dict, match: str) -> None:
    text = _yaml(data)

    with pytest.raises(ValueError, match=match):
        OssieDatasetDocument.from_ossie_yaml(text)


def test_a_model_document_round_trips() -> None:
    text = _yaml(_split_model(_ref(), _ref("customers", "customers.dataset.yaml"), description="d"))

    document = OssieSplitSemanticModel.from_ossie_yaml(text)

    assert document.datasets is None
    assert document.dataset_refs == [
        OssieDatasetReference(name="orders", iri="orders.dataset.yaml"),
        OssieDatasetReference(name="customers", iri="customers.dataset.yaml"),
    ]
    assert yaml.safe_load(document.to_ossie_yaml()) == yaml.safe_load(text)


def test_a_model_document_may_hold_inline_datasets() -> None:
    document = OssieSplitSemanticModel.from_ossie_yaml(_yaml(_split_model(datasets=[_customers()])))

    assert [d.name for d in document.datasets or []] == ["customers"]


@pytest.mark.parametrize(
    "data",
    [
        {"version": _VERSION, "name": "shop", "datasets": [_customers()]},
        {"version": _VERSION, "dataset_refs": [_ref()]},
        _split_model(dataset_refs=[]),
        _split_model(datasets=[]),
        _split_model(owner="finance"),
        _split_model(dataset=_customers()),
        _split_model({"name": "orders"}),
    ],
)
def test_a_model_document_rejects(data: dict) -> None:
    text = _yaml(data)

    with pytest.raises(ValueError):
        OssieSplitSemanticModel.from_ossie_yaml(text)


def test_a_semantic_model_still_rejects_dataset_refs() -> None:
    data = {"version": _VERSION, "name": "shop", "datasets": [_customers()], "dataset_refs": [_ref()]}

    text = _yaml(data)

    with pytest.raises(ValidationError):
        OssieSemanticModel.from_ossie_yaml(text)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_puts_referenced_datasets_after_the_inline_ones_in_reference_order() -> None:
    model = _split_model(_ref(), _ref("payments", "payments.dataset.yaml"), datasets=[_customers()], description="d")
    documents = {
        "orders.dataset.yaml": _dataset_document(_orders()),
        "payments.dataset.yaml": _dataset_document(_orders("payments")),
    }

    resolved = _resolve(model, documents)

    assert resolved == OssieSemanticModel.model_validate(
        {"name": "shop", "description": "d", "datasets": [_customers(), _orders(), _orders("payments")]}
    )


def test_resolve_reads_a_json_dataset_document() -> None:
    resolved = _resolve(
        _split_model(_ref(iri="orders.dataset.json")),
        {"orders.dataset.json": json.dumps(_dataset_document(_orders()))},
    )

    assert resolved.datasets == [OssieDataset.model_validate(_orders())]


def test_resolve_passes_the_iri_as_written_to_load() -> None:
    document = OssieSplitSemanticModel.from_ossie_yaml(_yaml(_split_model(_ref(iri="order%20lines.dataset.yaml"))))
    seen = []

    def load(iri: str) -> str:
        seen.append(iri)
        return _yaml(_dataset_document(_orders()))

    document.resolve(load)

    assert seen == ["order%20lines.dataset.yaml"]


def test_resolve_wraps_what_load_raises() -> None:
    document = OssieSplitSemanticModel.from_ossie_yaml(_yaml(_split_model()))

    def load(iri: str) -> str:
        raise RuntimeError("storage offline")

    with pytest.raises(ValueError, match=re.escape("iri 'orders.dataset.yaml' could not be loaded: storage offline")):
        document.resolve(load)


def test_resolve_names_the_model_by_a_default_label() -> None:
    document = OssieSplitSemanticModel.from_ossie_yaml(_yaml(_split_model()))

    text = _yaml(_dataset_document(_orders("order_lines")))

    with pytest.raises(ValueError, match=re.escape("<model document>: dataset_refs -> 0 names 'orders'")):
        document.resolve(lambda iri: text)


@pytest.mark.parametrize(
    "iri",
    [
        "sub/orders.dataset.yaml",
        "../orders.dataset.yaml",
        "./orders.dataset.yaml",
        "/orders.dataset.yaml",
        "file:orders.dataset.yaml",
        "https://example.com/orders.dataset.yaml",
        "//example.com/orders.dataset.yaml",
        "orders.dataset.yaml?v=1",
        "orders.dataset.yaml#v1",
        ".",
        "..",
        "%2E",
        "%2E%2E",
        # Malformed percent-encoding, and bytes that are not UTF-8.
        "%",
        "orders%2.dataset.yaml",
        "orders%GG.dataset.yaml",
        "orders%FF.dataset.yaml",
        # Decoded characters a file name in the folder cannot hold.
        "orders%00.dataset.yaml",
        "sub%2Forders.dataset.yaml",
        "sub%5Corders.dataset.yaml",
        "sub\\orders.dataset.yaml",
    ],
)
def test_resolve_rejects_an_iri_outside_the_folder_rule_before_load(iri: str) -> None:
    document = OssieSplitSemanticModel.from_ossie_yaml(_yaml(_split_model(_ref(iri=iri))))
    calls = []

    with pytest.raises(ValueError, match=re.escape(f"model.yaml: dataset_refs -> 0 iri '{iri}' {_FOLDER_RULE}")):
        document.resolve(calls.append, model_label="model.yaml")

    assert calls == []


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("dataset: {name: orders, source: [\n", "iri 'orders.dataset.yaml' is not valid YAML"),
        ("version: 0.3.0.dev0\nversion: 0.3.0.dev0\n", "iri 'orders.dataset.yaml' is not valid YAML"),
        ("- just\n- a list\n", "iri 'orders.dataset.yaml' does not hold a document"),
        ({"version": _VERSION, "name": "orders", "datasets": [_orders()]}, "iri 'orders.dataset.yaml' is not a dataset document"),
        (_dataset_document(_customers() | {"source": 1}), "iri 'orders.dataset.yaml' is not a valid dataset document"),
        (
            _dataset_document(_orders(), "0.2.0"),
            f"iri 'orders.dataset.yaml' declares version '0.2.0' but model.yaml declares version '{_VERSION}'",
        ),
        (
            _dataset_document(_orders("order_lines")),
            "model.yaml: dataset_refs -> 0 names 'orders' but iri 'orders.dataset.yaml' declares dataset 'order_lines'",
        ),
    ],
)
def test_resolve_rejects_a_referenced_document(content: object, match: str) -> None:
    model = _split_model()

    with pytest.raises(ValueError, match=re.escape(match)):
        _resolve(model, {"orders.dataset.yaml": content})


def test_resolve_reports_a_version_mismatch_once_beside_the_other_schema_errors() -> None:
    model = _split_model()
    documents = {"orders.dataset.yaml": _dataset_document(_customers() | {"source": 1}, "0.2.0")}

    with pytest.raises(ValueError) as error:
        _resolve(model, documents)

    assert "is not a valid dataset document" in str(error.value)
    assert str(error.value).count("0.2.0") == 1


def test_resolve_reports_a_references_errors_in_order_schema_version_name() -> None:
    document = _dataset_document(dict(_orders("order_lines"), source=1), "0.2.0")

    model = _split_model()

    with pytest.raises(ValueError) as error:
        _resolve(model, {"orders.dataset.yaml": document})

    message = str(error.value)
    positions = [
        message.find(fragment)
        for fragment in ("is not a valid dataset document", "declares version '0.2.0'", "declares dataset 'order_lines'")
    ]
    assert -1 not in positions
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    ("model", "documents", "names"),
    [
        (
            _split_model(_ref(iri="a.dataset.yaml"), _ref(iri="b.dataset.yaml")),
            {"a.dataset.yaml": _dataset_document(_orders()), "b.dataset.yaml": _dataset_document(_orders())},
            ["a.dataset.yaml", "b.dataset.yaml"],
        ),
        (_split_model(datasets=[_orders()]), None, ["model.yaml", "orders.dataset.yaml"]),
        (_split_model(_ref(), _ref()), None, ["orders.dataset.yaml"]),
    ],
)
def test_resolve_rejects_a_dataset_name_held_twice(model: dict, documents: dict | None, names: list[str]) -> None:
    with pytest.raises(ValueError, match="Duplicate dataset name 'orders'") as error:
        _resolve(model, documents)

    # Every document holding the name, each once.
    assert all(str(error.value).count(name) == 1 for name in names)


def test_resolve_reports_every_reference_error_at_once() -> None:
    model = _split_model(_ref(iri="missing.dataset.yaml"), _ref("customers", "customers.dataset.yaml"))
    documents = {"customers.dataset.yaml": _dataset_document(dict(_customers(), name="clients"))}

    with pytest.raises(ValueError) as error:
        _resolve(model, documents)

    assert "iri 'missing.dataset.yaml' could not be loaded" in str(error.value)
    assert "declares dataset 'clients'" in str(error.value)


def test_resolve_leaves_cross_dataset_checks_to_the_validator() -> None:
    relationship = {"name": "r", "from": "orders", "to": "suppliers", "from_columns": ["a"], "to_columns": ["b"]}

    resolved = _resolve(_split_model(relationships=[relationship]))

    assert resolved.relationships is not None
    assert resolved.relationships[0].to == "suppliers"


# ---------------------------------------------------------------------------
# from_ossie_path
# ---------------------------------------------------------------------------


def test_from_ossie_path_reads_a_plain_document() -> None:
    path = _REPO / "examples" / "tpcds_semantic_model.yaml"

    assert OssieSemanticModel.from_ossie_path(path) == OssieSemanticModel.from_ossie_yaml(path.read_text())


@pytest.mark.parametrize(
    ("iri", "file_name"),
    [
        ("orders.dataset.yaml", "orders.dataset.yaml"),
        ("%6Frders.dataset.yaml", "orders.dataset.yaml"),
        ("order%20lines.dataset.yaml", "order lines.dataset.yaml"),
        ("caf%C3%A9.dataset.yaml", "café.dataset.yaml"),
        # Decoded once: a%2541 names a%41, not aA.
        ("a%2541.dataset.yaml", "a%41.dataset.yaml"),
    ],
)
def test_from_ossie_path_resolves_a_split_model_from_its_folder(tmp_path: Path, iri: str, file_name: str) -> None:
    path = _write_split(tmp_path, _split_model(_ref(iri=iri)), {file_name: _dataset_document(_orders())})

    assert OssieSemanticModel.from_ossie_path(path).datasets == [OssieDataset.model_validate(_orders())]


@pytest.mark.parametrize("iri", ["missing.dataset.yaml", "Orders.dataset.yaml"])
def test_from_ossie_path_matches_directory_entries_exactly(tmp_path: Path, iri: str) -> None:
    path = _write_split(tmp_path, _split_model(_ref(iri=iri)))

    with pytest.raises(ValueError, match=re.escape(f"{path}: dataset_refs -> 0 iri '{iri}' does not resolve: no such file")):
        OssieSemanticModel.from_ossie_path(path)


def test_from_ossie_path_follows_a_symlink_to_a_sibling_file(tmp_path: Path) -> None:
    path = _write_split(tmp_path, _split_model(_ref(iri="link.dataset.yaml")))
    _symlink(tmp_path / "link.dataset.yaml", "orders.dataset.yaml")

    assert [d.name for d in OssieSemanticModel.from_ossie_path(path).datasets] == ["orders"]


def _escaping(folder: Path) -> str:
    outside = folder.parent / "outside"
    outside.mkdir()
    (outside / "orders.dataset.yaml").write_text(_yaml(_dataset_document(_orders())), encoding="utf-8")
    _symlink(folder / "link.dataset.yaml", "../outside/orders.dataset.yaml")
    return "resolves outside the model document's folder"


def _to_a_directory(folder: Path) -> str:
    (folder / "subdir").mkdir()
    _symlink(folder / "link.dataset.yaml", "subdir")
    return "does not name a regular file"


def _a_directory(folder: Path) -> str:
    (folder / "link.dataset.yaml").mkdir()
    return "does not name a regular file"


def _dangling(folder: Path) -> str:
    _symlink(folder / "link.dataset.yaml", "nowhere.dataset.yaml")
    return "does not resolve"


def _unreadable(folder: Path) -> str:
    link = folder / "link.dataset.yaml"
    link.write_text(_yaml(_dataset_document(_orders())), encoding="utf-8")
    link.chmod(0)
    if os.access(link, os.R_OK):
        pytest.skip("file permissions are not enforced here")
    return "does not resolve"


def _not_utf8(folder: Path) -> str:
    (folder / "link.dataset.yaml").write_bytes(b"version: caf\xe9\n")
    return "is not valid UTF-8 text"


@pytest.mark.parametrize("arrange", [_escaping, _to_a_directory, _a_directory, _dangling, _unreadable, _not_utf8])
def test_from_ossie_path_rejects_an_entry_that_is_not_a_readable_file_in_the_folder(tmp_path: Path, arrange) -> None:
    folder = tmp_path / "model"
    folder.mkdir()
    path = _write_split(folder, _split_model(_ref(iri="link.dataset.yaml")), {})
    expected = arrange(folder)

    with pytest.raises(ValueError, match=re.escape(f"{path}: dataset_refs -> 0 iri 'link.dataset.yaml' {expected}")):
        OssieSemanticModel.from_ossie_path(path)


# ---------------------------------------------------------------------------
# split, and the round trip
# ---------------------------------------------------------------------------


def _small_model(*names: str, **properties: object) -> OssieSemanticModel:
    datasets = [{"name": name, "source": f"db.t{i}"} for i, name in enumerate(names)]
    return OssieSemanticModel.model_validate({"name": "small", "datasets": datasets, **properties})


def _roundtrip(model: OssieSemanticModel) -> OssieSemanticModel:
    document, datasets = model.split()
    texts = {ref.iri: d.to_ossie_yaml() for ref, d in zip(document.dataset_refs, datasets, strict=True)}
    return OssieSplitSemanticModel.from_ossie_yaml(document.to_ossie_yaml()).resolve(texts.__getitem__)


@pytest.mark.parametrize(
    "model",
    [
        _small_model("orders"),
        _small_model(
            "orders",
            "customers",
            description="d",
            ai_context={"instructions": "i"},
            relationships=[{"name": "r", "from": "orders", "to": "customers", "from_columns": ["c"], "to_columns": ["c"]}],
            metrics=[{"name": "n", "expression": {"sql": "COUNT(*)"}}],
            nonportable_extensions=[{"vendor_name": "ACME", "data": {"k": 1}}],
        ),
        _small_model("order lines", "café"),
    ],
)
def test_resolving_a_split_gives_back_the_model(model: OssieSemanticModel) -> None:
    assert _roundtrip(model) == model


def test_resolving_a_split_of_a_resolved_model_gives_it_back() -> None:
    resolved = _resolve(_split_model(datasets=[_customers()]))

    assert _roundtrip(resolved) == resolved


def test_resolving_a_split_of_the_document_fixture_gives_it_back(document_data: dict) -> None:
    model = OssieSemanticModel.model_validate(document_data)

    assert _roundtrip(model) == model


def test_resolving_a_split_of_the_tpcds_example_gives_it_back() -> None:
    model = OssieSemanticModel.from_ossie_yaml((_REPO / "examples" / "tpcds_semantic_model.yaml").read_text())

    assert _roundtrip(model) == model


def test_split_moves_every_dataset_into_its_own_document() -> None:
    model = _small_model("orders", "customers", description="d", metrics=[{"name": "n", "expression": {"sql": "1"}}])

    document, datasets = model.split()

    assert document == OssieSplitSemanticModel.model_validate(
        {
            "name": "small",
            "description": "d",
            "dataset_refs": [_ref(), _ref("customers", "customers.dataset.yaml")],
            "metrics": [{"name": "n", "expression": {"sql": "1"}}],
        }
    )
    assert datasets == [OssieDatasetDocument(dataset=dataset) for dataset in model.datasets]


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


def test_split_percent_encodes_the_callers_file_names(tmp_path: Path) -> None:
    model = _small_model("a", "b")
    names = {"a": "order lines.yaml", "b": "café.yaml"}

    document, _ = model.split(file_name=lambda d: names[d.name])
    model.to_ossie_folder(tmp_path, "m.yaml", file_name=lambda d: names[d.name])

    assert [r.iri for r in document.dataset_refs] == ["order%20lines.yaml", "caf%C3%A9.yaml"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["café.yaml", "m.yaml", "order lines.yaml"]


@pytest.mark.parametrize(
    ("names", "file_name"),
    [
        (("a/b",), None),
        (("a\\b",), None),
        # Case-insensitive file systems would write one file for both.
        (("Orders", "orders"), None),
        (("a", "b"), lambda d: "same.yaml"),
        (("a", "b"), lambda d: "a.YAML" if d.name == "a" else "A.yaml"),
        (("a",), lambda d: "sub/a.yaml"),
        (("a",), lambda d: ".."),
    ],
)
def test_split_rejects_file_names_that_cannot_be_written(names: tuple[str, ...], file_name) -> None:
    model = _small_model(*names)

    with pytest.raises(ValueError):
        model.split(file_name=file_name)


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
def test_to_ossie_folder_rejects_a_bad_model_file_name_and_writes_nothing(tmp_path: Path, model_file_name: str) -> None:
    model = _small_model("orders")

    with pytest.raises(ValueError):
        model.to_ossie_folder(tmp_path, model_file_name)

    assert list(tmp_path.iterdir()) == []


def test_to_ossie_folder_writes_nothing_when_split_fails(tmp_path: Path) -> None:
    model = _small_model("Orders", "orders")

    with pytest.raises(ValueError):
        model.to_ossie_folder(tmp_path, "m.yaml")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("existing", ["orders.dataset.yaml", "m.yaml"])
def test_to_ossie_folder_refuses_an_existing_target_unless_told_to_overwrite(tmp_path: Path, existing: str) -> None:
    (tmp_path / existing).write_text("keep", encoding="utf-8")
    model = _small_model("orders")

    with pytest.raises(FileExistsError):
        model.to_ossie_folder(tmp_path, "m.yaml")

    assert [p.name for p in tmp_path.iterdir()] == [existing]
    assert (tmp_path / existing).read_text(encoding="utf-8") == "keep"

    model.to_ossie_folder(tmp_path, "m.yaml", overwrite=True)

    assert OssieSemanticModel.from_ossie_path(tmp_path / "m.yaml") == model


# ---------------------------------------------------------------------------
# The split TPC-DS example
# ---------------------------------------------------------------------------


def test_the_split_example_resolves_to_the_single_file_example() -> None:
    split = OssieSemanticModel.from_ossie_path(_REPO / "examples" / "tpcds_split" / "tpcds.semantic_model.yaml")

    assert split == OssieSemanticModel.from_ossie_path(_REPO / "examples" / "tpcds_semantic_model.yaml")
