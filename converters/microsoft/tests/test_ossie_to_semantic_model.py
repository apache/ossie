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

"""Tests for the Apache Ossie -> Power BI (TMSL ``model.bim``) converter."""

import json
import warnings
from pathlib import Path

import pytest
import yaml

from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
from ossie_microsoft._common import OSSIE_VERSION, make_expression, read_stash, write_stash

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def bim_out(model):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_semantic_model(
            {"version": OSSIE_VERSION, "semantic_model": [model]}
        )


def _table(bim, name):
    return next(t for t in bim["model"]["tables"] if t["name"] == name)


def _column(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


def _annotation(target, name):
    return next(a["value"] for a in target["annotations"] if a["name"] == name)


def _convert(semantic_model):
    return convert_ossie_to_semantic_model(
        {"version": OSSIE_VERSION, "semantic_model": [semantic_model]}
    )


def _minimal(**overrides):
    semantic_model = {
        "name": "m",
        "datasets": [
            {
                "name": "T",
                "source": "dbo.t",
                "fields": [
                    {
                        "name": "C",
                        "datatype": "String",
                        "expression": make_expression("c", "ANSI_SQL"),
                    }
                ],
            }
        ],
    }
    semantic_model.update(overrides)
    return semantic_model


# --- input handling --------------------------------------------------------


def test_a_non_document_is_rejected():
    with pytest.raises(TypeError):
        convert_ossie_to_semantic_model("not a document")


def test_a_document_without_a_model_is_rejected():
    with pytest.raises(ValueError):
        convert_ossie_to_semantic_model({"version": OSSIE_VERSION})


def test_a_foreign_spec_version_warns():
    with pytest.warns(UserWarning, match="targets Apache Ossie spec"):
        convert_ossie_to_semantic_model(
            {"version": "9.9.9", "semantic_model": [_minimal()]}
        )


def test_only_the_first_model_is_converted():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal(), _minimal()]}
    with pytest.warns(UserWarning, match="single model"):
        bim = convert_ossie_to_semantic_model(document)
    assert len(bim["model"]["tables"]) == 1


def test_tmsl_is_the_default_and_can_be_selected_explicitly():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal()]}

    assert convert_ossie_to_semantic_model(document) == convert_ossie_to_semantic_model(
        document, output_format="tmsl"
    )


def test_tmdl_serializes_the_completed_tmsl_model(monkeypatch):
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal()]}
    expected = "database Model\n\n\tmodel Model\n"
    received = []

    def fake_serialize_tmdl(tmsl):
        received.append(tmsl)
        return expected

    monkeypatch.setattr("ossie_microsoft.tom.serialize_tmdl", fake_serialize_tmdl)

    assert convert_ossie_to_semantic_model(document, output_format="tmdl") == expected
    assert received[0]["model"]["tables"][0]["name"] == "T"


def test_an_unknown_output_format_is_rejected():
    with pytest.raises(ValueError, match="output_format must be 'TMSL' or 'TMDL'"):
        convert_ossie_to_semantic_model({}, output_format="xmla")


# --- structure -------------------------------------------------------------


def test_the_model_header_is_restored_from_the_stash(bim_out):
    assert bim_out["name"] == "sales_model"
    assert bim_out["compatibilityLevel"] == 1702
    assert bim_out["model"]["culture"] == "en-US"
    assert bim_out["model"]["description"] == "Retail sales semantic model"


def test_a_model_without_a_stash_gets_documented_defaults():
    bim = _convert(_minimal())
    assert bim["compatibilityLevel"] == 1702
    assert bim["model"]["culture"] == "en-US"


def test_datasets_become_tables(bim_out):
    assert {t["name"] for t in bim_out["model"]["tables"]} >= {
        "Sales",
        "Customer",
        "Calendar",
    }


def test_excluded_tables_are_restored_verbatim(bim_out, bim):
    original = next(t for t in bim["model"]["tables"] if t["name"] == "Internal Staging")
    assert _table(bim_out, "Internal Staging") == original


