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

"""Tests for the Power BI (TMSL ``model.bim``) -> Apache Ossie converter."""

import json
import logging
import warnings
from pathlib import Path

import pytest
import yaml

from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
from ossie_microsoft._common import VENDOR, read_stash
from ossie_microsoft.semantic_model_to_ossie import build_ossie_document

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _dataset(model, name):
    return next(d for d in model["datasets"] if d["name"] == name)


def _field(dataset, name):
    return next(f for f in dataset["fields"] if f["name"] == name)


def _metric(model, name):
    return next(m for m in model["metrics"] if m["name"] == name)


def _expression(node, dialect):
    return next(d["expression"] for d in node["expression"]["dialects"] if d["dialect"] == dialect)


# --- public API ------------------------------------------------------------


def test_public_module_exports_the_converters():
    import ossie_microsoft

    assert ossie_microsoft.__all__ == [
        "ConversionError",
        "EngineFinding",
        "EngineUnavailableError",
        "EngineValidationError",
        "EngineValidationResult",
        "TomUnavailableError",
        "TomValidationError",
        "TomValidationIssue",
        "TomValidationResult",
        "build_ossie_document",
        "convert_ossie_to_semantic_model",
        "convert_semantic_model_to_ossie",
        "validate_bim",
        "validate_tmsl",
        "validate_with_engine",
    ]
    assert callable(ossie_microsoft.convert_semantic_model_to_ossie)
    assert callable(ossie_microsoft.convert_ossie_to_semantic_model)


def test_cli_writes_ossie_yaml(tmp_path):
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)]) == 0
    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert document["semantic_model"][0]["name"] == "sales_model"


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    from ossie_microsoft.cli import main

    bad = tmp_path / "bad.bim"
    bad.write_text('{"not_a_model": true}', encoding="utf-8")
    assert main(["import", "-i", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err


# --- document shape --------------------------------------------------------


def test_document_header(osi):
    assert osi["version"] == "0.2.0.dev0"
    assert len(osi["semantic_model"]) == 1


def test_model_name_and_description(model):
    assert model["name"] == "sales_model"
    assert model["description"] == "Retail sales semantic model"


def test_accepts_model_bim_json_text():
    document = {"name": "x", "model": {"tables": []}}
    assert convert_semantic_model_to_ossie(json.dumps(document)) == (
        convert_semantic_model_to_ossie(document)
    )


def test_rejects_input_that_is_neither_a_mapping_nor_text():
    with pytest.raises(TypeError, match="model.bim JSON text"):
        convert_semantic_model_to_ossie(["not a model"])


def test_rejects_document_without_model():
    with pytest.raises(ValueError):
        convert_semantic_model_to_ossie({"name": "x"})


# --- datasets --------------------------------------------------------------


def test_exports_only_user_facing_tables(model):
    assert [d["name"] for d in model["datasets"]] == ["Sales", "Customer", "Calendar"]


def test_calculation_group_is_skipped_with_a_warning():
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "Time Intelligence",
                    "calculationGroup": {"precedence": 0},
                }
            ]
        },
    }
    with pytest.warns(UserWarning, match="calculation groups are not converted"):
        document = build_ossie_document(bim)
    assert document["semantic_model"][0]["datasets"] == []


def test_calculated_table_is_skipped_with_a_warning():
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "Calculated Table",
                    "partitions": [
                        {
                            "name": "Calculated Table",
                            "source": {
                                "type": "calculated",
                                "expression": "CALENDAR(DATE(2020, 1, 1), TODAY())",
                            },
                        }
                    ],
                }
            ]
        },
    }
    with pytest.warns(UserWarning, match="calculated tables are not converted"):
        document = build_ossie_document(bim)
    assert document["semantic_model"][0]["datasets"] == []


def test_row_number_column_is_skipped(model):
    assert "RowNumber" not in [f["name"] for f in _dataset(model, "Sales")["fields"]]


def test_keys_are_mapped(model):
    assert _dataset(model, "Sales")["primary_key"] == ["SalesKey"]
    customer = _dataset(model, "Customer")
    assert customer["primary_key"] == ["CustomerKey"]
    assert customer["unique_keys"] == [["Email"]]


