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
import types
from importlib.metadata import EntryPoint
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate_vendor", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

_GROUP = _VALIDATE.VENDOR_VALIDATOR_GROUP
Site = _VALIDATE.ExtensionSite


def _ext(vendor: str, data: str) -> dict:
    return {"vendor_name": vendor, "data": data}


def _model() -> dict:
    """A valid model with an ACME extension at each place the schema allows one."""
    return {
        "version": "0.2.0.dev0",
        "name": "sales",
        "custom_extensions": [_ext("ACME", '{"at": "model"}'), _ext("OTHER", "not json")],
        "datasets": [
            {
                "name": "orders",
                "source": "db.orders",
                "custom_extensions": [_ext("ACME", '{"at": "dataset"}')],
                "fields": [
                    {"name": "customer_id", "expression": {"sql": "customer_id"}},
                    {
                        "name": "amount",
                        "expression": {"sql": "amount"},
                        "custom_extensions": [_ext("ACME", '{"at": "field"}')],
                    },
                ],
                "metrics": [
                    {
                        "name": "order_total",
                        "expression": {"sql": "SUM(amount)"},
                        "custom_extensions": [_ext("ACME", '{"at": "dataset metric"}')],
                    }
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
                "custom_extensions": [_ext("ACME", '{"at": "relationship"}')],
            }
        ],
        "metrics": [
            {
                "name": "total",
                "expression": {"sql": "SUM(orders.amount)"},
                "custom_extensions": [_ext("ACME", '{"at": "metric"}')],
            }
        ],
    }


# (site handed to the validator, label the script prints, "at" tag of the payload) per site in _model()
_EXPECTED = [
    (Site("model", "sales"), "Model 'sales'", "model"),
    (Site("dataset", "orders"), "Dataset 'orders' in model 'sales'", "dataset"),
    (Site("field", "amount", "orders"), "Field 'orders.amount' in model 'sales'", "field"),
    (Site("metric", "order_total", "orders"), "Metric 'orders.order_total' in model 'sales'", "dataset metric"),
    (Site("metric", "total"), "Metric 'total' in model 'sales'", "metric"),
    (
        Site("relationship", "orders_to_customers"),
        "Relationship 'orders_to_customers' in model 'sales'",
        "relationship",
    ),
]


@pytest.fixture(autouse=True)
def _no_sql_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing here is about SQL; without sqlglot the SQL check would add its
    # warning to every result and the exact assertions below would fail.
    monkeypatch.setattr(_VALIDATE, "validate_sql", lambda data: [])


@pytest.fixture
def core_schema() -> dict:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    return json.loads(schema_path.read_text())


def _register(monkeypatch: pytest.MonkeyPatch, **validators: object) -> None:
    """Make one entry point per vendor visible under the vendor validator group."""
    points = []
    for name, attr in validators.items():
        module_name = f"{name.lower()}_validator"
        module = types.ModuleType(module_name)
        module.check = attr
        monkeypatch.setitem(sys.modules, module_name, module)
        points.append(EntryPoint(name=name, value=f"{module_name}:check", group=_GROUP))
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda group: points if group == _GROUP else [])


def _write(tmp_path: Path, model: dict) -> Path:
    path = tmp_path / "model.yaml"
    path.write_text(yaml.safe_dump(model), encoding="utf-8")
    return path


def _run_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["validate.py", *argv])
    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()
    return raised.value.code, capsys.readouterr().out


def test_payloads_stay_opaque_without_a_vendor(core_schema: dict) -> None:
    assert _VALIDATE.validate_document(_model(), core_schema) == []


def test_vendor_checks_wait_for_the_other_checks(core_schema: dict) -> None:
    calls: list[object] = []
    model = _model()
    model["datasets"][1]["name"] = "orders"

    errors = _VALIDATE.validate_document(model, core_schema, vendors={"ACME": lambda p, s: calls.append(s) or []})

    assert any(e.startswith("[Unique]") for e in errors)
    assert not [e for e in errors if e.startswith("[Vendor")]
    assert calls == []