def test_a_primary_key_column_is_marked(bim_out):
    assert _column(_table(bim_out, "Sales"), "SalesKey")["isKey"] is True


def test_a_composite_primary_key_is_reported_as_unsupported():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["primary_key"] = ["A", "B"]
    with pytest.warns(UserWarning, match="no composite key"):
        bim = _convert(semantic_model)
    assert "isKey" not in _column(_table(bim, "T"), "C")


# --- columns ---------------------------------------------------------------


def test_a_plain_expression_becomes_a_source_column(bim_out):
    assert _column(_table(bim_out, "Sales"), "Amount")["sourceColumn"] == "amount"


def test_a_dax_expression_becomes_a_calculated_column(bim_out):
    column = _column(_table(bim_out, "Sales"), "AmountWithTax")
    assert column["type"] == "calculated"
    assert column["expression"] == "Sales[Amount] * 1.2"
    # DAX is written straight into `expression`, so annotating it would duplicate the
    # expression and make a model that merely round-tripped differ from the original.
    assert "annotations" not in column


def test_a_computed_sql_expression_uses_blank_and_annotations():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["expression"] = make_expression(
        "SUM(amount) / COUNT(*)", "ANSI_SQL"
    )
    with pytest.warns(UserWarning, match="could not be translated to DAX"):
        table = _table(_convert(semantic_model), "T")
    column = _column(table, "C")
    assert column["type"] == "calculated"
    assert column["expression"] == "BLANK()"
    assert _annotation(column, "OssieExpressionDialect") == "ANSI_SQL"
    assert _annotation(column, "OssieExpression") == "SUM(amount) / COUNT(*)"


def test_a_sql_concatenation_becomes_a_table_qualified_calculated_column():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"] = [
        {
            "name": "FirstName",
            "datatype": "String",
            "expression": make_expression("customer_name", "ANSI_SQL"),
        },
        {
            "name": "LastName",
            "datatype": "String",
            "expression": make_expression("customer_last_name", "ANSI_SQL"),
        },
        {
            "name": "FullName",
            "datatype": "String",
            "expression": make_expression(
                "customer_name || ' ' || customer_last_name", "ANSI_SQL"
            ),
        },
    ]

    column = _column(_table(_convert(semantic_model), "T"), "FullName")
    assert column["type"] == "calculated"
    assert column["expression"] == "'T'[FirstName] & \" \" & 'T'[LastName]"
    assert _annotation(column, "OssieExpressionDialect") == "ANSI_SQL"
    assert (
        _annotation(column, "OssieExpression")
        == "customer_name || ' ' || customer_last_name"
    )


def test_a_date_field_carries_a_date_only_format_string():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = "Date"
    column = _column(_table(_convert(semantic_model), "T"), "C")
    # Power BI has no date-only data type; the format string carries the intent.
    assert column["dataType"] == "dateTime"
    assert column["formatString"] == "yyyy-mm-dd"


@pytest.mark.parametrize(
    "datatype,expected", [("Time", "time-only"), ("DateTimeTz", "timezone-aware")]
)
def test_temporal_types_power_bi_lacks_are_reported(datatype, expected):
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = datatype
    with pytest.warns(UserWarning, match=expected):
        bim = _convert(semantic_model)
    assert _column(_table(bim, "T"), "C")["dataType"] == "dateTime"


def test_an_opaque_field_leaves_the_data_type_unspecified():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = "Opaque"
    with pytest.warns(UserWarning, match="'Opaque' has no Power BI equivalent"):
        bim = _convert(semantic_model)
    assert "dataType" not in _column(_table(bim, "T"), "C")


# --- partitions ------------------------------------------------------------


def test_a_preserved_partition_is_replayed(bim_out):
    partition = _table(bim_out, "Sales")["partitions"][0]
    assert partition["source"]["type"] == "m"
    assert "Sql.Database" in "\n".join(partition["source"]["expression"])