@pytest.mark.parametrize(
    "dataset_name,expected_source",
    [
        ("Sales", "retail.dbo.sales"),
        ("Customer", "dbo.customer"),
        ("Calendar", "SELECT * FROM dbo.calendar"),
    ],
)
def test_partition_sources(model, dataset_name, expected_source):
    assert _dataset(model, dataset_name)["source"] == expected_source


# --- fields ----------------------------------------------------------------


def test_plain_column_uses_source_column_as_sql(model):
    amount = _field(_dataset(model, "Sales"), "Amount")
    assert _expression(amount, "ANSI_SQL") == "amount"
    assert amount["datatype"] == "Decimal"
    assert amount["description"] == "Extended sales amount"


def test_calculated_column_uses_dax_dialect(model):
    field = _field(_dataset(model, "Sales"), "AmountWithTax")
    assert _expression(field, "DAX") == "Sales[Amount] * 1.2"


def test_temporal_column_is_marked_as_time(model):
    assert _field(_dataset(model, "Sales"), "OrderDate")["dimension"] == {"is_time": True}


def test_non_temporal_time_data_category_is_marked_as_time(model):
    fiscal_year = _field(_dataset(model, "Calendar"), "FiscalYear")
    assert fiscal_year["datatype"] == "Integer"
    assert fiscal_year["dimension"] == {"is_time": True}


def test_plain_column_has_no_dimension_marker(model):
    assert "dimension" not in _field(_dataset(model, "Customer"), "Country")


# --- metrics ---------------------------------------------------------------


def test_measures_become_dax_metrics(model):
    assert [m["name"] for m in model["metrics"]] == ["Total Sales", "Order Count"]
    total = _metric(model, "Total Sales")
    assert _expression(total, "DAX") == "SUM ( Sales[Amount] )"
    assert total["description"] == "Sum of sales amount"
    assert total["description"] == "Sum of sales amount"


def test_multi_line_measure_expression_is_joined(model):
    assert _expression(_metric(model, "Order Count"), "DAX") == "COUNTROWS (\n    Sales\n)"


def test_multi_line_expressions_use_yaml_literal_blocks(bim):
    assert "COUNTROWS (\n" in convert_semantic_model_to_ossie(bim)


# --- relationships ---------------------------------------------------------


def test_active_many_to_one_relationship(model):
    rel = next(r for r in model["relationships"] if r["from"] == "Sales" and r["to"] == "Customer")
    assert rel["from_columns"] == ["CustomerKey"]
    assert rel["to_columns"] == ["CustomerKey"]


def test_one_to_many_relationship_is_flipped(model):
    rel = next(r for r in model["relationships"] if r["to"] == "Calendar")
    assert rel["from"] == "Sales"
    assert rel["from_columns"] == ["OrderDate"]
    assert rel["to_columns"] == ["Date"]


# A one-to-many relationship is authored here rather than in the shared fixture:
# the engine rejects a "one" From end unless the relationship is one-to-one, so
# sales_model.bim has to stay deployable.
def _one_to_many_bim():
    return {
        "name": "flip",
        "model": {
            "tables": [
                {"name": "Calendar", "columns": [{"name": "Date", "dataType": "dateTime"}]},
                {"name": "Sales", "columns": [{"name": "OrderDate", "dataType": "dateTime"}]},
            ],
            "relationships": [
                {
                    "name": "flipme",
                    "fromTable": "Calendar",
                    "fromColumn": "Date",
                    "toTable": "Sales",
                    "toColumn": "OrderDate",
                    "fromCardinality": "one",
                    "toCardinality": "many",
                }
            ],
        },
    }


def _flip_osi():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yaml.safe_load(convert_semantic_model_to_ossie(_one_to_many_bim()))


def test_a_one_to_many_relationship_is_flipped_to_many_to_one():
    model = _flip_osi()["semantic_model"][0]
    rel = model["relationships"][0]
    assert rel["from"] == "Sales"
    assert rel["from_columns"] == ["OrderDate"]
    assert rel["to"] == "Calendar"
    assert rel["to_columns"] == ["Date"]


def test_a_flipped_relationship_records_its_original_orientation():
    model = _flip_osi()["semantic_model"][0]
    stash = read_stash(model["relationships"][0])
    assert stash["flipped"] is True
    assert stash["fromCardinality"] == "one"


