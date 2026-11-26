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

"""Resolve a split model document from the dataset documents in its folder."""

import json
import os
import shutil
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate_multi_file", _VALIDATE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

_REPO = Path(__file__).parents[2]
_VERSION = "0.3.0.dev0"
_FOLDER_RULE = "is not a file name in the model document's folder"
_NO_SUCH_FILE = "does not resolve: no such file in the model document's folder"


@pytest.fixture
def offline(monkeypatch):
    def reject_request(*args, **kwargs):
        pytest.fail("Schema validation must not make HTTP requests")

    monkeypatch.setattr(urllib.request, "urlopen", reject_request)
    monkeypatch.setattr("jsonschema.validators.urlopen", reject_request, raising=False)


def _expression(sql: str) -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def _orders(name: str = "orders") -> dict:
    return {
        "name": name,
        "source": "shop.public.orders",
        "primary_key": ["order_id"],
        "fields": [{"name": n, "expression": _expression(n)} for n in ("order_id", "customer_id", "amount")],
    }


def _customers() -> dict:
    return {
        "name": "customers",
        "source": "shop.public.customers",
        "primary_key": ["customer_id"],
        "fields": [{"name": n, "expression": _expression(n)} for n in ("customer_id", "region")],
    }


def _dataset_document(dataset: dict, version: str = _VERSION) -> dict:
    return {"version": version, "dataset": dataset}


def _ref(name: str = "orders", iri: str = "orders.dataset.yaml") -> dict:
    return {"name": name, "iri": iri}


def _split_model(*refs: dict, **properties) -> dict:
    return {"version": _VERSION, "name": "shop", "dataset_refs": list(refs) or [_ref()], **properties}


def _write(path: Path, content: object) -> Path:
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    return path


def _write_split(tmp_path: Path, model: dict | None = None, documents: dict | None = None) -> Path:
    """Write a model document and its dataset documents into tmp_path and return the model's path."""
    documents = {"orders.dataset.yaml": _dataset_document(_orders())} if documents is None else documents
    for name, document in documents.items():
        _write(tmp_path / name, document)
    return _write(tmp_path / "model.yaml", _split_model() if model is None else model)


