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

"""Solid semantic model -> Apache Ossie."""

import pytest
import yaml
from conftest import (
    by_name,
    convert_quietly,
    dataset_of,
    expression_of,
    field_of,
    fixture,
    model_of,
    stash_of,
)

from ossie_solid import ConversionError, convert_solid_to_ossie


@pytest.fixture(scope="module")
def tpcds():
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    return model_of(ossie)


def test_output_matches_the_committed_fixture():
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    assert yaml.safe_load(ossie) == yaml.safe_load(fixture("tpcds_ossie.yaml"))


def test_output_validates_against_the_ossie_schema(assert_valid_ossie):
    for name in ("tpcds_solid.yaml", "databricks_solid.yaml", "bigquery_solid.yaml"):
        ossie, _ = convert_quietly(convert_solid_to_ossie, fixture(name))
        assert_valid_ossie(ossie)


def test_document_declares_the_spec_version():
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    document = yaml.safe_load(ossie)
    assert document["version"] == "0.2.0.dev0"
    assert len(document["semantic_model"]) == 1


# --- datasets ---------------------------------------------------------------------


def test_table_fqn_becomes_the_source_and_its_last_part_the_name(tpcds):
    dataset = dataset_of(tpcds, "store_sales")
    assert dataset["source"] == "tpcds.public.store_sales"


def test_dataset_names_are_disambiguated_when_the_last_part_collides():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    # Two tables named `orders` in different schemas: the first keeps the short name,
    # the second widens to schema_table rather than colliding.
    solid["semantic_model"]["tables"][1]["name"] = "main.returns.orders"
    solid["semantic_model"]["relationships"][0]["right_table"] = "main.returns.orders"
    solid["semantic_model"]["tables"][1]["primary_key"] = "customer_id"
    ossie, _ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    names = [d["name"] for d in model_of(ossie)["datasets"]]
    assert names == ["orders", "returns_orders"]


def test_composite_primary_key_scalar_is_split_into_columns(tpcds):
    assert dataset_of(tpcds, "store_sales")["primary_key"] == [
        "ss_item_sk",
        "ss_ticket_number",
    ]


def test_single_primary_key_becomes_a_one_element_list(tpcds):
    assert dataset_of(tpcds, "customer")["primary_key"] == ["c_customer_sk"]


def test_table_descriptions_split_across_description_and_ai_context(tpcds):
    dataset = dataset_of(tpcds, "store_sales")
    assert dataset["description"].startswith("Fact table containing all store sales")
    assert dataset["ai_context"]["instructions"].startswith("Grain is (ss_item_sk")


def test_table_synonyms_become_ai_context_synonyms(tpcds):
    assert "POS data" in dataset_of(tpcds, "store_sales")["ai_context"]["synonyms"]


def test_quality_rank_and_indexes_are_stashed(tpcds):
    stash = stash_of(dataset_of(tpcds, "store_sales"))
    assert stash["quality_rank"] == "high"
    assert stash["indexes"] == ["ss_sold_date_sk", "ss_customer_sk"]


def test_a_table_without_indexes_gets_no_index_stash(tpcds):
    assert "indexes" not in stash_of(dataset_of(tpcds, "customer"))


# --- fields -----------------------------------------------------------------------


def test_dimensions_and_facts_merge_into_one_field_list(tpcds):
    fields = by_name(dataset_of(tpcds, "store_sales")["fields"])
    assert "ss_customer_sk" in fields  # a dimension
    assert "ss_net_profit" in fields  # a fact


def test_a_dimension_carries_the_dimension_block_and_a_fact_does_not(tpcds):
    assert field_of(tpcds, "store_sales", "ss_customer_sk")["dimension"] == {
        "is_time": False
    }
    assert "dimension" not in field_of(tpcds, "store_sales", "ss_net_profit")


def test_a_temporal_dimension_is_marked_is_time(tpcds):
    assert field_of(tpcds, "store_sales", "ss_sold_at")["dimension"] == {"is_time": True}
    assert field_of(tpcds, "date_dim", "d_date")["dimension"] == {"is_time": True}