def test_a_flipped_relationship_is_exported_the_way_power_bi_wrote_it():
    bim = _one_to_many_bim()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        round_tripped = convert_ossie_to_semantic_model(_flip_osi())
    assert round_tripped["model"]["relationships"][0] == bim["model"]["relationships"][0]


def test_inactive_many_to_many_and_dangling_relationships_are_dropped(model):
    assert len(model["relationships"]) == 2
    assert all("Internal Staging" not in (r["from"], r["to"]) for r in model["relationships"])


# --- spec conformance ------------------------------------------------------


def test_output_validates_against_core_spec_schema(osi):
    jsonschema = pytest.importorskip("jsonschema")

    with open(REPO_ROOT / "core-spec" / "ossie-schema.json", encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(osi, schema)


def test_dax_is_a_spec_dialect():
    with open(REPO_ROOT / "core-spec" / "ossie-schema.json", encoding="utf-8") as fh:
        schema = json.load(fh)
    assert "DAX" in schema["$defs"]["Dialect"]["enum"]


# --- lossless preservation (POWER_BI stash) --------------------------------


def test_model_stash_preserves_excluded_tables_and_relationships(model):
    stash = read_stash(model)
    assert stash["document"]["compatibilityLevel"] == 1702
    assert stash["culture"] == "en-US"
    excluded = {t["name"] for t in stash["excludedTables"]}
    assert excluded == {"LocalDateTable_9f2a1b3c", "Internal Staging", "Time Intelligence"}
    # Inactive, many-to-many, dangling and auto-date-table relationships are kept verbatim.
    assert len(stash["excludedRelationships"]) == 4


def test_dataset_stash_preserves_partitions_and_annotations(model):
    stash = read_stash(_dataset(model, "Sales"))
    assert stash["partitions"][0]["source"]["type"] == "m"
    assert stash["annotations"]


def test_field_stash_preserves_format_string_and_display_folder(model):
    stash = read_stash(_field(_dataset(model, "Sales"), "Amount"))
    assert stash["formatString"] == "#,0.00"
    assert stash["summarizeBy"] == "sum"


def test_metric_stash_records_the_home_table(model):
    assert read_stash(_metric(model, "Total Sales"))["table"] == "Sales"


def test_relationship_stash_records_a_flipped_orientation(model):
    # The shared fixture is authored many-to-one (the only orientation the engine
    # accepts), so nothing in it flips; the flip itself is covered above.
    unflipped = next(
        r for r in model["relationships"] if r["from"] == "Sales" and r["to"] == "Calendar"
    )
    stash = read_stash(unflipped)
    assert "flipped" not in stash
    assert stash["fromCardinality"] == "many"
    assert stash["toCardinality"] == "one"


def test_a_model_without_power_bi_specifics_has_no_stash():
    bim = {
        "name": "plain",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [{"name": "C", "dataType": "string", "sourceColumn": "C"}],
                }
            ]
        },
    }
    osi = yaml.safe_load(convert_semantic_model_to_ossie(bim))
    dataset = osi["semantic_model"][0]["datasets"][0]
    assert "custom_extensions" not in dataset
    assert "custom_extensions" not in dataset["fields"][0]


def test_stash_data_is_versioned_json(model):
    ext = next(e for e in model["custom_extensions"] if e["vendor_name"] == VENDOR)
    assert json.loads(ext["data"])["_v"] == 1


# --- data types ------------------------------------------------------------


@pytest.mark.parametrize(
    "tmsl_type,expected",
    [
        ("string", "String"),
        ("int64", "Integer"),
        # "Fixed Decimal Number" is exact; "Decimal Number" is 64-bit floating point.
        ("decimal", "Decimal"),
        ("double", "Float"),
        ("boolean", "Boolean"),
        ("dateTime", "DateTime"),
    ],
)
def test_data_types_map_to_the_portable_equivalent(tmsl_type, expected):
    assert _single_field_datatype(tmsl_type) == expected


@pytest.mark.parametrize("tmsl_type", ["binary", "variant"])
def test_types_without_a_portable_equivalent_become_opaque(tmsl_type):
    with pytest.warns(UserWarning, match="no portable equivalent"):
        assert _single_field_datatype(tmsl_type) == "Opaque"


