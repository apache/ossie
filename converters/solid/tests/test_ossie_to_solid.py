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

"""Apache Ossie -> Solid semantic model."""

import warnings

import pytest
import yaml
from conftest import (
    by_name,
    convert_quietly,
    example,
    fixture,
    solid_model_of,
)

from ossie_solid import ConversionError, convert_ossie_to_solid, convert_solid_to_ossie


@pytest.fixture(scope="module")
def from_fixture():
    """The TPC-DS Apache Ossie fixture exported back to Solid."""
    solid, _ = convert_quietly(convert_ossie_to_solid, fixture("tpcds_ossie.yaml"))
    return solid_model_of(solid)


@pytest.fixture(scope="module")
def from_example():
    """The repository's hand-authored TPC-DS model exported to Solid.

    It carries no SOLID stash, so this exercises every fallback path.
    """
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    return solid_model_of(solid)


def test_key_order_matches_solids_own_export_template(from_fixture):
    assert list(from_fixture) == [
        "name",
        "business_context",
        "model_llm_description",
        "tables",
        "metrics",
        "relationships",
        "example_queries",
        "benchmark_questions",
    ]


def test_table_key_order_matches_solids_own_export_template(from_fixture):
    assert list(by_name(from_fixture["tables"])["tpcds.public.store_sales"]) == [
        "name",
        "description",
        "manual_description",
        "synonyms",
        "primary_key",
        "quality_rank",
        "indexes",
        "dimensions",
        "facts",
    ]


# --- datasets ---------------------------------------------------------------------


def test_the_dataset_source_becomes_the_table_name(from_fixture):
    assert "tpcds.public.store_sales" in by_name(from_fixture["tables"])


def test_a_composite_primary_key_is_rejoined_into_one_scalar(from_fixture):
    table = by_name(from_fixture["tables"])["tpcds.public.store_sales"]
    assert table["primary_key"] == "ss_item_sk, ss_ticket_number"


def test_quality_rank_is_always_emitted_even_when_unknown(from_example):
    # The repository example has no SOLID stash, so no rank is known.
    assert all(t["quality_rank"] == "" for t in from_example["tables"])


def test_facts_is_always_emitted_even_when_empty(from_fixture):
    table = by_name(from_fixture["tables"])["tpcds.public.date_dim"]
    assert table["facts"] == []


def test_unique_keys_are_dropped_with_a_warning():
    _, warnings_ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    assert any("unique_keys have no Solid equivalent" in w for w in warnings_)


def test_foreign_vendor_extensions_are_dropped_with_a_warning():
    _, warnings_ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    assert any("DBT, SALESFORCE" in w for w in warnings_)


# --- fields -----------------------------------------------------------------------


def test_the_dimension_block_decides_the_fact_dimension_split(from_fixture):
    table = by_name(from_fixture["tables"])["tpcds.public.store_sales"]
    assert "ss_customer_sk" in by_name(table["dimensions"])
    assert "ss_net_profit" in by_name(table["facts"])


def test_without_dimension_metadata_the_split_falls_back_to_solids_type_rule():
    # An Apache Ossie model whose fields carry no `dimension` block: numeric fields
    # become facts and the rest dimensions, which is how Solid itself splits them.
    ossie = yaml.safe_load(example("tpcds_semantic_model.yaml"))
    for dataset in ossie["semantic_model"][0]["datasets"]:
        for field in dataset["fields"]:
            field.pop("dimension", None)
    solid, _ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    table = by_name(solid_model_of(solid)["tables"])["tpcds.public.store_sales"]
    assert "ss_quantity" in by_name(table["facts"])
    assert "ss_customer_sk" in by_name(table["facts"])  # Integer -> fact
    table = by_name(solid_model_of(solid)["tables"])["tpcds.public.customer"]
    assert "c_email_address" in by_name(table["dimensions"])  # String -> dimension