def test_yaml_text_and_source_parameters_generate_a_direct_lake_partition():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal()]}
    bim = convert_ossie_to_semantic_model(
        yaml.safe_dump(document),
        source={"workspaceId": "workspace", "itemId": "item"},
    )

    partition = _table(bim, "T")["partitions"][0]
    assert partition == {
        "name": "T",
        "mode": "directLake",
        "source": {
            "type": "entity",
            "entityName": "t",
            "schemaName": "dbo",
            "expressionSource": "DatabaseQuery",
        },
    }
    assert bim["compatibilityLevel"] == 1702
    expression = bim["model"]["expressions"][0]
    assert expression["name"] == "DatabaseQuery"
    assert "https://onelake.dfs.fabric.microsoft.com/workspace/item" in expression[
        "expression"
    ][1]


def test_a_query_source_uses_an_import_partition():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["source"] = "SELECT c FROM dbo.t;"
    with pytest.warns(UserWarning, match="Direct Lake cannot read a query source"):
        bim = _convert(semantic_model)

    partition = _table(bim, "T")["partitions"][0]
    assert partition["mode"] == "import"
    assert "Sql.Database" in "\n".join(partition["source"]["expression"])


@pytest.mark.parametrize(
    ("source", "schema", "entity"),
    [
        ("warehouse.sales.orders", "sales", "orders"),
        ('"my schema"."my table"', "my schema", "my table"),
        ("[bracketed schema].[bracketed table]", "bracketed schema", "bracketed table"),
        ("`backtick schema`.`backtick table`", "backtick schema", "backtick table"),
    ],
)
def test_a_delimited_qualified_source_keeps_its_parts(source, schema, entity):
    """A dot inside a delimiter is part of the name, not a separator."""
    semantic_model = _minimal()
    semantic_model["datasets"][0]["source"] = source
    partition = _table(_convert(semantic_model), "T")["partitions"][0]

    assert partition["mode"] == "directLake"
    assert partition["source"]["schemaName"] == schema
    assert partition["source"]["entityName"] == entity


def test_an_over_qualified_source_falls_back_to_an_import_partition():
    """More than database.schema.table is not a reference this converter can resolve."""
    semantic_model = _minimal()
    semantic_model["datasets"][0]["source"] = "a.b.c.d"
    with pytest.warns(UserWarning, match="Direct Lake cannot read a query source"):
        bim = _convert(semantic_model)

    assert _table(bim, "T")["partitions"][0]["mode"] == "import"


def test_an_unqualified_source_names_the_entity_without_inventing_a_schema():
    """Direct Lake resolves the default schema itself; guessing one could be wrong."""
    semantic_model = _minimal()
    semantic_model["datasets"][0]["source"] = "orders"
    partition = _table(_convert(semantic_model), "T")["partitions"][0]

    assert partition["source"]["entityName"] == "orders"
    assert "schemaName" not in partition["source"]


def test_a_missing_onelake_location_is_reported_rather_than_assumed():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal()]}
    with pytest.warns(UserWarning, match="placeholder ids"):
        bim = convert_ossie_to_semantic_model(document, source={"workspaceId": "w"})

    assert "expression" in bim["model"]["expressions"][0]


def test_a_non_mapping_onelake_location_is_rejected():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal()]}
    with pytest.raises(TypeError, match="workspaceId and itemId"):
        convert_ossie_to_semantic_model(document, source="workspace/item")


# --- measures --------------------------------------------------------------


def test_a_metric_returns_to_its_home_table(bim_out):
    measures = {m["name"]: m for m in _table(bim_out, "Sales")["measures"]}
    assert measures["Total Sales"]["expression"] == "SUM ( Sales[Amount] )"
    assert measures["Total Sales"]["formatString"] == "\\$#,0.00"
    # A DAX measure needs no annotation; see the calculated column test above.
    assert "annotations" not in measures["Total Sales"]


