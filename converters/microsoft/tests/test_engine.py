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

"""Offline tests for the live engine validator.

Nothing here touches the network: the transport is stubbed, so these cover the
document rewriting and the finding logic without a workspace or a credential.
The one test that would deploy for real is skipped unless a workspace is given.
"""

import base64
import io
import json
from pathlib import Path

import pytest

from ossie_microsoft import engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def bim():
    with open(FIXTURES / "sales_model.bim", encoding="utf-8-sig") as fh:
        return json.load(fh)


# --- build_deployable ------------------------------------------------------


def test_every_partition_becomes_an_inline_import(bim):
    document = engine.build_deployable(bim, "m")
    for table in document["model"]["tables"]:
        partitions = table["partitions"]
        assert len(partitions) == 1
        source = partitions[0]["source"]
        # A calculation group keeps its own source type; everything else must be
        # inline M so that a refresh needs no gateway or credential.
        assert source["type"] in ("m", "calculationGroup")
        if source["type"] == "m":
            assert "Sql.Database" not in source["expression"]
            assert source["expression"].startswith("let Source = #table(")


def test_the_original_document_is_not_mutated(bim):
    before = json.dumps(bim, sort_keys=True)
    engine.build_deployable(bim, "m")
    assert json.dumps(bim, sort_keys=True) == before


def test_measures_and_relationships_survive_the_rewrite(bim):
    document = engine.build_deployable(bim, "m")
    original = {t["name"]: t for t in bim["model"]["tables"]}
    for table in document["model"]["tables"]:
        assert table.get("measures", []) == original[table["name"]].get("measures", [])
    assert document["model"]["relationships"] == bim["model"]["relationships"]


def test_the_compatibility_level_is_raised_to_a_modern_one():
    document = engine.build_deployable({"compatibilityLevel": 1500, "model": {}}, "m")
    assert document["compatibilityLevel"] >= 1604


def test_a_higher_compatibility_level_is_kept():
    document = engine.build_deployable({"compatibilityLevel": 1702, "model": {}}, "m")
    assert document["compatibilityLevel"] == 1702


def test_direct_lake_data_sources_and_expressions_are_dropped():
    document = engine.build_deployable(
        {
            "model": {
                "dataSources": [{"name": "lakehouse"}],
                "expressions": [{"name": "DatabaseQuery", "kind": "m"}],
                "tables": [],
            }
        },
        "m",
    )
    assert "dataSources" not in document["model"]
    assert "expressions" not in document["model"]


def test_a_calculation_group_forces_implicit_measures_off():
    document = engine.build_deployable(
        {"model": {"tables": [{"name": "TI", "calculationGroup": {}, "columns": []}]}}, "m"
    )
    assert document["model"]["discourageImplicitMeasures"] is True


def test_relationship_targets_get_distinct_values():
    document = engine.build_deployable(
        {
            "model": {
                "tables": [
                    {"name": "D", "columns": [{"name": "K", "dataType": "int64"}]},
                    {"name": "F", "columns": [{"name": "K", "dataType": "int64"}]},
                ],
                "relationships": [
                    {
                        "name": "r",
                        "fromTable": "F",
                        "fromColumn": "K",
                        "toTable": "D",
                        "toColumn": "K",
                    }
                ],
            }
        },
        "m",
    )
    tables = {t["name"]: t for t in document["model"]["tables"]}
    # The one-side must not repeat a value or contain a null, or the refresh
    # fails on the declared cardinality.
    assert "null" not in tables["D"]["partitions"][0]["source"]["expression"]
    assert "null" in tables["F"]["partitions"][0]["source"]["expression"]


def test_calculated_columns_are_left_to_the_engine():
    document = engine.build_deployable(
        {
            "model": {
                "tables": [
                    {
                        "name": "T",
                        "columns": [
                            {"name": "A", "dataType": "int64"},
                            {"name": "B", "type": "calculated", "expression": "T[A] * 2"},
                        ],
                    }
                ]
            }
        },
        "m",
    )
    expression = document["model"]["tables"][0]["partitions"][0]["source"]["expression"]
    assert '"A"' in expression
    assert '"B"' not in expression


