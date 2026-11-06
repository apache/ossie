# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

import copy
import json
import subprocess
import sys
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location("validate_mcp", ROOT / "validation/validate_mcp.py")
VALIDATOR = module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


@pytest.fixture
def capture():
    return json.loads((ROOT / "validation/mcp/example-capture.json").read_text(encoding="utf-8"))


def success(capture):
    return capture["cases"][0]["result"]


def payload(capture):
    return success(capture)["structuredContent"]


def resource(capture):
    return success(capture)["content"][1]["resource"]


def codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_published_example_passes_with_explicit_unchecked_scope(capture):
    report = VALIDATOR.validate_capture(capture)
    assert report["status"] == "passed"
    assert report["cases_checked"] == 2
    assert report["scope"] == "captured MCP endpoint contract"
    assert "authorization" in " ".join(report["unchecked"])
    assert "grain" in " ".join(report["unchecked"])


def test_generation_has_no_drift():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate-mcp-profile.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("change", [
    lambda c: c.update(protocol_version="2025-11-25"),
    lambda c: c.update(cases=[]),
    lambda c: c["tool"].update(name="native_execute"),
    lambda c: c["tool"].update(annotations={"readOnlyHint": "true"}),
    lambda c: success(c).pop("resultType"),
    lambda c: success(c).pop("isError"),
    lambda c: success(c).update(resultType="task"),
    lambda c: resource(c).update(text=42),
    lambda c: resource(c).update(blob="ZGF0YQ=="),
    lambda c: resource(c).update(_meta=None),
    lambda c: payload(c).update(contract_version="0.3-draft"),
    lambda c: payload(c).pop("model"),
    lambda c: payload(c)["diagnostics"].update(state="partial"),
    lambda c: c["cases"][1]["result"]["structuredContent"]["suggestions"][0].update(replacement_query="SELECT 1"),
])
def test_schema_violations_fail(capture, change):
    change(capture)
    report = VALIDATOR.validate_capture(capture)
    assert report["status"] == "failed"
    assert "CONTRACT_SCHEMA" in codes(report)


@pytest.mark.parametrize(("change", "expected"), [
    (lambda c: c["cases"][0]["arguments"].update(query="SELECT 1"), "INVALID_INPUT_ACCEPTED"),
    (lambda c: success(c).update(isError=True), "STATUS_MISMATCH"),
    (lambda c: payload(c).update(data_source_id="another-model"), "SOURCE_MISMATCH"),
    (lambda c: resource(c).update(mimeType="text/plain"), "RESOURCE_MISMATCH"),
    (lambda c: resource(c).update(text="wrong,header\r\nWest,12345.67\r\n"), "CSV_HEADER"),
    (lambda c: resource(c).update(text="region,total_revenue\r\nWest\r\n"), "CSV_ARITY"),
    (lambda c: resource(c).update(text="region,total_revenue\r\n"), "CSV_ROW_COUNT"),
    (lambda c: resource(c).update(text='region,total_revenue\r\n"unclosed'), "INVALID_CSV"),
    (lambda c: payload(c)["preview"].update(row_count=2), "PREVIEW_ROW_COUNT"),
    (lambda c: payload(c)["preview"].update(has_more=True), "PREVIEW_COMPLETENESS"),
    (lambda c: payload(c)["preview"].update(rows=[["West"]]), "PREVIEW_ARITY"),
    (lambda c: payload(c)["preview"].update(rows=[["East", "12345.67"]]), "PREVIEW_VALUES"),
    (lambda c: payload(c)["preview"].update(rows=[["West", 12345.67]]), "VALUE_ENCODING"),
    (lambda c: payload(c)["preview"].update(rows=[["West", "12,345.67"]]), "VALUE_ENCODING"),
    (lambda c: payload(c)["preview"]["columns"][0].update(name="other"), "PREVIEW_COLUMNS"),
    (lambda c: resource(c)["_meta"][VALIDATOR.METADATA].update(completeness="truncated"), "CSV_METADATA_MISMATCH"),
    (lambda c: success(c)["content"].pop(1), "MISSING_EMBEDDED_RESOURCE"),
    (lambda c: success(c)["content"].append(copy.deepcopy(success(c)["content"][1])), "DUPLICATE_RESOURCE"),
    (lambda c: payload(c)["result"]["resources"].append(copy.deepcopy(payload(c)["result"]["resources"][0])), "DUPLICATE_RESOURCE"),
    (lambda c: c["tool"].update(annotations={"readOnlyHint": False}), "READ_ONLY_DECLARATION"),
])
def test_cross_surface_contract_violations(capture, change, expected):
    change(capture)
    report = VALIDATOR.validate_capture(capture)
    assert report["status"] == "failed"
    assert expected in codes(report)