@pytest.mark.parametrize("tmsl_type", ["automatic", "unknown"])
def test_unresolved_types_omit_the_datatype(tmsl_type):
    with pytest.warns(UserWarning, match="carries no type information"):
        assert _single_field_datatype(tmsl_type) is None


def test_an_unrecognized_type_omits_the_datatype():
    with pytest.warns(UserWarning, match="unrecognized TMSL data type"):
        assert _single_field_datatype("nonsense") is None


def test_a_date_only_format_string_yields_a_date_field():
    # Power BI has no date-only data type; the format string is the only signal.
    assert _single_field_datatype("dateTime", "yyyy-mm-dd") == "Date"


def test_a_format_string_with_a_time_part_stays_a_datetime():
    assert _single_field_datatype("dateTime", "yyyy-mm-dd hh:nn") == "DateTime"


def test_a_quoted_literal_in_a_format_string_is_not_a_time_token():
    # The "h" here is display text, not an hour token.
    assert _single_field_datatype("dateTime", '"month" mmm yyyy') == "Date"


def _single_field_datatype(tmsl_type, format_string=None):
    column = {"name": "C", "dataType": tmsl_type, "sourceColumn": "C"}
    if format_string:
        column["formatString"] = format_string
    bim = {"name": "m", "model": {"tables": [{"name": "T", "columns": [column]}]}}
    osi = yaml.safe_load(convert_semantic_model_to_ossie(bim))
    return osi["semantic_model"][0]["datasets"][0]["fields"][0].get("datatype")


# --- lossy steps are reported ----------------------------------------------


def test_every_lossy_step_warns(bim):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        convert_semantic_model_to_ossie(bim)
    messages = [str(w.message) for w in caught]
    assert any("inactive relationships" in m for m in messages)
    assert any("many-to-many" in m for m in messages)
    assert any("not exported" in m for m in messages)
    assert any("Time Intelligence" in m for m in messages)


def test_a_measure_without_an_expression_is_skipped():
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {"name": "T", "columns": [], "measures": [{"name": "Empty"}]}
            ]
        },
    }
    with pytest.warns(UserWarning, match="no expression"):
        osi = yaml.safe_load(convert_semantic_model_to_ossie(bim))
    assert "metrics" not in osi["semantic_model"][0]


def test_a_conversion_can_be_asserted_lossless_by_escalating_warnings():
    bim = {
        "name": "plain",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [{"name": "C", "dataType": "string", "sourceColumn": "C"}],
                }
            ]
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        convert_semantic_model_to_ossie(bim)


@pytest.mark.parametrize("format_string", ["Short Date", "Long Date", "Medium Date"])
def test_a_named_date_format_yields_a_date_field(format_string):
    # A VBA named format is a whole-string name, not a token sequence: "Short Date"
    # contains an "h" and "Long Date" an "n", neither of which is a time token here.
    assert _single_field_datatype("dateTime", format_string) == "Date"


@pytest.mark.parametrize("format_string", ["General Date", "Short Time", "Long Time"])
def test_a_named_format_with_a_time_part_stays_a_datetime(format_string):
    assert _single_field_datatype("dateTime", format_string) == "DateTime"


@pytest.mark.parametrize("format_string", ["#,0.00", "\\$#,0.00", "0.00%"])
def test_a_numeric_format_string_is_not_a_date(format_string):
    assert _single_field_datatype("dateTime", format_string) == "DateTime"


# ---------------------------------------------------------------------------
# Reporting constructs Apache Ossie cannot represent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("construct", "phrase"),
    [
        ("roles", "row-level security roles"),
        ("perspectives", "perspectives"),
        ("cultures", "translations"),
        ("expressions", "shared Power Query expressions"),
        ("queryGroups", "Power Query group folders"),
    ],
)
def test_a_model_level_construct_ossie_cannot_hold_is_reported(bim, construct, phrase):
    with pytest.warns(UserWarning) as caught:
        build_ossie_document(bim)
    messages = [str(w.message) for w in caught]
    assert any(phrase in m and construct in m for m in messages), (
        f"{construct} was carried in the stash but never reported"
    )


