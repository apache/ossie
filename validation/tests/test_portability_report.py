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
import sys
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate_report", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

Member = _VALIDATE.MemberForm
_ROOT = Path(__file__).parents[2]


def _entry(dialect: str, expression: str) -> dict:
    return {"dialect": dialect, "expression": expression}


def _ext(vendor: str, data: dict) -> dict:
    return {"vendor_name": vendor, "data": data}


def _model() -> dict:
    """Build a valid model with an expression in every form the report tells apart."""
    return {
        "version": "0.3.0.dev0",
        "name": "sales",
        "nonportable_extensions": [_ext("ZETA", {"owner": "finance"}), _ext("ACME", {"tier": 1})],
        "datasets": [
            {
                "name": "orders",
                "source": "db.orders",
                "nonportable_extensions": [_ext("MIDL", {"refresh": "daily"})],
                "fields": [
                    {"name": "id", "expression": {"sql": "id"}},
                    {
                        "name": "customer_id",
                        "expression": {
                            "nonportable_dialects": [_entry("ANSI_SQL", "customer_id"), _entry("OSSIE_SQL_2026", "customer_id")]
                        },
                    },
                    {
                        "name": "amount",
                        "expression": {
                            "sql": "amount",
                            "nonportable_dialects": [_entry("ANSI_SQL", "amount"), _entry("SNOWFLAKE", "amount::NUMBER")],
                        },
                        "nonportable_extensions": [_ext("MIDL", {"format": "usd"})],
                    },
                    {"name": "status", "expression": {"nonportable_dialects": [_entry("ANSI_SQL", "status")]}},
                    {
                        "name": "placed_on",
                        "expression": {
                            "nonportable_dialects": [
                                _entry("SNOWFLAKE", "placed_at::DATE"),
                                _entry("ANSI_SQL", "CAST(placed_at AS DATE)"),
                            ]
                        },
                    },
                    {
                        "name": "region",
                        "expression": {"nonportable_dialects": [_entry("SNOWFLAKE", "IFF(region IS NULL, 'n/a', region)")]},
                    },
                ],
                "metrics": [
                    {"name": "order_total", "expression": {"sql": "SUM(amount)"}},
                    {
                        "name": "order_count",
                        "expression": {
                            "nonportable_dialects": [_entry("DAX", "COUNTROWS(orders)"), _entry("MDX", "[Measures].[Orders]")]
                        },
                        "nonportable_extensions": [_ext("MIDL", {"folder": "kpi"})],
                    },
                ],
            },
            {
                "name": "customers",
                "source": "db.customers",
                "fields": [{"name": "id", "expression": {"sql": "id"}}],
            },
        ],
        "relationships": [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
                "nonportable_extensions": [_ext("ACME", {"join": "left"})],
            }
        ],
        "metrics": [
            {"name": "revenue", "expression": {"nonportable_dialects": [_entry("ANSI_SQL", "SUM(orders.amount)")]}},
        ],
    }


_REPORT = """\
Portability report:
  Fields: 7, by most portable form: OSSIE_SQL_2026 4, ANSI_SQL 2, other 1
  Metrics: 3, by most portable form: OSSIE_SQL_2026 1, ANSI_SQL 1, other 1
  ANSI_SQL without OSSIE_SQL_2026:
    Field 'orders.status' (ANSI_SQL)
    Field 'orders.placed_on' (SNOWFLAKE, ANSI_SQL)
    Metric 'revenue' (ANSI_SQL)
  Other dialects only:
    Field 'orders.region' (SNOWFLAKE)
    Metric 'orders.order_count' (DAX, MDX)
  nonportable_extensions: 6 entries, 12.8% of the document
    on: model 2, dataset 1, field 1, metric 1, relationship 1
    vendors: ACME 2, MIDL 3, ZETA 1
"""


@pytest.fixture(autouse=True)
def _no_sql_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing here is about SQL; without sqlglot the SQL check would add its
    # warning to every result and the exact assertions below would fail.
    monkeypatch.setattr(_VALIDATE, "validate_sql", lambda data: [])


def _write(tmp_path: Path, document: object, name: str = "model.yaml") -> Path:
    path = tmp_path / name
    text = document if isinstance(document, str) else yaml.safe_dump(document, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def _run_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["validate.py", *argv])
    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()
    return raised.value.code, capsys.readouterr().out


def _share(model: dict) -> float:
    return _VALIDATE.portability_report(model).extension_share


def _written_out(model: dict) -> dict:
    """Copy a model with every object it shares written out where it is used."""
    return json.loads(json.dumps(model))