def test_missing_resource_metadata_fails(capture):
    resource(capture).pop("_meta")
    assert VALIDATOR.validate_capture(capture)["status"] == "failed"


def test_descriptor_cannot_be_satisfied_by_a_link(capture):
    descriptor = payload(capture)["result"]["resources"][0]
    success(capture)["content"][1] = {
        "type": "resource_link", "uri": descriptor["uri"], "name": descriptor["name"],
    }
    assert "MISSING_EMBEDDED_RESOURCE" in codes(VALIDATOR.validate_capture(capture))


def test_same_binding_cannot_return_different_model_revisions(capture):
    changed = copy.deepcopy(capture["cases"][0])
    changed["result"]["structuredContent"]["model"]["revision"] = "r2"
    capture["cases"].append(changed)
    assert "MODEL_REBOUND" in codes(VALIDATOR.validate_capture(capture))


def test_input_and_replacement_use_identical_query_schema(capture):
    for name in ("expression", "projection", "predicate", "query"):
        assert capture["tool"]["inputSchema"]["$defs"][name] == capture["tool"]["outputSchema"]["$defs"][name]


def test_schema_annotations_and_local_def_names_do_not_matter(capture):
    schema = capture["tool"]["inputSchema"]
    schema["description"] = "Vendor implementation"
    schema["required"].reverse()
    schema["$defs"]["query_copy"] = schema["$defs"].pop("query")
    schema["properties"]["query"]["$ref"] = "#/$defs/query_copy"
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"


def test_unproven_schema_equivalence_is_never_success(capture):
    capture["tool"]["inputSchema"] = {"type": "object"}
    report = VALIDATOR.validate_capture(capture)
    assert report["status"] == "unverified"
    assert report["unverified"]


def test_invalid_schema_is_explicit_failure(capture):
    capture["tool"]["inputSchema"] = {"type": "not-a-json-type"}
    assert "INVALID_DECLARATION" in codes(VALIDATOR.validate_capture(capture))


@pytest.mark.parametrize("ref", ["https://example.invalid/schema.json", "#/$defs/loop"])
def test_schema_normalization_never_retrieves_or_recurses_unboundedly(capture, monkeypatch, ref):
    def no_network(*args, **kwargs):
        pytest.fail("Captured schemas must not trigger network reads")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    capture["tool"]["inputSchema"] = {
        "$ref": ref, "$defs": {"loop": {"$ref": "#/$defs/loop"}},
    }
    assert VALIDATOR.validate_capture(capture)["status"] == "unverified"


def test_advertised_output_schema_requires_structured_content(capture):
    success(capture).pop("structuredContent")
    assert "MISSING_STRUCTURED_OUTPUT" in codes(VALIDATOR.validate_capture(capture))


def test_markdown_only_profile_is_explicitly_unverified(capture):
    capture["tool"].pop("outputSchema")
    for case in capture["cases"]:
        case["result"].pop("structuredContent")
    assert VALIDATOR.validate_capture(capture)["status"] == "unverified"


def test_no_preview_is_valid(capture):
    payload(capture).pop("preview")
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"


def test_scalar_capture_does_not_need_a_semantic_planner(capture):
    capture["cases"][0]["arguments"]["query"] = {"fields": ["customers.region"]}
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"


def test_header_only_zero_rows_are_success(capture):
    resource(capture)["text"] = "region,total_revenue\r\n"
    resource(capture)["_meta"][VALIDATOR.METADATA]["row_count"] = 0
    payload(capture)["result"]["row_count"] = 0
    payload(capture)["preview"].update(rows=[], row_count=0)
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"


def test_duplicate_names_nulls_empty_strings_precision_and_escapes(capture):
    columns = [{"name": "value", "datatype": "String"}, {"name": "value", "datatype": "Integer"}]
    rows = [[None, "9007199254740993"], ["", "2"], [r"\N", "3"]]
    resource(capture)["text"] = "value,value\r\n\\N,9007199254740993\r\n,2\r\n\\\\N,3\r\n"
    resource(capture)["_meta"][VALIDATOR.METADATA].update(columns=columns, row_count=3)
    payload(capture)["result"]["row_count"] = 3
    payload(capture)["preview"].update(columns=columns, rows=rows, row_count=3)
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"


def test_preview_sample_and_first_rows_have_distinct_order_rules(capture):
    resource(capture)["text"] = "region,total_revenue\r\nWest,12345.67\r\nEast,2.00\r\n"
    resource(capture)["_meta"][VALIDATOR.METADATA]["row_count"] = 2
    payload(capture)["result"]["row_count"] = 2
    payload(capture)["preview"].update(rows=[["East", "2.00"]], has_more=True, selection="sample")
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"
    payload(capture)["preview"]["selection"] = "first_rows"
    assert "PREVIEW_SELECTION" in codes(VALIDATOR.validate_capture(capture))