def _resolve(path: Path) -> tuple[Any, list[str]]:
    return _VALIDATE.resolve_split_model(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def _errors(path: Path) -> list[str]:
    resolved, errors = _resolve(path)
    assert resolved is None
    return errors


def _symlink(link: Path, target: str) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_referenced_datasets_follow_the_inline_ones_in_reference_order(tmp_path):
    model = _split_model(_ref(), _ref("payments", "payments.dataset.yaml"), datasets=[_customers()])
    documents = {
        "orders.dataset.yaml": _dataset_document(_orders()),
        "payments.dataset.yaml": _dataset_document(_orders("payments")),
    }

    resolved, errors = _resolve(_write_split(tmp_path, model, documents))

    assert errors == []
    assert resolved == {
        "version": _VERSION,
        "name": "shop",
        "datasets": [_customers(), _orders(), _orders("payments")],
    }


@pytest.mark.parametrize(
    "keys, expected",
    [
        (["version", "name", "dataset_refs", "metrics"], ["version", "name", "datasets", "metrics"]),
        (["dataset_refs", "version", "name"], ["datasets", "version", "name"]),
        # An inline datasets key keeps its place, before or after dataset_refs.
        (["version", "datasets", "name", "dataset_refs"], ["version", "datasets", "name"]),
        (["version", "name", "dataset_refs", "datasets"], ["version", "name", "datasets"]),
    ],
)
def test_resolved_key_order(tmp_path, keys, expected):
    values = {
        "version": _VERSION,
        "name": "shop",
        "dataset_refs": [_ref()],
        "datasets": [_customers()],
        "metrics": [{"name": "n", "expression": _expression("COUNT(*)")}],
    }

    resolved, errors = _resolve(_write_split(tmp_path, {key: values[key] for key in keys}))

    assert errors == []
    assert list(resolved) == expected


def test_a_json_dataset_document_is_read(tmp_path):
    model = _split_model(_ref(iri="orders.dataset.json"))
    documents = {"orders.dataset.json": json.dumps(_dataset_document(_orders()))}

    resolved, errors = _resolve(_write_split(tmp_path, model, documents))

    assert errors == []
    assert resolved["datasets"] == [_orders()]


@pytest.mark.parametrize(
    "iri, file_name",
    [
        ("orders.dataset.yaml", "orders.dataset.yaml"),
        # Percent-encoded unreserved characters decode to themselves.
        ("%6Frders.dataset.yaml", "orders.dataset.yaml"),
        ("orders%2Edataset.yaml", "orders.dataset.yaml"),
        ("order%20lines.dataset.yaml", "order lines.dataset.yaml"),
        ("caf%C3%A9.dataset.yaml", "café.dataset.yaml"),
        # Decoded once: a%2541 names a%41, not aA.
        ("a%2541.dataset.yaml", "a%41.dataset.yaml"),
    ],
)
def test_an_iri_naming_a_file_in_the_folder_is_followed(tmp_path, iri, file_name):
    model = _split_model(_ref(iri=iri))

    resolved, errors = _resolve(_write_split(tmp_path, model, {file_name: _dataset_document(_orders())}))

    assert errors == []
    assert resolved["datasets"] == [_orders()]


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
def test_an_iri_outside_the_folder_rule_is_an_error(tmp_path, iri):
    path = _write_split(tmp_path, _split_model(_ref(iri=iri)))

    assert _errors(path) == [f"[Reference] {path}: dataset_refs -> 0 iri '{iri}' {_FOLDER_RULE}"]


@pytest.mark.parametrize(
    "iri",
    [
        "missing.dataset.yaml",
        # Matched against the directory entries exactly, whatever the file system folds.
        "Orders.dataset.yaml",
    ],
)
def test_an_iri_naming_no_entry_of_the_folder_is_an_error(tmp_path, iri):
    path = _write_split(tmp_path, _split_model(_ref(iri=iri)))

    assert _errors(path) == [f"[Reference] {path}: dataset_refs -> 0 iri '{iri}' {_NO_SUCH_FILE}"]


def test_a_symlink_to_a_sibling_file_is_followed(tmp_path):
    path = _write_split(tmp_path, _split_model(_ref(iri="link.dataset.yaml")))
    _symlink(tmp_path / "link.dataset.yaml", "orders.dataset.yaml")

    resolved, errors = _resolve(path)

    assert errors == []
    assert resolved["datasets"] == [_orders()]


def _escaping(folder: Path) -> str:
    outside = folder.parent / "outside"
    outside.mkdir()
    _write(outside / "orders.dataset.yaml", _dataset_document(_orders()))
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
    with pytest.raises(FileNotFoundError) as missing:
        (folder / "link.dataset.yaml").stat()
    return f"does not resolve: {missing.value.strerror}"


def _unreadable(folder: Path) -> str:
    link = _write(folder / "link.dataset.yaml", _dataset_document(_orders()))
    link.chmod(0)
    try:
        link.read_bytes()
    except PermissionError as denied:
        return f"does not resolve: {denied.strerror}"
    pytest.skip("file permissions are not enforced here")


def _not_utf8(folder: Path) -> str:
    _write(folder / "link.dataset.yaml", b"version: caf\xe9\n")
    return "is not valid UTF-8 text"


@pytest.mark.parametrize("arrange", [_escaping, _to_a_directory, _a_directory, _dangling, _unreadable, _not_utf8])
def test_an_entry_that_is_not_a_readable_file_in_the_folder_is_an_error(tmp_path, arrange):
    folder = tmp_path / "model"
    folder.mkdir()
    path = _write_split(folder, _split_model(_ref(iri="link.dataset.yaml")))
    expected = arrange(folder)

    assert _errors(path) == [f"[Reference] {path}: dataset_refs -> 0 iri 'link.dataset.yaml' {expected}"]


@pytest.mark.parametrize(
    "content, expected",
    [
        ("dataset: {name: orders, source: [\n", "is not valid YAML: "),
        ("version: 0.3.0.dev0\nversion: 0.3.0.dev0\n", "is not valid YAML: "),
        ("- just\n- a list\n", "does not hold a document"),
        # A semantic model is not a dataset document, and neither is a split one.
        ({"version": _VERSION, "name": "orders", "datasets": [_orders()]}, "is not a dataset document"),
        (_split_model(), "is not a dataset document"),
    ],
)
def test_a_referenced_file_that_is_not_a_dataset_document_is_an_error(tmp_path, content, expected):
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": content})

    errors = _errors(path)

    assert len(errors) == 1
    assert errors[0].startswith(f"[Reference] {path}: dataset_refs -> 0 iri 'orders.dataset.yaml' {expected}")
    # A parser error keeps only its first line.
    assert "\n" not in errors[0]


def _without_source() -> dict:
    dataset = _orders()
    del dataset["source"]
    return dataset


def test_a_dataset_documents_schema_errors_name_that_document(tmp_path):
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": _dataset_document(_without_source())})

    assert _errors(path) == [f"[Schema] {tmp_path / 'orders.dataset.yaml'}: dataset: 'source' is a required property"]


def test_a_version_mismatch_is_one_error_naming_both_documents(tmp_path):
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": _dataset_document(_orders(), "0.2.0")})

    assert _errors(path) == [
        f"[Reference] {tmp_path / 'orders.dataset.yaml'} declares version '0.2.0' but {path} declares version '{_VERSION}'"
    ]


def test_a_version_mismatch_is_reported_once_beside_the_other_schema_errors(tmp_path):
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": _dataset_document(_without_source(), "0.2.0")})
    dataset_path = tmp_path / "orders.dataset.yaml"

    assert _errors(path) == [
        f"[Schema] {dataset_path}: dataset: 'source' is a required property",
        f"[Reference] {dataset_path} declares version '0.2.0' but {path} declares version '{_VERSION}'",
    ]


def test_a_reference_must_name_the_dataset_it_resolves_to(tmp_path):
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": _dataset_document(_orders("order_lines"))})

    assert _errors(path) == [
        f"[Reference] {path}: dataset_refs -> 0 names 'orders' but "
        f"{tmp_path / 'orders.dataset.yaml'} declares dataset 'order_lines'"
    ]


def test_a_references_errors_come_in_order_schema_version_name(tmp_path):
    document = _dataset_document(dict(_without_source(), name="order_lines"), "0.2.0")
    path = _write_split(tmp_path, documents={"orders.dataset.yaml": document})
    dataset_path = tmp_path / "orders.dataset.yaml"

    assert _errors(path) == [
        f"[Schema] {dataset_path}: dataset: 'source' is a required property",
        f"[Reference] {dataset_path} declares version '0.2.0' but {path} declares version '{_VERSION}'",
        f"[Reference] {path}: dataset_refs -> 0 names 'orders' but {dataset_path} declares dataset 'order_lines'",
    ]


def test_a_dataset_named_twice_across_references_is_an_error(tmp_path):
    model = _split_model(_ref(iri="a.dataset.yaml"), _ref(iri="b.dataset.yaml"))
    documents = {name: _dataset_document(_orders()) for name in ("a.dataset.yaml", "b.dataset.yaml")}

    path = _write_split(tmp_path, model, documents)

    assert _errors(path) == [
        f"[Unique] Duplicate dataset name 'orders' in {tmp_path / 'a.dataset.yaml'}, {tmp_path / 'b.dataset.yaml'}"
    ]


def test_a_dataset_both_inline_and_referenced_is_an_error(tmp_path):
    path = _write_split(tmp_path, _split_model(datasets=[_orders()]))

    assert _errors(path) == [f"[Unique] Duplicate dataset name 'orders' in {path}, {tmp_path / 'orders.dataset.yaml'}"]


def test_two_references_to_one_file_are_a_duplicate_dataset(tmp_path):
    path = _write_split(tmp_path, _split_model(_ref(), _ref()))

    assert _errors(path) == [f"[Unique] Duplicate dataset name 'orders' in {tmp_path / 'orders.dataset.yaml'}"]


@pytest.mark.parametrize(
    "properties, expected",
    [
        # dataset belongs to a dataset document; a document with dataset_refs is a model document.
        ({"dataset": _customers()}, "(root): Additional properties are not allowed ('dataset' was unexpected)"),
        ({"owner": "finance"}, "(root): Additional properties are not allowed ('owner' was unexpected)"),
        ({"datasets": []}, "datasets: [] should be non-empty"),
    ],
)
def test_a_model_documents_schema_errors_name_it_and_stop_resolution(offline, tmp_path, properties, expected):
    path = _write_split(tmp_path, _split_model(**properties))

    assert _errors(path) == [f"[Schema] {path}: {expected}"]


def test_every_reference_error_is_reported(tmp_path):
    model = _split_model(_ref(iri="missing.dataset.yaml"), _ref("customers", "customers.dataset.yaml"))
    documents = {"customers.dataset.yaml": _dataset_document(dict(_customers(), name="clients"))}

    path = _write_split(tmp_path, model, documents)

    assert _errors(path) == [
        f"[Reference] {path}: dataset_refs -> 0 iri 'missing.dataset.yaml' {_NO_SUCH_FILE}",
        f"[Reference] {path}: dataset_refs -> 1 names 'customers' but "
        f"{tmp_path / 'customers.dataset.yaml'} declares dataset 'clients'",
    ]


# ---------------------------------------------------------------------------
# The two new schemas
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-dataset-schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def split_model_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-split-model-schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def core_schema() -> dict:
    return json.loads((_REPO / "core-spec/ossie-schema.json").read_text(encoding="utf-8"))


def test_the_new_schemas_are_valid_json_schemas(dataset_schema, split_model_schema):
    _VALIDATE.Draft202012Validator.check_schema(dataset_schema)
    _VALIDATE.Draft202012Validator.check_schema(split_model_schema)


def test_the_dataset_schema_reuses_the_core_dataset_definition(dataset_schema):
    assert dataset_schema["properties"]["dataset"]["$ref"].endswith("ossie-schema.json#/$defs/Dataset")


def test_the_split_model_schema_reuses_every_core_root_property(split_model_schema, core_schema):
    properties = split_model_schema["properties"]

    assert set(properties) == set(core_schema["properties"]) | {"dataset_refs"}
    assert all("$ref" in properties[name] for name in core_schema["properties"])


def test_a_dataset_document_validates(offline, dataset_schema):
    assert _VALIDATE.validate_schema(_dataset_document(_orders()), dataset_schema) == []


@pytest.mark.parametrize(
    "document",
    [
        {"dataset": _orders()},
        {"version": _VERSION},
        _dataset_document(_orders(), "0.2.0"),
        dict(_dataset_document(_orders()), name="orders"),
        dict(_dataset_document(_orders()), semantic_model_ref={"name": "shop", "iri": "model.yaml"}),
        _dataset_document(_without_source()),
        _dataset_document(dict(_orders(), relationships=[])),
    ],
)
def test_the_dataset_schema_rejects(offline, dataset_schema, document):
    assert _VALIDATE.validate_schema(document, dataset_schema)


@pytest.mark.parametrize(
    "document",
    [
        _split_model(),
        _split_model(datasets=[_customers()]),
        _split_model(
            description="d",
            ai_context={"instructions": "i"},
            metrics=[{"name": "n", "expression": _expression("COUNT(*)")}],
            nonportable_extensions=[{"vendor_name": "ACME", "data": {}}],
        ),
    ],
)
def test_the_split_model_schema_accepts(offline, split_model_schema, document):
    assert _VALIDATE.validate_schema(document, split_model_schema) == []


@pytest.mark.parametrize(
    "document",
    [
        {"version": _VERSION, "name": "shop", "datasets": [_customers()]},
        {"version": _VERSION, "dataset_refs": [_ref()]},
        {"name": "shop", "dataset_refs": [_ref()]},
        _split_model(dataset_refs=[]),
        _split_model(datasets=[]),
        _split_model(owner="finance"),
        _split_model(dataset=_customers()),
        _split_model({"name": "orders"}),
        _split_model({"iri": "orders.dataset.yaml"}),
        _split_model(_ref(iri="")),
        _split_model(_ref(name="")),
        _split_model(dict(_ref(), version=_VERSION)),
    ],
)
def test_the_split_model_schema_rejects(offline, split_model_schema, document):
    assert _VALIDATE.validate_schema(document, split_model_schema)


def test_the_core_schema_still_rejects_dataset_refs(offline, core_schema):
    document = {"version": _VERSION, "name": "shop", "datasets": [_customers()], "dataset_refs": [_ref()]}

    assert _VALIDATE.validate_schema(document, core_schema) == [
        "[Schema] (root): Additional properties are not allowed ('dataset_refs' was unexpected)"
    ]


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(_VALIDATE.sys, "argv", [str(_VALIDATE_PATH), *map(str, args)])
    with pytest.raises(SystemExit) as caught:
        _VALIDATE.main()
    return caught.value.code, capsys.readouterr().out


def test_cli_validates_a_split_model(tmp_path, monkeypatch, capsys, offline):
    path = _write_split(tmp_path)

    code, output = _run_cli(monkeypatch, capsys, path)

    assert code == 0
    assert "Validation PASSED: model.yaml" in output


def test_cli_reports_a_resolution_error(tmp_path, monkeypatch, capsys, offline):
    path = _write_split(tmp_path, _split_model(_ref(iri="missing.dataset.yaml")))

    code, output = _run_cli(monkeypatch, capsys, path)

    assert code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert f"[Reference] {path}: dataset_refs -> 0 iri 'missing.dataset.yaml' {_NO_SUCH_FILE}" in output


def test_cli_checks_the_resolved_document(tmp_path, monkeypatch, capsys, offline):
    relationship = {
        "name": "orders_to_suppliers",
        "from": "orders",
        "to": "suppliers",
        "from_columns": ["customer_id"],
        "to_columns": ["supplier_id"],
    }
    path = _write_split(tmp_path, _split_model(relationships=[relationship]))

    code, output = _run_cli(monkeypatch, capsys, path)

    assert code == 1
    assert "[Reference] Relationship 'orders_to_suppliers' in model 'shop' references unknown dataset 'suppliers'" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_cli_checks_metrics_against_the_fields_of_dataset_documents(tmp_path, monkeypatch, capsys, offline):
    metric = {"name": "total_discount", "expression": _expression("SUM(orders.discount)")}
    path = _write_split(tmp_path, _split_model(metrics=[metric]))

    code, output = _run_cli(monkeypatch, capsys, path)

    assert code == 0
    assert "'orders.discount' is not a declared field of dataset 'orders'" in output


def test_cli_rejects_a_dataset_document_on_its_own(tmp_path, monkeypatch, capsys, offline):
    path = _write(tmp_path / "orders.dataset.yaml", _dataset_document(_orders()))

    code, output = _run_cli(monkeypatch, capsys, path)

    assert code == 1
    assert f"[Reference] {path} is a dataset document; validate a model document that lists it in dataset_refs" in output
    assert "[Schema]" not in output


def _other_schema(tmp_path: Path) -> Path:
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.com/other.json"}
    return _write(tmp_path / "other-schema.json", json.dumps(schema))


@pytest.mark.parametrize("document", ["model.yaml", "orders.dataset.yaml"])
def test_cli_reads_both_kinds_as_plain_documents_under_another_schema(tmp_path, monkeypatch, capsys, offline, document):
    # The reference names no file, so a resolution would fail.
    _write_split(tmp_path, _split_model(_ref(iri="missing.dataset.yaml")))

    code, output = _run_cli(monkeypatch, capsys, "--schema", _other_schema(tmp_path), tmp_path / document)

    assert code == 0
    assert f"Validation PASSED: {document}" in output


def test_cli_resolves_under_a_copy_of_the_core_schema(tmp_path, monkeypatch, capsys, offline):
    path = _write_split(tmp_path, _split_model(_ref(iri="missing.dataset.yaml")))
    copied = tmp_path / "copied-schema.json"
    shutil.copy(_REPO / "core-spec/ossie-schema.json", copied)

    code, output = _run_cli(monkeypatch, capsys, "--schema", copied, path)

    assert code == 1
    assert f"[Reference] {path}: dataset_refs -> 0 iri 'missing.dataset.yaml' {_NO_SUCH_FILE}" in output


# ---------------------------------------------------------------------------
# The split TPC-DS example
# ---------------------------------------------------------------------------

_SPLIT_EXAMPLE = _REPO / "examples/tpcds_split/tpcds.semantic_model.yaml"


def test_the_split_example_resolves_to_the_single_file_example(offline):
    resolved, errors = _resolve(_SPLIT_EXAMPLE)

    assert errors == []
    assert resolved == yaml.safe_load((_REPO / "examples/tpcds_semantic_model.yaml").read_text(encoding="utf-8"))


def test_cli_validates_the_split_example(monkeypatch, capsys, offline):
    code, output = _run_cli(monkeypatch, capsys, _SPLIT_EXAMPLE)

    assert code == 0
    assert "Validation PASSED: tpcds.semantic_model.yaml" in output