def test_a_hierarchy_is_reported_against_its_table(bim):
    with pytest.warns(UserWarning) as caught:
        build_ossie_document(bim)
    assert any(
        "hierarchies" in str(w.message) and "table 'Calendar'" in str(w.message)
        for w in caught
    )


def test_a_kpi_is_reported_against_its_measure(bim):
    with pytest.warns(UserWarning) as caught:
        build_ossie_document(bim)
    assert any(
        "KPI" in str(w.message) and "measure 'Total Sales'" in str(w.message)
        for w in caught
    )


def test_a_column_construct_is_reported_against_its_column(bim):
    with pytest.warns(UserWarning) as caught:
        build_ossie_document(bim)
    messages = [str(w.message) for w in caught]
    assert any("variations" in m and "column 'Date'" in m for m in messages)
    assert any("sortByColumn" in m and "column 'FiscalYear'" in m for m in messages)


def test_an_unrepresentable_construct_still_survives_the_round_trip(bim):
    """Reporting a construct is not the same as dropping it."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document = build_ossie_document(bim)
        out = convert_ossie_to_semantic_model(document)
    assert out["model"]["roles"] == bim["model"]["roles"]
    assert out["model"]["perspectives"] == bim["model"]["perspectives"]


def test_a_clean_model_reports_nothing():
    """A model using no Power BI-only construct must convert in silence."""
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [{"name": "C", "dataType": "string", "sourceColumn": "c"}],
                    "partitions": [
                        {
                            "name": "T",
                            "mode": "import",
                            "source": {"type": "entity", "entityName": "t"},
                        }
                    ],
                }
            ]
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_ossie_document(bim)


def test_every_report_also_reaches_the_log(bim, caplog):
    """The log is the channel for callers that do not install a warning filter."""
    with caplog.at_level(logging.WARNING, logger="ossie_microsoft"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        build_ossie_document(bim)
    assert any("row-level security roles" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# CLI reporting
# ---------------------------------------------------------------------------


def test_the_cli_reports_unconvertible_constructs_on_stderr(tmp_path, capsys):
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "row-level security roles" in err
    assert "could not be converted faithfully" in err


def test_the_cli_reports_each_construct_once(tmp_path, capsys):
    """warn() feeds both a warning and a log; the CLI must not print both."""
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)])
    err = capsys.readouterr().err
    assert err.count("row-level security roles") == 1


def test_quiet_suppresses_the_report(tmp_path, capsys):
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    assert main(["import", "-q", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)]) == 0
    assert capsys.readouterr().err == ""
    assert out.exists()


def test_strict_fails_when_something_could_not_be_converted(tmp_path):
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    assert main(
        ["import", "--strict", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)]
    ) == 1
    # A non-zero exit must still mean the output was written, so a caller can inspect it.
    assert out.exists()


def test_strict_succeeds_on_a_clean_model(tmp_path):
    from ossie_microsoft.cli import main

    src = tmp_path / "clean.bim"
    src.write_text(
        json.dumps(
            {
                "name": "m",
                "model": {
                    "tables": [
                        {
                            "name": "T",
                            "columns": [
                                {"name": "C", "dataType": "string", "sourceColumn": "c"}
                            ],
                            "partitions": [
                                {
                                    "name": "T",
                                    "mode": "import",
                                    "source": {"type": "entity", "entityName": "t"},
                                }
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert main(["import", "--strict", "-i", str(src), "-o", str(tmp_path / "o.yaml")]) == 0


def test_the_flags_work_before_and_after_the_subcommand(tmp_path):
    """Nobody expects to have to put a global flag before the subcommand."""
    from ossie_microsoft.cli import main

    src = str(FIXTURES / "sales_model.bim")
    before = main(["--strict", "import", "-i", src, "-o", str(tmp_path / "a.yaml")])
    after = main(["import", "--strict", "-i", src, "-o", str(tmp_path / "b.yaml")])
    assert before == after == 1


def test_the_cli_leaves_no_handler_behind(tmp_path):
    """A library caller importing the CLI must not inherit its stderr handler."""
    from ossie_microsoft._common import LOGGER
    from ossie_microsoft.cli import main

    before = list(LOGGER.handlers)
    main(["import", "-q", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(tmp_path / "o.yaml")])
    assert LOGGER.handlers == before