def test_a_column_expression_is_its_own_name(tpcds):
    assert expression_of(field_of(tpcds, "customer", "c_email_address")) == (
        "c_email_address"
    )


def test_the_raw_warehouse_type_is_stashed_alongside_the_portable_datatype(tpcds):
    field = field_of(tpcds, "store_sales", "ss_sales_price")
    assert field["datatype"] == "Decimal"
    assert stash_of(field)["type"] == "NUMBER(7,2)"


def test_sample_values_are_stashed(tpcds):
    assert stash_of(field_of(tpcds, "item", "i_category"))["sample_values"] == [
        "Electronics",
        "Home",
        "Sports",
    ]


def test_an_expression_only_fact_keeps_its_expression_and_is_marked_a_metric(tpcds):
    field = field_of(tpcds, "store_sales", "ss_discount_pct")
    assert expression_of(field) == "1 - (ss_sales_price / NULLIF(ss_list_price, 0))"
    assert stash_of(field) == {"role": "metric"}
    assert "datatype" not in field


def test_a_column_description_pair_maps_to_description_and_ai_context(tpcds):
    field = field_of(tpcds, "store_sales", "ss_sales_price")
    assert field["description"] == "Sales price per unit."
    assert field["ai_context"]["instructions"].startswith("Per-unit price.")


# --- relationships ----------------------------------------------------------------


def test_direction_follows_the_primary_key_not_the_left_right_order(tpcds):
    # date_dim is the left table in the fixture, but its primary key covers the join,
    # so it is the one side and store_sales must be the many side.
    relationship = by_name(tpcds["relationships"])["store_sales_to_date_dim"]
    assert relationship["from"] == "store_sales"
    assert relationship["to"] == "date_dim"
    assert relationship["from_columns"] == ["ss_sold_date_sk"]
    assert relationship["to_columns"] == ["d_date_sk"]
    assert stash_of(relationship)["flipped"] is True


def test_a_relationship_already_in_many_to_one_order_is_not_flipped(tpcds):
    relationship = by_name(tpcds["relationships"])["store_sales_to_customer"]
    assert relationship["from"] == "store_sales"
    assert "flipped" not in stash_of(relationship)


def test_relationships_keep_their_declaration_order(tpcds):
    assert [r["name"] for r in tpcds["relationships"]] == [
        "store_sales_to_date_dim",
        "store_sales_to_customer",
        "store_sales_to_item",
        "store_sales_to_store",
    ]


def test_only_a_flipped_relationship_carries_a_stash(tpcds):
    stashes = [stash_of(r) for r in tpcds["relationships"]]
    assert stashes == [{"flipped": True}, {}, {}, {}]


def test_undetermined_cardinality_warns_and_keeps_solid_order():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    del solid["semantic_model"]["tables"][1]["primary_key"]
    ossie, warnings_ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert any("could not be determined" in w for w in warnings_)
    relationship = model_of(ossie)["relationships"][0]
    assert (relationship["from"], relationship["to"]) == ("orders", "customers")


def test_a_one_to_one_relationship_is_annotated():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    # Make the join unique on both ends.
    solid["semantic_model"]["tables"][0]["primary_key"] = "customer_id"
    ossie, _ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    relationship = model_of(ossie)["relationships"][0]
    assert "One-to-one" in relationship["ai_context"]["instructions"]


def test_a_relationship_naming_an_unknown_table_is_dropped_with_a_warning():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    solid["semantic_model"]["relationships"][0]["right_table"] = "main.sales.missing"
    ossie, warnings_ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert any("main.sales.missing" in w for w in warnings_)
    assert "relationships" not in model_of(ossie)


def test_mismatched_join_key_counts_are_rejected():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    solid["semantic_model"]["relationships"][0]["join_keys"]["right"] = [
        "customer_id",
        "region",
    ]
    with pytest.raises(ConversionError, match="correspond positionally"):
        convert_solid_to_ossie(yaml.safe_dump(solid))


# --- metrics ----------------------------------------------------------------------


def test_a_single_table_metric_is_qualified_with_its_dataset(tpcds):
    metrics = by_name(tpcds["metrics"])
    assert expression_of(metrics["TOTAL_SALES"]) == (
        "SUM(store_sales.ss_ext_sales_price)"
    )


