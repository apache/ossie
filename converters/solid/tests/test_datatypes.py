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

"""Warehouse type vocabulary: dialect inference and datatype mapping."""

import pytest
import yaml
from conftest import convert_quietly, field_of, fixture, model_of, stash_of

from ossie_solid import ConversionError, convert_solid_to_ossie
from ossie_solid.datatypes import (
    infer_dialect,
    is_solid_fact_type,
    normalize_dialect,
    parse_type,
    to_ossie_datatype,
    to_raw_type,
)


@pytest.mark.parametrize(
    ("raw", "base", "params"),
    [
        ("NUMBER(38,0)", "NUMBER", ["38", "0"]),
        ("number(7, 2)", "NUMBER", ["7", "2"]),
        ("TEXT", "TEXT", []),
        ("VARCHAR(16777216)", "VARCHAR", ["16777216"]),
        ("ARRAY<STRING>", "ARRAY", []),
        ("MAP<STRING, STRING>", "MAP", []),
        ("TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH TIME ZONE", []),
        ("", None, []),
        (None, None, []),
    ],
)
def test_a_raw_type_splits_into_a_base_name_and_parameters(raw, base, params):
    assert parse_type(raw) == (base, params)


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        (["NUMBER(38,0)", "TEXT", "TIMESTAMP_NTZ"], "SNOWFLAKE"),
        (["LONG", "STRING", "MAP<STRING, STRING>"], "DATABRICKS"),
        (["INT64", "STRING", "BOOL", "FLOAT64"], "BIGQUERY"),
        # A STRUCT column decides nothing on its own, but must not stop the
        # surrounding vocabulary from deciding.
        (["STRUCT<a: INT>", "LONG", "STRING"], "DATABRICKS"),
        (["STRUCT<a: INT64>", "RECORD", "INT64"], "BIGQUERY"),
        # Names every warehouse shares carry no signal.
        (["STRING", "DATE", "BOOLEAN", "DECIMAL"], "ANSI_SQL"),
        ([], "ANSI_SQL"),
        # A tie between two vocabularies is not a decision.
        (["NUMBER", "INT64"], "ANSI_SQL"),
    ],
)
def test_the_warehouse_is_inferred_from_its_distinctive_type_names(types, expected):
    dialect, confident = infer_dialect(types)
    assert dialect == expected
    assert confident is (expected != "ANSI_SQL")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("tpcds_solid.yaml", "SNOWFLAKE"),
        ("databricks_solid.yaml", "DATABRICKS"),
        ("bigquery_solid.yaml", "BIGQUERY"),
    ],
)
def test_each_fixture_resolves_to_its_own_warehouse(name, expected):
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture(name))
    assert stash_of(model_of(ossie))["dialect"] == expected


@pytest.mark.parametrize("struct", ["STRUCT<a: INT, b: STRING>", "STRUCT"])
def test_a_struct_column_alone_does_not_identify_a_warehouse(struct):
    """STRUCT is Databricks and BigQuery both, and the angle-bracket params that
    would tell them apart are stripped by `parse_type` before the vote is counted --
    so it belongs to neither marker set. Regression: it was once listed as
    `STRUCT<>` under Databricks, a string `parse_type` can never return, which left
    BigQuery holding the only STRUCT marker and a Databricks model claiming BIGQUERY.
    """
    assert infer_dialect([struct, "STRING", "DATE"]) == ("ANSI_SQL", False)


def test_an_unrecognizable_vocabulary_falls_back_to_ansi_with_a_warning():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    for table in solid["semantic_model"]["tables"]:
        for column in table["dimensions"] + table["facts"]:
            column["type"] = "STRING"
    ossie, warnings_ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert any("could not infer the source warehouse" in w for w in warnings_)
    assert stash_of(model_of(ossie))["dialect"] == "ANSI_SQL"


def test_an_explicit_dialect_suppresses_inference():
    ossie, warnings_ = convert_quietly(
        convert_solid_to_ossie, fixture("databricks_solid.yaml"), dialect="ANSI_SQL"
    )
    assert stash_of(model_of(ossie))["dialect"] == "ANSI_SQL"
    assert not any("could not infer" in w for w in warnings_)