def test_each_field_and_metric_is_placed_by_its_most_portable_form() -> None:
    report = _VALIDATE.portability_report(_model())

    assert list(report.members) == [
        Member("field", "Field 'orders.id'", "OSSIE_SQL_2026", ("OSSIE_SQL_2026",)),
        Member("field", "Field 'orders.customer_id'", "OSSIE_SQL_2026", ("ANSI_SQL", "OSSIE_SQL_2026")),
        Member("field", "Field 'orders.amount'", "OSSIE_SQL_2026", ("OSSIE_SQL_2026", "ANSI_SQL", "SNOWFLAKE")),
        Member("field", "Field 'orders.status'", "ANSI_SQL", ("ANSI_SQL",)),
        Member("field", "Field 'orders.placed_on'", "ANSI_SQL", ("SNOWFLAKE", "ANSI_SQL")),
        Member("field", "Field 'orders.region'", "other", ("SNOWFLAKE",)),
        Member("metric", "Metric 'orders.order_total'", "OSSIE_SQL_2026", ("OSSIE_SQL_2026",)),
        Member("metric", "Metric 'orders.order_count'", "other", ("DAX", "MDX")),
        Member("field", "Field 'customers.id'", "OSSIE_SQL_2026", ("OSSIE_SQL_2026",)),
        Member("metric", "Metric 'revenue'", "ANSI_SQL", ("ANSI_SQL",)),
    ]


def test_a_dialect_with_two_entries_is_named_once() -> None:
    model = _model()
    model["datasets"][0]["fields"][5]["expression"]["nonportable_dialects"] = [
        _entry("SNOWFLAKE", "region"),
        _entry("DAX", "orders[region]"),
        _entry("SNOWFLAKE", "IFF(region IS NULL, 'n/a', region)"),
    ]

    assert _VALIDATE.portability_report(model).members[5].dialects == ("SNOWFLAKE", "DAX")


@pytest.mark.parametrize("dialect", ["ossie_sql_2026", "ansi_sql"])
def test_a_dialect_name_is_compared_as_written(dialect: str) -> None:
    model = _model()
    model["metrics"][0]["expression"]["nonportable_dialects"] = [_entry(dialect, "SUM(orders.amount)")]

    assert _VALIDATE.portability_report(model).members[-1].form == "other"


def test_a_member_whose_expression_the_schema_rejects_is_left_out() -> None:
    model = _model()
    model["datasets"][1]["fields"][0]["expression"] = {}

    assert len(_VALIDATE.portability_report(model).members) == 9


def test_extension_entries_are_counted_by_place_and_by_vendor() -> None:
    report = _VALIDATE.portability_report(_model())

    assert report.extension_places == {"model": 2, "dataset": 1, "field": 1, "metric": 1, "relationship": 1}
    assert report.extension_vendors == {"ZETA": 1, "ACME": 2, "MIDL": 3}


def test_the_share_is_the_text_of_the_entries_over_the_text_of_the_document() -> None:
    model = {
        "version": "1",
        "name": "m",
        "ai_context": {7: "days", "tags": {"ab"}, "pairs": [("k", 5)], "when": None},
        "nonportable_extensions": [_ext("V", {"k": [True, 12, "xyz"]})],
        "datasets": [{"name": "d", "source": "s"}],
    }

    # Entries: vendor_name V data k True 12 xyz, 26 characters. The rest:
    # version 1 name m ai_context 7 days tags ab pairs k 5 when None
    # nonportable_extensions datasets name d source s, 91 characters.
    assert _share(model) == 26 / 117


def test_the_share_grows_with_the_payload() -> None:
    small, large = _model(), _model()
    large["nonportable_extensions"][0]["data"]["notes"] = ["x" * 500]

    assert 0 < _share(small) < _share(large) < 1


def test_a_payload_of_numbers_counts_by_their_length() -> None:
    model = _model()
    model["nonportable_extensions"][0]["data"]["ids"] = [1000000] * 5000

    assert _share(model) > 0.9


def test_an_integer_too_long_to_print_counts_about_its_digits() -> None:
    model = _VALIDATE.without_extensions(_model())
    model["nonportable_extensions"] = [_ext("ACME", {"mask": int("F" * 4000, 16)})]
    printable = _VALIDATE.without_extensions(_model())
    printable["nonportable_extensions"] = [_ext("ACME", {"mask": "9" * 4800})]

    assert _share(model) == _share(printable)


@pytest.mark.parametrize("note", [{"note": "one note in two places"}, ["one note in two places"]])
def test_what_aliases_share_counts_once(note: object) -> None:
    aliased = _model()
    aliased["ai_context"] = {"here": note, "there": note}

    assert _share(aliased) > _share(_written_out(aliased))