def test_a_table_with_no_bound_columns_still_gets_a_partition():
    document = engine.build_deployable(
        {"model": {"tables": [{"name": "T", "columns": [{"name": "R", "type": "rowNumber"}]}]}}, "m"
    )
    assert document["model"]["tables"][0]["partitions"][0]["source"]["expression"]


# --- findings --------------------------------------------------------------


def test_a_finding_without_an_error_is_ok():
    assert engine.EngineFinding(kind="measure", object="T[M]").ok


def test_a_result_is_valid_only_when_nothing_failed():
    good = engine.EngineValidationResult(findings=(engine.EngineFinding("measure", "T[M]"),))
    assert good.is_valid
    good.raise_for_errors()

    bad = engine.EngineValidationResult(
        findings=(engine.EngineFinding("measure", "T[M]", error="boom"),)
    )
    assert not bad.is_valid
    assert bad.failures
    with pytest.raises(engine.EngineValidationError, match="T\\[M\\]: boom"):
        bad.raise_for_errors()


def test_a_stage_error_is_reported_with_its_stage():
    result = engine.EngineValidationResult(stage="deploy", error="nope")
    assert not result.is_valid
    with pytest.raises(engine.EngineValidationError, match="deploy: nope"):
        result.raise_for_errors()


def test_the_engine_diagnostic_is_pulled_out_of_the_rest_envelope():
    body = json.dumps(
        {
            "error": {
                "message": "generic",
                "pbi.error": {
                    "details": [{"detail": {"value": "Cannot find table <oii>X</oii>."}}]
                },
            }
        }
    )
    assert engine._engine_message(body) == "Cannot find table X."


def test_a_plain_error_message_is_used_when_there_is_no_detail():
    assert engine._engine_message(json.dumps({"error": {"message": "generic"}})) == "generic"


def test_an_unparsable_body_is_returned_as_text():
    assert "not json" in engine._engine_message("not json")


# --- check_model -----------------------------------------------------------


def _stub_evaluate(monkeypatch, responses):
    """Answer each DAX query from ``responses`` keyed by substring."""

    def fake(workspace, dataset, token, query):
        for needle, outcome in responses.items():
            if needle in query:
                return outcome
        return [{"[v]": 1}], None

    monkeypatch.setattr(engine, "evaluate", fake)


DOC = {"model": {"tables": [{"name": "T", "measures": [{"name": "M", "expression": "SUM(T[A])"}]}]}}


def test_a_working_measure_reports_its_value(monkeypatch):
    _stub_evaluate(monkeypatch, {"[M]": ([{"[v]": 42}], None)})
    findings = engine.check_model(DOC, "w", "d", "t")
    measure = next(f for f in findings if f.kind == "measure")
    assert measure.ok
    assert measure.value == 42


def test_a_measure_error_is_reported(monkeypatch):
    _stub_evaluate(monkeypatch, {"[M]": (None, "Cannot find table 'X'.")})
    measure = next(f for f in engine.check_model(DOC, "w", "d", "t") if f.kind == "measure")
    assert not measure.ok
    assert measure.value is None


def test_a_measure_the_engine_dropped_is_caught_by_re_evaluating(monkeypatch):
    # The engine drops a measure whose DAX did not compile, so asking for it by
    # name succeeds with no rows. The expression must be retried to get the
    # real diagnostic rather than silently passing.
    _stub_evaluate(
        monkeypatch,
        {"[M]": ([], None), "SUM(T[A])": (None, "Column 'A' cannot be found.")},
    )
    measure = next(f for f in engine.check_model(DOC, "w", "d", "t") if f.kind == "measure")
    assert not measure.ok
    assert "cannot be found" in measure.error


def test_a_dropped_measure_is_still_reported_when_the_retry_is_silent(monkeypatch):
    _stub_evaluate(monkeypatch, {"[M]": ([], None), "SUM(T[A])": ([], None)})
    measure = next(f for f in engine.check_model(DOC, "w", "d", "t") if f.kind == "measure")
    assert not measure.ok
    assert "rejected it" in measure.error


def test_a_multi_line_expression_is_joined_before_it_is_retried(monkeypatch):
    seen = []

    def fake(workspace, dataset, token, query):
        seen.append(query)
        return ([], None) if "[M]" in query else (None, "boom")

    monkeypatch.setattr(engine, "evaluate", fake)
    document = {
        "model": {
            "tables": [{"name": "T", "measures": [{"name": "M", "expression": ["SUM(", "T[A])"]}]}]
        }
    }
    engine.check_model(document, "w", "d", "t")
    assert any("SUM(\nT[A])" in q for q in seen)