def test_a_warning_from_the_other_checks_does_not_withhold_the_document(
    core_schema: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_VALIDATE, "validate_sql", lambda data: [_VALIDATE.ValidationWarning("[SQL] Warning: probe")])
    calls: list[object] = []

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": lambda p, s: calls.append(s) or []})

    assert errors == ["[SQL] Warning: probe"]
    assert calls == [site for site, _, _ in _EXPECTED]


def test_every_site_is_handed_to_the_validator(core_schema: dict) -> None:
    seen: list[tuple[object, object]] = []

    def check(payload: object, site: object) -> list[str]:
        seen.append((payload, site))
        return [f"saw {payload['at']}"]

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": check})

    assert errors == [f"[Vendor ACME] {label}: saw {tag}" for _, label, tag in _EXPECTED]
    assert seen == [({"at": tag}, site) for site, _, tag in _EXPECTED]


def test_unnamed_vendors_are_not_decoded(core_schema: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    decoded: list[str] = []

    def counting_loads(text: str, *args: object, **kwargs: object) -> object:
        decoded.append(text)
        return json.loads(text, *args, **kwargs)

    # Only the script's own json.loads calls are counted; jsonschema keeps the real module.
    stub = types.ModuleType("json_stub")
    stub.__dict__.update(json.__dict__)
    stub.loads = counting_loads
    monkeypatch.setattr(_VALIDATE, "json", stub)

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": lambda payload, site: []})

    assert errors == []
    assert "not json" not in decoded
    assert '{"at": "model"}' in decoded


def test_bad_json_is_reported_once_and_not_handed_over(core_schema: dict) -> None:
    calls: list[object] = []

    def check(payload: object, site: object) -> list[str]:
        calls.append(site)
        return []

    model = _model()
    model["datasets"][0]["custom_extensions"] = [_ext("ACME", "{not json")]

    errors = _VALIDATE.validate_document(model, core_schema, vendors={"ACME": check})

    assert len(errors) == 1
    assert errors[0].startswith("[Vendor ACME] Dataset 'orders' in model 'sales': data is not valid JSON: Expecting")
    assert Site("dataset", "orders") not in calls
    assert Site("model", "sales") in calls


def test_a_payload_that_is_not_an_object_still_reaches_the_validator(core_schema: dict) -> None:
    seen: list[object] = []
    model = _model()
    model["custom_extensions"] = [_ext("ACME", "42"), _ext("ACME", "null")]
    for dataset in model["datasets"]:
        dataset.pop("custom_extensions", None)
        for node in dataset["fields"] + dataset.get("metrics", []):
            node.pop("custom_extensions", None)
    for node in model["relationships"] + model["metrics"]:
        node.pop("custom_extensions", None)

    errors = _VALIDATE.validate_document(model, core_schema, vendors={"ACME": lambda p, s: seen.append(p) or []})

    assert errors == []
    assert seen == [42, None]


def test_a_raising_validator_is_reported_against_its_vendor(
    core_schema: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    def check(payload: object, site: object) -> list[str]:
        if site.kind == "model":
            raise KeyError("_v")
        return []

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": check})

    assert errors == ["[Vendor ACME] Model 'sales': validator raised KeyError: '_v'"]
    assert "KeyError: '_v'" in capsys.readouterr().err


def test_a_validator_that_exits_is_reported_like_one_that_raises(
    core_schema: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    def check(payload: object, site: object) -> list[str]:
        if site.kind == "model":
            sys.exit(0)
        if site.kind == "dataset":
            sys.exit()
        return []

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": check})

    assert errors == [
        "[Vendor ACME] Model 'sales': validator raised SystemExit: 0",
        "[Vendor ACME] Dataset 'orders' in model 'sales': validator raised SystemExit",
    ]
    assert "SystemExit: 0" in capsys.readouterr().err


def test_a_single_string_is_one_message_and_none_is_none(core_schema: dict) -> None:
    def check(payload: object, site: object) -> object:
        return "one finding" if site.kind == "model" else None

    errors = _VALIDATE.validate_document(_model(), core_schema, vendors={"ACME": check})

    assert errors == ["[Vendor ACME] Model 'sales': one finding"]


def test_cli_runs_the_registered_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _register(monkeypatch, ACME=lambda payload, site: [f"{payload['at']} rejected"])

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(_write(tmp_path, _model())))

    assert code == 1
    assert "Validation FAILED with 6 error(s)" in output
    assert "[Vendor ACME] Field 'orders.amount' in model 'sales': field rejected" in output


def test_cli_runs_a_repeated_vendor_name_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _register(monkeypatch, ACME=lambda payload, site: ["acme says no"], OTHER=lambda payload, site: ["never"])
    model = _model()
    model["custom_extensions"] = [_ext("ACME", "{}"), _ext("OTHER", "{}")]

    code, output = _run_main(
        monkeypatch, capsys, "--vendor", "ACME", "--vendor", "OTHER", "--vendor", "ACME", str(_write(tmp_path, model))
    )

    assert code == 1
    assert output.count("[Vendor ACME] Model 'sales': acme says no") == 1
    assert output.count("[Vendor OTHER] Model 'sales': never") == 1


def test_cli_passes_without_the_flag_even_when_a_validator_would_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _register(monkeypatch, ACME=lambda payload, site: ["rejected"])

    code, output = _run_main(monkeypatch, capsys, str(_write(tmp_path, _model())))

    assert code == 0
    assert "Validation PASSED" in output


def test_missing_validator_is_an_error_before_any_file_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda group: [])

    code, output = _run_main(
        monkeypatch, capsys, "--vendor", "THOUGHTSPOT", "--vendor", "THOUGHTSPOT", str(_write(tmp_path, _model()))
    )

    assert code == 1
    assert output.count("Error: No validator is installed for vendor 'THOUGHTSPOT'") == 1
    assert "uv run --with <package> validation/validate.py --vendor THOUGHTSPOT <file>" in output
    assert "Validation" not in output


def test_two_registrations_for_one_vendor_are_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    points = [
        EntryPoint(name="ACME", value="acme_one:check", group=_GROUP),
        EntryPoint(name="ACME", value="acme_two:check", group=_GROUP),
    ]
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda group: points)

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(_write(tmp_path, _model())))

    assert code == 1
    assert "Error: More than one validator is installed for vendor 'ACME': acme_one:check, acme_two:check" in output