def test_the_stashed_raw_type_wins_over_the_portable_datatype(from_fixture):
    table = by_name(from_fixture["tables"])["tpcds.public.store_sales"]
    assert by_name(table["facts"])["ss_sales_price"]["type"] == "NUMBER(7,2)"


def test_without_a_stash_the_type_is_derived_from_the_datatype(from_example):
    table = by_name(from_example["tables"])["tpcds.public.store_sales"]
    # The example is ANSI_SQL, so the ANSI type names are used.
    assert by_name(table["facts"])["ss_sales_price"]["type"] == "DECIMAL"


def test_a_field_with_no_datatype_gets_an_empty_type_and_a_warning():
    solid, warnings_ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    table = by_name(solid_model_of(solid)["tables"])["tpcds.public.date_dim"]
    assert by_name(table["dimensions"])["d_quarter_name"]["type"] == ""
    assert any("d_quarter_name" in w and "no datatype" in w for w in warnings_)


def test_an_expression_only_fact_round_trips_as_an_expression(from_fixture):
    table = by_name(from_fixture["tables"])["tpcds.public.store_sales"]
    fact = by_name(table["facts"])["ss_discount_pct"]
    assert fact["expression"] == "1 - (ss_sales_price / NULLIF(ss_list_price, 0))"
    assert "type" not in fact


def test_a_computed_dimension_drops_its_expression_with_a_warning():
    _, warnings_ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    assert any("customer_full_name" in w and "computed field" in w for w in warnings_)


def test_a_field_label_is_dropped_with_a_warning():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["semantic_model"][0]["datasets"][0]["fields"][0]["label"] = "filter"
    _, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("label 'filter'" in w for w in warnings_)


# --- metrics ----------------------------------------------------------------------


def test_a_qualified_metric_is_written_back_with_bare_columns(from_fixture):
    metric = by_name(from_fixture["metrics"])["TOTAL_SALES"]
    assert metric["expression"] == "SUM(ss_ext_sales_price)"
    assert metric["tables"] == ["tpcds.public.store_sales"]


def test_the_owning_tables_are_recovered_from_the_expression_without_a_stash(
    from_example,
):
    metric = by_name(from_example["metrics"])["customer_lifetime_value"]
    assert metric["tables"] == ["tpcds.public.store_sales", "tpcds.public.customer"]


def test_metric_key_order_matches_solids_own_export_template(from_fixture):
    assert list(by_name(from_fixture["metrics"])["TOTAL_SALES"]) == [
        "name",
        "description",
        "expression",
        "synonyms",
        "tables",
    ]


# --- relationships ----------------------------------------------------------------


def test_a_flipped_relationship_is_restored_to_solids_left_right_order(from_fixture):
    relationship = from_fixture["relationships"][0]
    assert relationship["left_table"] == "tpcds.public.date_dim"
    assert relationship["right_table"] == "tpcds.public.store_sales"


def test_relationship_order_is_preserved(from_fixture):
    assert [r["right_table"] for r in from_fixture["relationships"]] == [
        "tpcds.public.store_sales",
        "tpcds.public.customer",
        "tpcds.public.item",
        "tpcds.public.store",
    ]


def test_a_relationship_naming_an_undeclared_dataset_is_rejected():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["semantic_model"][0]["relationships"][0]["to"] = "nope"
    with pytest.raises(ConversionError, match="not declared in 'datasets'"):
        convert_ossie_to_solid(yaml.safe_dump(ossie))


# --- dialect selection ------------------------------------------------------------


def test_the_dialect_recorded_at_import_is_reused(from_fixture):
    # NUMBER(7,2) is a Snowflake type name; reading it back proves the SNOWFLAKE
    # dialect recorded in the stash was used.
    table = by_name(from_fixture["tables"])["tpcds.public.store_sales"]
    assert by_name(table["facts"])["ss_sales_price"]["type"] == "NUMBER(7,2)"