def test_a_value_that_is_not_a_string_counts_as_it_prints() -> None:
    dated, spelled = _model(), _model()
    dated["ai_context"] = {"since": date(2024, 1, 2), "rate": 0.5}
    spelled["ai_context"] = {"since": "2024-01-02", "rate": "0.5"}

    assert _share(dated) == _share(spelled)


def test_a_model_with_nothing_in_it_has_no_share() -> None:
    report = _VALIDATE.portability_report({})

    assert report.extension_share == 0
    assert not report.members


def test_what_an_entry_shares_with_the_rest_of_the_model_is_the_models() -> None:
    aliased, emptied = _model(), _model()
    datasets = aliased["datasets"]
    aliased["nonportable_extensions"][0]["data"]["copy"] = datasets
    emptied["nonportable_extensions"][0]["data"]["copy"] = []

    assert _share(aliased) == _share(emptied) < _share(_written_out(aliased))


def test_an_entry_two_places_share_is_counted_at_both_and_measured_once() -> None:
    model = _model()
    entry = model["datasets"][0]["nonportable_extensions"][0]
    model["datasets"][1]["nonportable_extensions"] = [entry]

    report = _VALIDATE.portability_report(model)

    assert report.extension_places["dataset"] == 2
    assert report.extension_share < _share(_model()) < _share(_written_out(model))


def test_an_alias_back_to_its_own_anchor_adds_only_its_key() -> None:
    plain, looped = _model(), _model()
    plain["ai_context"] = {"note": "kept"}
    looped["ai_context"] = {"note": "kept"}
    looped["ai_context"]["again"] = looped["ai_context"]
    looped["ai_context"]["twice"] = [looped["ai_context"]] * 300

    assert _VALIDATE._text_size([looped], set()) == _VALIDATE._text_size([plain], set()) + len("again") + len("twice")


def test_a_loop_through_fields_that_datasets_share_keeps_the_share_a_share() -> None:
    model = _model()
    fields = model["datasets"][0]["fields"]
    fields[2]["ai_context"] = {"siblings": fields}
    model["datasets"][1]["fields"] = fields
    model["ai_context"] = {"see": fields[2]}

    report = _VALIDATE.portability_report(model)

    assert report.extension_places["field"] == 2
    assert 0 < report.extension_share < 1


def test_nesting_deeper_than_the_recursion_limit_is_measured() -> None:
    model = _model()
    nested: dict = {"leaf": "x"}
    for _ in range(sys.getrecursionlimit() * 2):
        nested = {"a": nested}
    model["ai_context"] = nested

    assert 0 < _share(model) < _share(_model())


def test_a_model_without_entries_has_no_share() -> None:
    model = _VALIDATE.without_extensions(_model())

    report = _VALIDATE.portability_report(model)

    assert report.extension_share == 0
    assert not report.extension_places
    assert _VALIDATE.format_portability_report(report)[-1] == "  nonportable_extensions: none"


def test_one_entry_is_not_called_entries() -> None:
    model = _VALIDATE.without_extensions(_model())
    model["nonportable_extensions"] = [_ext("ACME", {"owner": "finance"})]

    lines = _VALIDATE.format_portability_report(_VALIDATE.portability_report(model))

    assert lines[-3].startswith("  nonportable_extensions: 1 entry, ")


def test_the_report_names_what_has_no_portable_form() -> None:
    lines = _VALIDATE.format_portability_report(_VALIDATE.portability_report(_model()))

    assert "\n".join(lines) + "\n" == _REPORT


def test_a_portable_model_lists_no_names() -> None:
    model = _VALIDATE.without_extensions(_model())
    model["datasets"] = model["datasets"][1:]
    del model["relationships"], model["metrics"]

    assert _VALIDATE.format_portability_report(_VALIDATE.portability_report(model)) == [
        "Portability report:",
        "  Fields: 1, by most portable form: OSSIE_SQL_2026 1, ANSI_SQL 0, other 0",
        "  Metrics: 0, by most portable form: OSSIE_SQL_2026 0, ANSI_SQL 0, other 0",
        "  nonportable_extensions: none",
    ]


_HEAD = "version: 0.3.0.dev0\nname: sales\n"
_BODY = """\
nonportable_extensions:
  - vendor_name: ACME
    data: {owner: finance}
datasets:
  - name: orders
    source: db.orders
    fields:
      - name: id
        expression: {sql: id}
"""