def test_a_metric_with_an_untranslatable_expression_uses_blank_and_annotations():
    semantic_model = _minimal(
        metrics=[
            {
                "name": "Revenue",
                "expression": make_expression("SUM(amount) / COUNT(*)", "ANSI_SQL"),
            }
        ]
    )
    with pytest.warns(UserWarning, match="could not be translated to DAX"):
        bim = _convert(semantic_model)
    measure = _table(bim, "T")["measures"][0]
    assert measure["expression"] == "BLANK()"
    assert _annotation(measure, "OssieExpressionDialect") == "ANSI_SQL"
    assert _annotation(measure, "OssieExpression") == "SUM(amount) / COUNT(*)"


def _metric_dax(sql, dialect="ANSI_SQL"):
    """Convert a single-metric model and return the DAX the measure was given."""
    semantic_model = _minimal(
        metrics=[{"name": "M", "expression": make_expression(sql, dialect)}]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bim = _convert(semantic_model)
    measures = _table(bim, "T").get("measures") or []
    return measures[0]["expression"] if measures else None


@pytest.mark.parametrize(
    ("sql", "dax"),
    [
        ("SUM(c)", "SUM('T'[C])"),
        ("MIN(c)", "MIN('T'[C])"),
        ("MAX(c)", "MAX('T'[C])"),
        ("COUNT(c)", "COUNTA('T'[C])"),
        # DAX renames these, so a passthrough would be silently wrong.
        ("AVG(c)", "AVERAGE('T'[C])"),
        ("STDDEV(c)", "STDEV.S('T'[C])"),
        ("STDDEV_POP(c)", "STDEV.P('T'[C])"),
        ("VARIANCE(c)", "VAR.S('T'[C])"),
        ("VAR_POP(c)", "VAR.P('T'[C])"),
        ("MEDIAN(c)", "MEDIAN('T'[C])"),
        ("COUNT(DISTINCT c)", "DISTINCTCOUNTNOBLANK('T'[C])"),
        ("COUNT(*)", "COUNTROWS('T')"),
        ("SUM(c) / COUNT(*)", "DIVIDE(SUM('T'[C]), COUNTROWS('T'))"),
        ("SUM(t.c)", "SUM('T'[C])"),
        # The field is named `C` but its source column is `c`; DAX must use the
        # model name, not the physical one.
        ("sum(C)", "SUM('T'[C])"),
    ],
)
def test_a_supported_sql_aggregate_is_translated_to_dax(sql, dax):
    assert _metric_dax(sql) == dax


def test_all_tpcds_example_metrics_translate_to_dax():
    repo_root = Path(__file__).resolve().parents[3]
    ossie = (repo_root / "examples" / "tpcds_semantic_model.yaml").read_text(
        encoding="utf-8"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bim = convert_ossie_to_semantic_model(ossie)

    measures = {
        measure["name"]: measure
        for table in bim["model"]["tables"]
        for measure in table.get("measures") or []
    }
    assert measures["total_sales"]["expression"] == (
        "SUM('store_sales'[ss_ext_sales_price])"
    )
    assert measures["total_profit"]["expression"] == (
        "SUM('store_sales'[ss_net_profit])"
    )
    assert measures["customer_lifetime_value"]["expression"] == (
        "DIVIDE(SUM('store_sales'[ss_ext_sales_price]), "
        "DISTINCTCOUNTNOBLANK('customer'[c_customer_sk]))"
    )
    assert measures["sales_by_brand"]["expression"] == (
        "SUM('store_sales'[ss_ext_sales_price])"
    )
    assert measures["store_productivity"]["expression"] == (
        "DIVIDE(SUM('store_sales'[ss_ext_sales_price]), "
        "SUM('store'[s_number_employees]))"
    )
    assert all(measure["expression"] != "BLANK()" for measure in measures.values())


def test_a_translated_sql_measure_preserves_its_source_expression():
    semantic_model = _minimal(
        metrics=[{"name": "M", "expression": make_expression("SUM(c)", "ANSI_SQL")}]
    )

    with pytest.warns(UserWarning, match="no home table recorded"):
        bim = _convert(semantic_model)
    measure = _table(bim, "T")["measures"][0]
    assert measure["expression"] == "SUM('T'[C])"
    assert _annotation(measure, "OssieExpressionDialect") == "ANSI_SQL"
    assert _annotation(measure, "OssieExpression") == "SUM(c)"


@pytest.mark.parametrize(
    "sql",
    [
        "SUM(c + c)",  # aggregate over an expression
        "SUM(DISTINCT c)",  # no DAX equivalent
        "COUNT(DISTINCT c, c)",  # multi-column DISTINCT
        "PERCENTILE_CONT(c, 0.5)",  # DAX spells this differently per interpolation
        "SUM(c) FILTER (WHERE c > 1)",
        "COUNT(c) OVER ()",
        "SUM(nonexistent)",  # does not resolve to a modelled column
        "CASE WHEN c THEN 1 END",
        "c",  # not an aggregate at all
    ],
)
def test_an_unsupported_sql_expression_is_never_guessed(sql):
    """Anything outside the curated set must use a visible placeholder, never a guess."""
    assert _metric_dax(sql) == "BLANK()"


def test_a_column_in_two_datasets_is_too_ambiguous_to_translate():
    semantic_model = _minimal(
        metrics=[{"name": "M", "expression": make_expression("SUM(c)", "ANSI_SQL")}]
    )
    # A second dataset exposing the same column name: DAX must name one table, and
    # guessing which was meant is exactly what this converter refuses to do.
    semantic_model["datasets"].append(
        {
            "name": "T2",
            "source": "dbo.t2",
            "fields": [
                {
                    "name": "C",
                    "datatype": "String",
                    "expression": make_expression("c", "ANSI_SQL"),
                }
            ],
        }
    )
    with pytest.warns(UserWarning, match="does not resolve to exactly one"):
        bim = _convert(semantic_model)
    assert _table(bim, "T")["measures"][0]["expression"] == "BLANK()"


def test_a_non_sql_dialect_is_not_parsed_as_sql():
    assert _metric_dax("SUM([Measures].[x])", "MDX") == "BLANK()"


def test_a_metric_without_a_home_table_lands_on_the_first_table():
    semantic_model = _minimal(
        metrics=[{"name": "Rows", "expression": make_expression("COUNTROWS(T)", "DAX")}]
    )
    with pytest.warns(UserWarning, match="no home table recorded"):
        bim = _convert(semantic_model)
    assert _table(bim, "T")["measures"][0]["name"] == "Rows"


# --- relationships ---------------------------------------------------------


def test_relationships_are_restored_with_their_original_orientation(bim_out, bim):
    by_name = {r["name"]: r for r in bim_out["model"]["relationships"]}
    original = {r["name"]: r for r in bim["model"]["relationships"]}
    # "e5f6a7b8" is authored many-to-one and must survive the round trip verbatim.
    assert by_name["e5f6a7b8"] == original["e5f6a7b8"]
    assert by_name["a1b2c3d4"] == original["a1b2c3d4"]


def test_a_composite_relationship_is_reported_as_unsupported():
    semantic_model = _minimal(
        relationships=[
            {
                "name": "r",
                "from": "T",
                "to": "T",
                "from_columns": ["A", "B"],
                "to_columns": ["A", "B"],
            }
        ]
    )
    with pytest.warns(UserWarning, match="single column pair"):
        bim = _convert(semantic_model)
    assert "relationships" not in bim["model"]


def test_a_relationship_to_a_missing_column_is_skipped():
    semantic_model = _minimal(
        relationships=[
            {
                "name": "r",
                "from": "T",
                "to": "T",
                "from_columns": ["C"],
                "to_columns": ["Nope"],
            }
        ]
    )
    with pytest.warns(UserWarning, match="is not in the model"):
        bim = _convert(semantic_model)
    assert "relationships" not in bim["model"]


# --- other vendors ---------------------------------------------------------


def test_another_vendors_extensions_are_reported_as_dropped():
    semantic_model = _minimal()
    write_stash_for_other_vendor(semantic_model["datasets"][0])
    with pytest.warns(UserWarning, match="vendor 'DATABRICKS'"):
        _convert(semantic_model)


def write_stash_for_other_vendor(obj):
    obj.setdefault("custom_extensions", []).append(
        {"vendor_name": "DATABRICKS", "data": "{}"}
    )


# --- round trip ------------------------------------------------------------


def test_a_model_survives_a_round_trip(bim, bim_out):
    """A ``model.bim`` converted to Apache Ossie and back is the same model."""
    original = {t["name"]: t for t in bim["model"]["tables"]}
    result = {t["name"]: t for t in bim_out["model"]["tables"]}
    assert set(original) == set(result)

    for name, table in original.items():
        assert _normalize(table) == _normalize(result[name]), name

    assert _normalize(bim["model"]["relationships"]) == _normalize(
        bim_out["model"]["relationships"]
    )


def _normalize(node):
    """Compare TMSL structurally, ignoring key order and the string/array text form."""
    if isinstance(node, dict):
        return {key: _normalize(value) for key, value in sorted(node.items())}
    if isinstance(node, list):
        if node and all(isinstance(item, str) for item in node):
            return "\n".join(node)
        return [_normalize(item) for item in node]
    return node


def test_cli_round_trip(tmp_path):
    from ossie_microsoft.cli import main

    osi_path = tmp_path / "model.yaml"
    bim_path = tmp_path / "model.bim"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(osi_path)]) == 0
        assert main(["export", "-i", str(osi_path), "-o", str(bim_path)]) == 0

    written = json.loads(bim_path.read_text(encoding="utf-8"))
    assert written["name"] == "sales_model"
    assert bim_path.read_text(encoding="utf-8").endswith("\n")