def test_a_qualified_metric_needs_no_table_stash(tpcds):
    assert stash_of(by_name(tpcds["metrics"])["TOTAL_SALES"]) == {}


def test_a_metric_referencing_no_column_keeps_its_tables_in_the_stash(tpcds):
    metric = by_name(tpcds["metrics"])["ROW_COUNT"]
    assert expression_of(metric) == "COUNT(*)"
    assert stash_of(metric)["tables"] == ["tpcds.public.store_sales"]


def test_a_cross_table_metric_is_left_unqualified_and_warns():
    _, warnings_ = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    assert any(
        "CUSTOMER_LIFETIME_VALUE" in w and "spans 2 tables" in w for w in warnings_
    )


def test_a_cross_table_metric_keeps_its_tables_in_the_stash(tpcds):
    metric = by_name(tpcds["metrics"])["CUSTOMER_LIFETIME_VALUE"]
    assert expression_of(metric) == (
        "SUM(ss_ext_sales_price) / COUNT(DISTINCT c_customer_sk)"
    )
    assert stash_of(metric)["tables"] == [
        "tpcds.public.store_sales",
        "tpcds.public.customer",
    ]


def test_metric_synonyms_become_ai_context_synonyms(tpcds):
    assert by_name(tpcds["metrics"])["TOTAL_SALES"]["ai_context"]["synonyms"] == [
        "total revenue",
        "gross sales",
        "sales amount",
    ]


def test_a_metric_without_an_expression_is_dropped_with_a_warning():
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    del solid["semantic_model"]["metrics"][0]["expression"]
    ossie, warnings_ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert any("LATE_ORDER_COUNT" in w and "no expression" in w for w in warnings_)
    assert [m["name"] for m in model_of(ossie)["metrics"]] == ["GROSS_REVENUE"]


# --- model level ------------------------------------------------------------------


def test_asset_link_markup_is_resolved_in_ai_context_and_kept_raw_in_the_stash(tpcds):
    instructions = tpcds["ai_context"]["instructions"]
    assert "public.store_sales" in instructions
    assert "assetlink" not in instructions
    assert "@<assetlink" in stash_of(tpcds)["custom_instructions"]


def test_business_questions_become_ai_context_examples(tpcds):
    assert len(tpcds["ai_context"]["examples"]) == 3


def test_example_queries_and_benchmark_questions_are_stashed(tpcds):
    stash = stash_of(tpcds)
    assert [q["name"] for q in stash["example_queries"]] == [
        "Monthly revenue and profit",
        "Sales per employee by state",
    ]
    assert stash["benchmark_questions"][0]["is_enabled"] is True
    assert stash["benchmark_questions"][1]["is_enabled"] is False


def test_both_model_descriptions_are_preserved(tpcds):
    stash = stash_of(tpcds)
    assert tpcds["description"].startswith("TPC-DS retail semantic model")
    assert stash["model_llm_description"].startswith("TPC-DS retail semantic model")
    assert stash["model_description"].startswith("Retail analytics for the TPC-DS")


def test_the_model_name_can_be_overridden():
    ossie, _ = convert_quietly(
        convert_solid_to_ossie, fixture("tpcds_solid.yaml"), model_name="renamed"
    )
    assert model_of(ossie)["name"] == "renamed"


# --- input validation --------------------------------------------------------------


def test_an_ossie_document_is_rejected_with_a_pointed_message():
    with pytest.raises(ConversionError, match="ossie-solid export"):
        convert_solid_to_ossie(fixture("tpcds_ossie.yaml"))


def test_a_model_without_tables_is_rejected():
    with pytest.raises(ConversionError, match="at least one dataset"):
        convert_solid_to_ossie("semantic_model:\n  name: empty\n")


def test_malformed_yaml_is_rejected_cleanly():
    with pytest.raises(ConversionError, match="Invalid YAML"):
        convert_solid_to_ossie("semantic_model: [unclosed\n")


def test_a_missing_semantic_model_key_is_rejected():
    with pytest.raises(ConversionError, match="missing the top-level"):
        convert_solid_to_ossie("tables: []\n")