def test_tables_are_checked_as_well_as_measures(monkeypatch):
    _stub_evaluate(monkeypatch, {"COUNTROWS": (None, "Cannot find table 'T'.")})
    table = next(f for f in engine.check_model(DOC, "w", "d", "t") if f.kind == "table")
    assert not table.ok


# --- transport -------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status, body, headers):
        self.status = status
        self._body = body
        self.headers = headers

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_request_returns_the_parsed_body(monkeypatch):
    captured = {}

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["auth"] = request.get_header("Authorization")
        captured["data"] = request.data
        return _FakeResponse(200, json.dumps({"id": "x"}), {"Location": "loc"})

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    status, body, headers = engine._request("POST", "https://e/x", "tok", {"a": 1})
    assert (status, body, headers["Location"]) == (200, {"id": "x"}, "loc")
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer tok"
    assert json.loads(captured["data"]) == {"a": 1}


def test_an_empty_response_body_is_none(monkeypatch):
    monkeypatch.setattr(
        engine.urllib.request, "urlopen", lambda request: _FakeResponse(204, "", {})
    )
    assert engine._request("DELETE", "https://e/x", "tok")[1] is None


def test_an_http_error_is_returned_rather_than_raised(monkeypatch):
    def fake_urlopen(request):
        raise engine.urllib.error.HTTPError(
            "https://e/x", 400, "Bad", {"h": "v"}, io.BytesIO(b'{"error":{"message":"no"}}')
        )

    monkeypatch.setattr(engine.urllib.request, "urlopen", fake_urlopen)
    status, body, _headers = engine._request("GET", "https://e/x", "tok")
    assert status == 400
    assert engine._engine_message(body) == "no"


def _stub_request(monkeypatch, handler):
    monkeypatch.setattr(engine, "_request", handler)


def test_deploy_returns_the_item_id(monkeypatch):
    _stub_request(monkeypatch, lambda *a, **k: (201, {"id": "ds"}, {}))
    assert engine.deploy({"model": {}}, "w", "n", "t") == ("ds", None)


def test_deploy_sends_the_document_as_base64_tmsl(monkeypatch):
    seen = {}

    def handler(method, url, token, payload=None):
        seen["payload"] = payload
        return 201, {"id": "ds"}, {}

    _stub_request(monkeypatch, handler)
    engine.deploy({"model": {"tables": []}}, "w", "n", "t")
    part = seen["payload"]["definition"]["parts"][0]
    assert part["path"] == "model.bim"
    assert json.loads(base64.b64decode(part["payload"])) == {"model": {"tables": []}}


def test_deploy_polls_a_long_running_operation(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)

    def handler(method, url, token, payload=None):
        if method == "POST":
            return 202, None, {"Location": "https://e/op"}
        if url.endswith("/result"):
            return 200, {"id": "ds"}, {}
        return 200, {"status": "Succeeded"}, {}

    _stub_request(monkeypatch, handler)
    assert engine.deploy({"model": {}}, "w", "n", "t") == ("ds", None)


def test_deploy_reports_a_failed_operation(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)

    def handler(method, url, token, payload=None):
        if method == "POST":
            return 202, None, {"Location": "https://e/op"}
        return 200, {"status": "Failed", "error": {"message": "bad model"}}, {}

    _stub_request(monkeypatch, handler)
    dataset, error = engine.deploy({"model": {}}, "w", "n", "t")
    assert dataset is None
    assert "bad model" in error


def test_deploy_reports_an_http_failure(monkeypatch):
    _stub_request(monkeypatch, lambda *a, **k: (403, json.dumps({"error": {"message": "no"}}), {}))
    dataset, error = engine.deploy({"model": {}}, "w", "n", "t")
    assert dataset is None
    assert "403" in error