def test_cli_export_reports_errors_without_traceback(tmp_path, capsys):
    from ossie_microsoft.cli import main

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 0.2.0.dev0\n", encoding="utf-8")
    assert main(["export", "-i", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err


def test_stash_data_that_is_not_json_is_rejected():
    from ossie_microsoft import ConversionError

    semantic_model = _minimal()
    semantic_model["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": "{oops"}]
    with pytest.raises(ConversionError):
        _convert(semantic_model)


def test_a_written_stash_round_trips():
    obj = {}
    write_stash(obj, {"a": 1})
    assert yaml.safe_load(json.dumps(obj))["custom_extensions"][0]["vendor_name"] == "POWER_BI"


def test_import_export_is_stable_across_two_passes(bim):
    """Converting twice produces the same model, so the pipeline has no drift."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        first = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
        second = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(first))
        )
    assert first == second


def test_an_unknown_tmsl_property_is_preserved():
    # The stash is a deny-list, so TMSL properties this converter has never heard of
    # still survive a round trip.
    bim = {
        "name": "m",
        "someFutureDocumentProperty": 7,
        "model": {
            "someFutureModelProperty": "x",
            "tables": [
                {
                    "name": "T",
                    "someFutureTableProperty": True,
                    "columns": [
                        {
                            "name": "C",
                            "dataType": "string",
                            "sourceColumn": "c",
                            "keepUniqueRows": True,
                            "alignment": "right",
                            "displayOrdinal": 7,
                            "sourceProviderType": "bigint",
                        }
                    ],
                }
            ],
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert result["someFutureDocumentProperty"] == 7
    assert result["model"]["someFutureModelProperty"] == "x"
    table = _table(result, "T")
    assert table["someFutureTableProperty"] is True
    column = _column(table, "C")
    assert column["keepUniqueRows"] is True
    assert column["alignment"] == "right"
    assert column["displayOrdinal"] == 7
    assert column["sourceProviderType"] == "bigint"


def test_a_row_number_column_is_restored():
    # It is kept out of the vendor-neutral model but is not lost.
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [
                        {"name": "RowNumber", "type": "rowNumber", "dataType": "int64"},
                        {"name": "C", "dataType": "string", "sourceColumn": "c"},
                    ],
                }
            ]
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        osi = yaml.safe_load(convert_semantic_model_to_ossie(bim))
        result = convert_ossie_to_semantic_model(osi)
    assert [f["name"] for f in osi["semantic_model"][0]["datasets"][0]["fields"]] == ["C"]
    assert [c["name"] for c in _table(result, "T")["columns"]] == ["RowNumber", "C"]


def test_a_one_to_one_relationship_keeps_its_cardinality():
    # TMSL defaults to many-to-one, so a one-to-one has to be recorded explicitly or the
    # export would silently widen it.
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {"name": "A", "columns": [{"name": "K", "dataType": "int64", "sourceColumn": "k"}]},
                {"name": "B", "columns": [{"name": "K", "dataType": "int64", "sourceColumn": "k"}]},
            ],
            "relationships": [
                {
                    "name": "r",
                    "fromTable": "A",
                    "fromColumn": "K",
                    "toTable": "B",
                    "toColumn": "K",
                    "fromCardinality": "one",
                    "toCardinality": "one",
                }
            ],
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert result["model"]["relationships"] == [bim["model"]["relationships"][0]]


@pytest.mark.parametrize("tmsl_type", ["binary", "variant", "automatic", "unknown"])
def test_a_data_type_with_no_portable_equivalent_is_restored(tmsl_type):
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [{"name": "C", "dataType": tmsl_type, "sourceColumn": "c"}],
                }
            ]
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert _column(_table(result, "T"), "C")["dataType"] == tmsl_type


def test_a_stale_stash_cannot_contradict_an_edited_expression():
    # Someone imports a data column, then edits the Ossie field to hold DAX. The
    # preserved `type: data` must not override what the expression now implies.
    semantic_model = _minimal()
    field = semantic_model["datasets"][0]["fields"][0]
    field["expression"] = make_expression("1 + 1", "DAX")
    write_stash(field, {"type": "data", "isHidden": True})
    column = _column(_table(_convert(semantic_model), "T"), "C")
    assert column["type"] == "calculated"
    assert column["isHidden"] is True


def test_a_stale_stash_cannot_override_a_core_description():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["description"] = "current"
    write_stash(semantic_model["datasets"][0], {"description": "stale"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _table(_convert(semantic_model), "T")["description"] == "current"


def test_a_document_description_returns_to_the_document():
    # TMSL allows a description on both the document and the model; the Apache Ossie
    # model has one, so the import records which one it came from.
    bim = {
        "name": "m",
        "description": "document description",
        "model": {"tables": [{"name": "T", "columns": []}]},
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert result["description"] == "document description"
    assert "description" not in result["model"]


def test_both_descriptions_survive():
    bim = {
        "name": "m",
        "description": "document description",
        "model": {
            "description": "model description",
            "tables": [{"name": "T", "columns": []}],
        },
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert result["description"] == "document description"
    assert result["model"]["description"] == "model description"


def test_a_metric_datatype_is_not_forced_onto_a_measure():
    # A Power BI measure has no writable data type; the engine infers the result type
    # from the DAX, so emitting one would claim a property the model does not own.
    semantic_model = _minimal(
        metrics=[
            {
                "name": "Rows",
                "datatype": "Integer",
                "expression": make_expression("COUNTROWS(T)", "DAX"),
            }
        ]
    )
    with pytest.warns(UserWarning, match="infers a measure's data type"):
        bim = _convert(semantic_model)
    assert "dataType" not in _table(bim, "T")["measures"][0]


@pytest.mark.parametrize("tmsl_type", ["binary", "variant", "automatic", "unknown"])
def test_an_edited_datatype_beats_the_preserved_one(tmsl_type):
    # The stashed TMSL type is only replayed while the portable type still agrees with
    # it. Once someone edits the Apache Ossie datatype, the edit is authoritative.
    semantic_model = _minimal()
    field = semantic_model["datasets"][0]["fields"][0]
    field["datatype"] = "String"
    write_stash(field, {"dataType": tmsl_type})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _column(_table(_convert(semantic_model), "T"), "C")["dataType"] == "string"


@pytest.mark.parametrize("source_column", ["Order Date", "Sales-Amount", "123Code", "col%"])
def test_a_source_column_that_is_not_a_sql_identifier_survives(source_column):
    # A TMSL sourceColumn names a column in the source query, which may be spelled in
    # ways SQL would need to quote.
    bim = {
        "name": "m",
        "model": {
            "tables": [
                {
                    "name": "T",
                    "columns": [
                        {"name": "C", "dataType": "string", "sourceColumn": source_column}
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
    with warnings.catch_warnings():
        # Nothing about this model is lossy, so any warning at all is a failure.
        warnings.simplefilter("error")
        result = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
    assert _column(_table(result, "T"), "C")["sourceColumn"] == source_column


def test_an_edited_expression_beats_a_preserved_source_column():
    semantic_model = _minimal()
    field = semantic_model["datasets"][0]["fields"][0]
    field["expression"] = make_expression("other_column", "ANSI_SQL")
    write_stash(field, {"sourceColumn": "Order Date"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        column = _column(_table(_convert(semantic_model), "T"), "C")
    assert column["sourceColumn"] == "other_column"


def test_a_stash_written_by_a_newer_converter_is_refused():
    """Replaying a payload we may misunderstand is worse than failing loudly."""
    from ossie_microsoft._common import STASH_VERSION, VENDOR, ConversionError

    obj = {
        "custom_extensions": [
            {"vendor_name": VENDOR, "data": json.dumps({"_v": STASH_VERSION + 1})}
        ]
    }
    with pytest.raises(ConversionError, match="newer converter"):
        read_stash(obj)


def test_a_stash_with_a_non_integer_version_is_refused():
    from ossie_microsoft._common import VENDOR, ConversionError

    obj = {
        "custom_extensions": [
            {"vendor_name": VENDOR, "data": json.dumps({"_v": "1"})}
        ]
    }
    with pytest.raises(ConversionError, match="non-integer version"):
        read_stash(obj)


def test_a_stash_this_converter_understands_is_replayed():
    from ossie_microsoft._common import STASH_VERSION, VENDOR

    obj = {
        "custom_extensions": [
            {
                "vendor_name": VENDOR,
                "data": json.dumps({"_v": STASH_VERSION, "lineageTag": "abc"}),
            }
        ]
    }
    assert read_stash(obj) == {"lineageTag": "abc"}


# ---------------------------------------------------------------------------
# Reporting Apache Ossie constructs Power BI cannot hold
# ---------------------------------------------------------------------------


def test_an_ossie_field_construct_power_bi_cannot_hold_is_reported():
    """These are dropped outright, so silence would be real data loss."""
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["label"] = "something"
    with pytest.warns(UserWarning, match="label"):
        _convert(semantic_model)


def test_ai_context_is_saved_as_annotations_on_semantic_model_objects():
    semantic_model = _minimal()
    semantic_model["ai_context"] = "model level"
    dataset = semantic_model["datasets"][0]
    dataset["ai_context"] = {"instructions": "dataset level"}
    dataset["fields"][0]["ai_context"] = "field level"
    semantic_model["metrics"] = [
        {
            "name": "Rows",
            "expression": make_expression("COUNTROWS(T)", "DAX"),
            "ai_context": "metric level",
        }
    ]

    with pytest.warns(UserWarning, match="no home table recorded"):
        bim = _convert(semantic_model)
    table = _table(bim, "T")
    assert _annotation(bim["model"], "OssieAIContext") == "model level"
    assert _annotation(table, "OssieAIContext") == '{"instructions": "dataset level"}'
    assert _annotation(_column(table, "C"), "OssieAIContext") == "field level"
    assert _annotation(table["measures"][0], "OssieAIContext") == "metric level"