def test_an_unsupported_dialect_is_rejected():
    with pytest.raises(ConversionError, match="Unsupported dialect"):
        convert_solid_to_ossie(fixture("databricks_solid.yaml"), dialect="POSTGRES")


def test_dialect_names_are_normalized_case_insensitively():
    assert normalize_dialect("snowflake") == "SNOWFLAKE"


@pytest.mark.parametrize(
    ("raw", "dialect", "expected"),
    [
        # A declared scale of zero is an integer, which is how Snowflake stores ids.
        ("NUMBER(38,0)", "SNOWFLAKE", "Integer"),
        ("NUMBER(7,2)", "SNOWFLAKE", "Decimal"),
        ("NUMBER", "SNOWFLAKE", "Integer"),
        ("NUMERIC(10,4)", "BIGQUERY", "Decimal"),
        ("TEXT", "SNOWFLAKE", "String"),
        ("STRING", "DATABRICKS", "String"),
        ("LONG", "DATABRICKS", "Integer"),
        ("INT64", "BIGQUERY", "Integer"),
        ("FLOAT64", "BIGQUERY", "Float"),
        ("DOUBLE", "DATABRICKS", "Float"),
        ("BOOL", "BIGQUERY", "Boolean"),
        ("BOOLEAN", "SNOWFLAKE", "Boolean"),
        ("DATE", "SNOWFLAKE", "Date"),
        # TIMESTAMP means different things per warehouse.
        ("TIMESTAMP", "SNOWFLAKE", "DateTime"),
        ("TIMESTAMP", "DATABRICKS", "DateTimeTz"),
        ("TIMESTAMP", "BIGQUERY", "DateTimeTz"),
        ("TIMESTAMP_NTZ", "SNOWFLAKE", "DateTime"),
        ("TIMESTAMP_NTZ", "DATABRICKS", "DateTime"),
        ("TIMESTAMP_TZ", "SNOWFLAKE", "DateTimeTz"),
        ("DATETIME", "BIGQUERY", "DateTime"),
        # Known, but outside the portable vocabulary.
        ("VARIANT", "SNOWFLAKE", "Opaque"),
        ("MAP<STRING, STRING>", "DATABRICKS", "Opaque"),
        ("ARRAY", "SNOWFLAKE", "Opaque"),
        # Unknown: the spec says to omit datatype rather than guess.
        ("SOMETHING_ELSE", "SNOWFLAKE", None),
        ("", "SNOWFLAKE", None),
        (None, "SNOWFLAKE", None),
    ],
)
def test_warehouse_types_map_onto_the_portable_datatypes(raw, dialect, expected):
    assert to_ossie_datatype(raw, dialect) == expected


def test_an_unmappable_type_omits_the_datatype_and_warns():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    solid["semantic_model"]["tables"][0]["dimensions"][2]["type"] = "SOMETHING_ELSE"
    ossie, warnings_ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert any("SOMETHING_ELSE" in w for w in warnings_)
    field = field_of(model_of(ossie), "orders", "status")
    assert "datatype" not in field
    # The raw name is still kept, so the export is unaffected.
    assert stash_of(field)["type"] == "SOMETHING_ELSE"


@pytest.mark.parametrize(
    ("datatype", "dialect", "expected"),
    [
        ("String", "SNOWFLAKE", "TEXT"),
        ("String", "DATABRICKS", "STRING"),
        ("String", "BIGQUERY", "STRING"),
        ("String", "ANSI_SQL", "VARCHAR"),
        ("Integer", "BIGQUERY", "INT64"),
        ("Float", "DATABRICKS", "DOUBLE"),
        ("DateTimeTz", "SNOWFLAKE", "TIMESTAMP_TZ"),
        (None, "SNOWFLAKE", None),
    ],
)
def test_portable_datatypes_map_back_to_a_representative_warehouse_type(
    datatype, dialect, expected
):
    assert to_raw_type(datatype, dialect) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NUMBER(38,0)", True),
        ("DECIMAL(10,2)", True),
        ("DOUBLE", True),
        ("INT64", True),
        ("LONG", True),
        ("TEXT", False),
        ("STRING", False),
        ("DATE", False),
        ("BOOLEAN", False),
        (None, False),
    ],
)
def test_solids_own_fact_type_rule_is_mirrored(raw, expected):
    assert is_solid_fact_type(raw) is expected