@pytest.mark.parametrize(("datatype", "value", "valid"), [
    ("Integer", "9007199254740993", True),
    ("Integer", 9007199254740993, False),
    ("Decimal", "1234.5600", True),
    ("Decimal", "1.234,56", False),
    ("Float", float("nan"), False),
    ("Float", True, False),
    ("Date", "2026-10-09", True),
    ("Date", "09/10/2026", False),
    ("DateTime", "2026-10-09T10:30:00", True),
    ("DateTime", "2026-10-09T10:30:00+02:00", False),
    ("DateTimeTz", "2026-10-09T10:30:00+02:00", True),
    ("DateTimeTz", "2026-10-09T10:30:00", False),
])
def test_machine_value_types(datatype, value, valid):
    if valid:
        assert VALIDATOR._cell(value, datatype) == value
    else:
        with pytest.raises(ValueError):
            VALIDATOR._cell(value, datatype)


def test_invalid_binary_base64_fails(capture):
    success(capture)["content"].append({
        "type": "resource",
        "resource": {"uri": "file:///data.xlsx", "mimeType": "application/octet-stream", "blob": "!"},
    })
    assert "INVALID_BASE64" in codes(VALIDATOR.validate_capture(capture))


def test_nested_schema_ids_cannot_produce_a_false_equivalence_pass(capture):
    capture["tool"]["inputSchema"]["$defs"]["query"]["$id"] = "urn:other:scope"
    assert VALIDATOR.validate_capture(capture)["status"] == "unverified"


def test_opaque_encodings_are_unverified_not_misreported_as_invalid(capture):
    columns = [{"name": "opaque", "datatype": "Opaque"}]
    resource(capture)["text"] = 'opaque\r\n"{""x"":1}"\r\n'
    resource(capture)["_meta"][VALIDATOR.METADATA]["columns"] = columns
    payload(capture)["preview"].update(columns=columns, rows=[[{"x": 1}]])
    assert VALIDATOR.validate_capture(capture)["status"] == "unverified"


def test_float_preview_cannot_lose_negative_zero(capture):
    columns = [{"name": "f", "datatype": "Float"}]
    resource(capture)["text"] = "f\r\n-0.0\r\n"
    resource(capture)["_meta"][VALIDATOR.METADATA]["columns"] = columns
    payload(capture)["preview"].update(columns=columns, rows=[[-0.0]])
    assert VALIDATOR.validate_capture(capture)["status"] == "passed"
    payload(capture)["preview"]["rows"] = [[0.0]]
    assert "PREVIEW_VALUES" in codes(VALIDATOR.validate_capture(capture))


def test_large_valid_csv_cells_are_unverified_when_parser_budget_is_exceeded(capture):
    columns = [{"name": "s", "datatype": "String"}]
    resource(capture)["text"] = "s\r\n" + ("x" * 200000) + "\r\n"
    resource(capture)["_meta"][VALIDATOR.METADATA]["columns"] = columns
    payload(capture).pop("preview")
    assert VALIDATOR.validate_capture(capture)["status"] == "unverified"


@pytest.mark.parametrize(("text", "quoted_null"), [
    ('s\r\n"\\N"\r\n', True),
    ('s\r\n\\N\r\n', False),
    ('"\\N"\r\nx\r\n', False),
    ('s\r\n"line1\r\n""\\N"""\r\n', False),
    ('s\r\n"\\\\N"\r\n', False),
])
def test_csv_null_quoting_is_checked_without_confusing_headers_or_escaped_quotes(text, quoted_null):
    assert VALIDATOR._has_quoted_null(text) == quoted_null


@pytest.mark.parametrize("body", ['{"tool":1,"tool":2}', '{"x":NaN}', 'not-json'])
def test_cli_rejects_malformed_capture(tmp_path, capsys, body):
    path = tmp_path / "capture.json"
    path.write_text(body, encoding="utf-8")
    assert VALIDATOR.main([str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_cli_exit_codes(capture, tmp_path, capsys):
    path = tmp_path / "capture.json"
    for expected, mutate in [
        (0, lambda c: None),
        (2, lambda c: c["tool"].update(inputSchema={"type": "object"})),
        (1, lambda c: success(c).update(isError=True)),
    ]:
        value = copy.deepcopy(capture)
        mutate(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        assert VALIDATOR.main([str(path)]) == expected
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == {0: "passed", 1: "failed", 2: "unverified"}[expected]