def test_an_operation_that_never_settles_times_out(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    _stub_request(monkeypatch, lambda *a, **k: (200, {"status": "Running"}, {}))
    assert engine._wait_for_operation("https://e/op", "t", tries=2, delay=0) == {
        "status": "TimedOut"
    }


def test_refresh_returns_none_when_it_completes(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)

    def handler(method, url, token, payload=None):
        if method == "POST":
            return 202, None, {}
        return 200, {"value": [{"status": "Completed"}]}, {}

    _stub_request(monkeypatch, handler)
    assert engine.refresh("w", "d", "t") is None


def test_refresh_reports_a_failure(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)

    def handler(method, url, token, payload=None):
        if method == "POST":
            return 202, None, {}
        return 200, {"value": [{"status": "Failed", "serviceExceptionJson": "boom"}]}, {}

    _stub_request(monkeypatch, handler)
    assert "boom" in engine.refresh("w", "d", "t")


def test_refresh_reports_a_rejected_request(monkeypatch):
    _stub_request(monkeypatch, lambda *a, **k: (401, json.dumps({"error": {}}), {}))
    assert "401" in engine.refresh("w", "d", "t")


def test_refresh_times_out(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)

    def handler(method, url, token, payload=None):
        if method == "POST":
            return 202, None, {}
        return 200, {"value": [{"status": "InProgress"}]}, {}

    _stub_request(monkeypatch, handler)
    assert engine.refresh("w", "d", "t", tries=2, delay=0) == "refresh timed out"


def test_evaluate_returns_rows(monkeypatch):
    _stub_request(
        monkeypatch,
        lambda *a, **k: (200, {"results": [{"tables": [{"rows": [{"[v]": 1}]}]}]}, {}),
    )
    rows, error = engine.evaluate("w", "d", "t", "EVALUATE 1")
    assert rows == [{"[v]": 1}]
    assert error is None


def test_evaluate_returns_the_engine_error(monkeypatch):
    _stub_request(
        monkeypatch, lambda *a, **k: (400, json.dumps({"error": {"message": "bad dax"}}), {})
    )
    rows, error = engine.evaluate("w", "d", "t", "EVALUATE 1")
    assert rows is None
    assert error == "bad dax"


# --- orchestration ---------------------------------------------------------


def test_validate_with_engine_deletes_the_model_it_created(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "deploy", lambda *a, **k: ("ds", None))
    monkeypatch.setattr(engine, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(engine, "check_model", lambda *a, **k: ())
    def record(method, url, *a, **k):
        calls.append((method, url))
        return 200, None, {}

    _stub_request(monkeypatch, record)

    result = engine.validate_with_engine({}, workspace="w", fabric_token="a", powerbi_token="b")
    assert result.is_valid
    assert any(method == "DELETE" and "ds" in url for method, url in calls)


def test_validate_with_engine_can_keep_the_model(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "deploy", lambda *a, **k: ("ds", None))
    monkeypatch.setattr(engine, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(engine, "check_model", lambda *a, **k: ())
    _stub_request(monkeypatch, lambda method, url, *a, **k: calls.append(method) or (200, None, {}))

    engine.validate_with_engine({}, workspace="w", fabric_token="a", powerbi_token="b", keep=True)
    assert "DELETE" not in calls


def test_a_deploy_failure_stops_before_refreshing(monkeypatch):
    monkeypatch.setattr(engine, "deploy", lambda *a, **k: (None, "nope"))
    result = engine.validate_with_engine({}, workspace="w", fabric_token="a", powerbi_token="b")
    assert (result.stage, result.error) == ("deploy", "nope")


def test_a_refresh_failure_is_reported_with_its_stage(monkeypatch):
    monkeypatch.setattr(engine, "deploy", lambda *a, **k: ("ds", None))
    monkeypatch.setattr(engine, "refresh", lambda *a, **k: "broke")
    _stub_request(monkeypatch, lambda *a, **k: (200, None, {}))
    result = engine.validate_with_engine({}, workspace="w", fabric_token="a", powerbi_token="b")
    assert (result.stage, result.error) == ("refresh", "broke")


# --- guards ----------------------------------------------------------------


def test_validate_with_engine_requires_a_workspace():
    with pytest.raises(engine.EngineUnavailableError, match="workspace"):
        engine.validate_with_engine({}, workspace="", fabric_token="a", powerbi_token="b")


def test_validate_with_engine_requires_both_tokens():
    with pytest.raises(engine.EngineUnavailableError, match="token"):
        engine.validate_with_engine({}, workspace="w", fabric_token="", powerbi_token="b")