@pytest.mark.parametrize(
    "ai_context",
    [
        pytest.param("ai_context: {2024-01-01: launch, since: 2024-01-02, 7: days}\n", id="date-key"),
        pytest.param("ai_context: &loop\n  again: *loop\n", id="alias-to-itself"),
        pytest.param("ai_context: " + "{a: " * 300 + "1" + "}" * 300 + "\n", id="nesting"),
        pytest.param("ai_context: {blob: !!binary aGVsbG8=, tags: !!set {a, b}, order: !!omap [a: 1]}\n", id="yaml-types"),
    ],
)
def test_cli_reports_a_passing_model_whatever_sits_under_ai_context(
    ai_context: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, "--report", str(_write(tmp_path, _HEAD + ai_context + _BODY)))

    assert code == 0
    assert "Validation PASSED: model.yaml\nPortability report:\n" in output
    assert "  nonportable_extensions: 1 entry, " in output


def test_cli_prints_the_report_after_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, "--report", str(_write(tmp_path, _model())))

    assert code == 0
    assert output == "Validation PASSED: model.yaml\n" + _REPORT


@pytest.mark.parametrize("argv", [["FILE", "--report"], ["--report", "FILE", "--report"], ["--report", "--", "FILE"]])
def test_cli_takes_the_flag_anywhere_before_the_separator(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_write(tmp_path, _model()))

    code, output = _run_main(monkeypatch, capsys, *(path if arg == "FILE" else arg for arg in argv))

    assert code == 0
    assert output == "Validation PASSED: model.yaml\n" + _REPORT


def test_cli_reads_the_flag_after_the_separator_as_a_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, "--", str(_write(tmp_path, _model())), "--report")

    assert code == 1
    assert "Error: File not found: --report" in output
    assert "Portability report" not in output


def test_cli_reports_after_the_findings_of_a_vendor_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def check(data: dict, site: object) -> dict:
        return {"message": "checked", "severity": "warning"}

    monkeypatch.setattr(_VALIDATE, "load_vendor_validators", lambda names: {"ZETA": check})

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ZETA", "--report", str(_write(tmp_path, _model())))

    assert code == 0
    assert output == "  [Vendor ZETA] Warning: Model 'sales': checked\nValidation PASSED: model.yaml\n" + _REPORT


def test_cli_prints_no_report_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, str(_write(tmp_path, _model())))

    assert code == 0
    assert output == "Validation PASSED: model.yaml\n"


def test_cli_reports_a_model_that_fails_a_check_and_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = _model()
    model["relationships"][0]["to"] = "nowhere__"

    code, output = _run_main(monkeypatch, capsys, "--report", str(_write(tmp_path, model)))

    assert code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert output.endswith(_REPORT)


@pytest.mark.parametrize("broken", ["expression", "extension data"])
def test_cli_prints_no_report_for_a_model_with_a_schema_error(
    broken: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = _model()
    if broken == "expression":
        model["datasets"][0]["fields"][0]["expression"] = {}
    else:
        model["nonportable_extensions"][0]["data"]["since"] = float("nan")

    code, output = _run_main(monkeypatch, capsys, "--report", str(_write(tmp_path, model)))

    assert code == 1
    assert "[Schema]" in output
    assert "Portability report" not in output


@pytest.mark.parametrize(
    ("schema", "document"),
    [("ontology/ontology.json", "examples/flights.yaml"), ("ontology/mapping.json", "examples/flights.mapping.yaml")],
)
def test_cli_prints_no_report_for_an_ontology_or_a_mapping_document(
    schema: str, document: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, "--report", "--schema", str(_ROOT / schema), str(_ROOT / document))

    assert code == 0
    assert "Validation PASSED" in output
    assert "Portability report" not in output


def test_cli_reports_each_file_under_its_own_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _write(tmp_path, _model(), "first.yaml")
    second = _write(tmp_path, _VALIDATE.without_extensions(_model()), "second.yaml")

    code, output = _run_main(monkeypatch, capsys, "--report", str(first), str(second))

    assert code == 0
    assert output.count("Portability report:") == 2
    assert f"== {first}\nValidation PASSED: first.yaml\n{_REPORT}== {second}\n" in output
    assert output.endswith("  nonportable_extensions: none\n\n2 of 2 file(s) passed\n")


def test_cli_keeps_the_result_and_the_other_files_when_the_report_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _write(tmp_path, _model(), "first.yaml")
    second = _write(tmp_path, _model(), "second.yaml")
    report = _VALIDATE.portability_report
    reported: list[dict] = []

    def raise_on_the_first(model: dict):
        reported.append(model)
        if len(reported) == 1:
            raise RuntimeError("broken report")
        return report(model)

    monkeypatch.setattr(_VALIDATE, "portability_report", raise_on_the_first)
    monkeypatch.setattr(sys, "argv", ["validate.py", "--report", str(first), str(second)])

    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()

    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert f"Validation PASSED: first.yaml\nError: RuntimeError while reporting on {first}: broken report\n" in captured.out
    assert captured.out.endswith(_REPORT + "\n2 of 2 file(s) passed\n")
    assert "RuntimeError: broken report" in captured.err