def test_an_explicit_dialect_overrides_the_stash():
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture("databricks_solid.yaml"))
    solid, _ = convert_quietly(convert_ossie_to_solid, ossie, dialect="DATABRICKS")
    assert solid_model_of(solid)["name"] == "orders_analytics"


def test_forcing_a_dialect_the_model_does_not_carry_is_an_error():
    # The TPC-DS fixture is SNOWFLAKE-only, with no ANSI_SQL fallback to read.
    with pytest.raises(ConversionError, match="no DATABRICKS or ANSI_SQL expression"):
        convert_ossie_to_solid(fixture("tpcds_ossie.yaml"), dialect="DATABRICKS")


def test_an_unsupported_dialect_is_rejected():
    with pytest.raises(ConversionError, match="Unsupported dialect"):
        convert_ossie_to_solid(fixture("tpcds_ossie.yaml"), dialect="ORACLE")


def test_a_missing_dialect_expression_falls_back_to_ansi_with_a_warning():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    field = ossie["semantic_model"][0]["datasets"][0]["fields"][0]
    field["expression"]["dialects"] = [
        {"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}
    ]
    _, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("no SNOWFLAKE expression" in w for w in warnings_)


# --- input validation --------------------------------------------------------------


def test_a_wrong_spec_version_is_rejected():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["version"] = "0.1.0"
    with pytest.raises(ConversionError, match="Unsupported Apache Ossie version"):
        convert_ossie_to_solid(yaml.safe_dump(ossie))


def test_a_solid_document_is_rejected():
    with pytest.raises(ConversionError, match="Unsupported Apache Ossie version"):
        convert_ossie_to_solid(fixture("tpcds_solid.yaml"))


def test_extra_semantic_models_are_dropped_with_a_warning():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    second = dict(ossie["semantic_model"][0])
    second["name"] = "second_model"
    ossie["semantic_model"].append(second)
    solid, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("holds 2 semantic models" in w for w in warnings_)
    assert solid_model_of(solid)["name"] == "tpcds_retail_model"


# --- constructs Solid's format cannot hold -----------------------------------------


def test_a_metric_datatype_is_dropped_with_a_warning():
    """Solid types a metric by evaluating its formula, so a declared type has no slot."""
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["semantic_model"][0]["metrics"][0]["datatype"] = "Decimal"
    solid, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("datatype 'Decimal' has no Solid equivalent" in w for w in warnings_)
    assert "datatype" not in solid_model_of(solid)["metrics"][0]


def test_a_relationship_annotation_is_dropped_with_a_warning():
    """A Solid relationship carries only its tables and join keys."""
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["semantic_model"][0]["relationships"][0]["ai_context"] = {
        "instructions": "join only on settled sales",
        "synonyms": ["sold on"],
    }
    _, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    dropped = [w for w in warnings_ if "no Solid equivalent" in w and "relationship" in w]
    assert len(dropped) == 1
    assert "ai_context.instructions" in dropped[0]
    assert "ai_context.synonyms" in dropped[0]


def test_the_one_to_one_note_import_writes_is_not_reported_as_a_loss():
    """That note is this converter's own marker, not a user annotation.

    Import adds it where Apache Ossie's from/to direction is arbitrary; dropping it on
    the way back loses nothing, so it must not be reported as a dropped annotation.
    """
    solid = yaml.safe_load(fixture("tpcds_solid.yaml"))
    # Make both ends of the first join unique on their join columns -> a one-to-one.
    join = solid["semantic_model"]["relationships"][0]
    tables = {t["name"]: t for t in solid["semantic_model"]["tables"]}
    tables[join["left_table"]]["primary_key"] = ", ".join(join["join_keys"]["left"])
    tables[join["right_table"]]["primary_key"] = ", ".join(join["join_keys"]["right"])

    ossie, _ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    annotated = [
        r for r in yaml.safe_load(ossie)["semantic_model"][0]["relationships"]
        if (r.get("ai_context") or {}).get("instructions")
    ]
    assert annotated, "expected import to annotate the one-to-one"

    _, warnings_ = convert_quietly(convert_ossie_to_solid, ossie)
    assert not [w for w in warnings_ if "ai_context.instructions" in w], warnings_


# --- non-SQL dialects --------------------------------------------------------------


def test_a_non_sql_dialect_never_becomes_the_resolved_dialect():
    """MDX/TABLEAU/MAQL are in Apache Ossie's enum but are not SQL.

    Resolving to one would hand a non-SQL formula to the expression rewriter, and to
    Solid, as though it were SQL. The ANSI_SQL form is read instead.
    """
    ossie = {
        "version": "0.2.0.dev0",
        "semantic_model": [{
            "name": "m",
            "datasets": [{
                "name": "a",
                "source": "db.s.a",
                "fields": [{
                    "name": "amt",
                    "datatype": "Decimal",
                    "expression": {"dialects": [
                        {"dialect": "TABLEAU", "expression": "[Amount]"},
                        {"dialect": "ANSI_SQL", "expression": "amt"},
                    ]},
                }],
            }],
        }],
    }
    solid, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("not SQL this converter can read" in w for w in warnings_)
    column = solid_model_of(solid)["tables"][0]["facts"][0]
    # The ANSI_SQL form was read, so the column is a plain reference to itself and no
    # Tableau formula leaked into the Solid model.
    assert "expression" not in column
    assert column["name"] == "amt"


def test_a_non_sql_dialect_with_no_ansi_form_is_an_error():
    """Better a clear failure than a Tableau formula in a SQL field."""
    ossie = {
        "version": "0.2.0.dev0",
        "semantic_model": [{
            "name": "m",
            "datasets": [{
                "name": "a",
                "source": "db.s.a",
                "fields": [{
                    "name": "amt",
                    "expression": {"dialects": [
                        {"dialect": "MDX", "expression": "[Measures].[Amt]"},
                    ]},
                }],
            }],
        }],
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ConversionError, match="no ANSI_SQL expression"):
            convert_ossie_to_solid(yaml.safe_dump(ossie))


def test_a_hand_edited_stash_dialect_is_rejected():
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    model = ossie["semantic_model"][0]
    for ext in model["custom_extensions"]:
        if ext["vendor_name"] == "SOLID":
            ext["data"] = ext["data"].replace("SNOWFLAKE", "MAQL")
    with pytest.raises(ConversionError, match="Unsupported dialect"):
        convert_ossie_to_solid(yaml.safe_dump(ossie))


# --- spec versions -----------------------------------------------------------------


def test_the_released_0_1_1_spec_is_read_with_a_warning():
    """0.1.1 is the only released spec version, so models in the wild declare it.

    A 0.1.1 document is a 0.2.0.dev0 document minus additive fields, so it is read
    rather than rejected -- but the version gap is reported, since the missing
    `datatype` is what leaves a column's Solid `type` empty.
    """
    ossie = yaml.safe_load(fixture("tpcds_ossie.yaml"))
    ossie["version"] = "0.1.1"
    solid, warnings_ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(ossie))
    assert any("declares Apache Ossie v0.1.1" in w for w in warnings_)
    assert any("predates the `datatype` field" in w for w in warnings_)
    # The model still converts in full: the stashed raw types carry the column types.
    assert solid_model_of(solid)["tables"][0]["facts"][0]["type"]


def test_reading_an_older_spec_still_writes_the_current_one():
    solid = yaml.safe_load(fixture("tpcds_solid.yaml"))
    ossie, _ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    aged = yaml.safe_load(ossie)
    aged["version"] = "0.1.1"
    back, _ = convert_quietly(convert_ossie_to_solid, yaml.safe_dump(aged))
    again, _ = convert_quietly(convert_solid_to_ossie, back)
    assert yaml.safe_load(again)["version"] == "0.2.0.dev0"