def test_entry_point_that_does_not_import_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry_point = EntryPoint(name="ACME", value="acme_validator_missing:check", group=_GROUP)
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda group: [entry_point])

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(_write(tmp_path, _model())))

    assert code == 1
    assert (
        "Error: Could not load the validator for vendor 'ACME' from acme_validator_missing:check: "
        "ModuleNotFoundError:" in output
    )


def test_entry_point_that_exits_on_import_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "acme_exits_on_import.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    entry_point = EntryPoint(name="ACME", value="acme_exits_on_import:check", group=_GROUP)
    monkeypatch.setattr(_VALIDATE, "entry_points", lambda group: [entry_point])

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(_write(tmp_path, _model())))

    assert code == 1
    assert (
        "Error: Could not load the validator for vendor 'ACME' from acme_exits_on_import:check: "
        "SystemExit: 2" in output
    )
    assert "Validation" not in output


def test_entry_point_that_is_not_callable_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _register(monkeypatch, ACME="not a function")

    code, output = _run_main(monkeypatch, capsys, "--vendor", "ACME", str(_write(tmp_path, _model())))

    assert code == 1
    assert "Error: The validator for vendor 'ACME' (acme_validator:check) is not callable" in output


@pytest.mark.parametrize("argv", [["model.yaml", "--vendor"], ["--vendor", "--schema", "x.json"], ["--vendor", ""]])
def test_vendor_flag_needs_a_name(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = _run_main(monkeypatch, capsys, *argv)

    assert code == 1
    assert output.strip() == _VALIDATE.USAGE
