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

"""Unit tests for the Omni -> OSI importer."""

import json
import warnings

import pytest

from osi_omni import ConversionError, convert_omni_to_osi
from osi_omni._common import dump_yaml
from _util import load_fixture_dir, parse


def imp(files, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return parse(convert_omni_to_osi(files, **kwargs))["semantic_model"][0]


def minimal_files(**view_overrides):
    view = {"schema": "sch", "dimensions": {"id": {"primary_key": True},
                                            "amount": {}}}
    view.update(view_overrides)
    return {"views/orders.view.yaml": dump_yaml(view)}


def stash_of(obj):
    for ext in obj.get("custom_extensions") or []:
        if ext.get("vendor_name") == "OMNI":
            return json.loads(ext["data"])
    return {}


def dataset(model, name):
    return next(d for d in model["datasets"] if d["name"] == name)


def field(ds, name):
    return next(f for f in ds.get("fields", []) if f["name"] == name)


def expr_of(obj):
    return obj["expression"]["dialects"][0]["expression"]


# --- structure --------------------------------------------------------------

def test_view_becomes_dataset_with_source():
    model = imp(minimal_files())
    ds = dataset(model, "orders")
    assert ds["source"] == "sch.orders"  # table_name defaults to the view name
    assert ds["primary_key"] == ["id"]
    assert expr_of(field(ds, "amount")) == "amount"


def test_catalog_and_table_name_join_into_source():
    model = imp(minimal_files(catalog="db", table_name="RAW_ORDERS"))
    assert dataset(model, "orders")["source"] == "db.sch.RAW_ORDERS"


def test_sql_view_becomes_subquery_source():
    files = {"views/v.view.yaml": dump_yaml({"sql": "SELECT 1 AS x",
                                             "dimensions": {"x": {}}})}
    assert dataset(imp(files), "v")["source"] == "SELECT 1 AS x"


def test_view_without_schema_or_sql_is_stashed_not_converted():
    # An extends-only view has no standalone dataset form; it is preserved in
    # the model stash (and a model with nothing else convertible is an error).
    files = dict(minimal_files())
    files["views/v.view.yaml"] = dump_yaml({"extends": ["orders"],
                                            "dimensions": {"x": {}}})
    model = imp(files)
    assert [d["name"] for d in model["datasets"]] == ["orders"]
    assert "views/v.view.yaml" in stash_of(model)["extra_files"]

    with pytest.raises(ConversionError, match="no convertible view files"):
        imp({"views/v.view.yaml": dump_yaml({"dimensions": {"x": {}}})})


def test_no_view_files_rejected():
    with pytest.raises(ConversionError, match="no convertible view files"):
        imp({"model.yaml": "{}\n"})


def test_dimension_metadata_maps():
    files = minimal_files(dimensions={
        "amount": {"label": "Amount", "description": "The amount",
                   "synonyms": ["value"], "ai_context": "Prefer this."}})
    f = field(dataset(imp(files), "orders"), "amount")
    assert f["label"] == "Amount"
    assert f["description"] == "The amount"
    assert f["ai_context"] == {"instructions": "Prefer this.",
                               "synonyms": ["value"]}


def test_timeframes_map_to_is_time_and_stash():
    files = minimal_files(dimensions={"created_at": {"timeframes": ["raw", "date"]}})
    f = field(dataset(imp(files), "orders"), "created_at")
    assert f["dimension"] == {"is_time": True}
    assert stash_of(f)["timeframes"] == ["raw", "date"]


def test_field_reference_sql_translates_and_stashes_original():
    files = minimal_files(dimensions={
        "sale_price": {},
        "full_price": {"sql": "${sale_price} * 1.1"}})
    f = field(dataset(imp(files), "orders"), "full_price")
    assert expr_of(f) == "sale_price * 1.1"
    assert stash_of(f)["sql"] == "${sale_price} * 1.1"


def test_table_ref_sql_translates():
    files = minimal_files(dimensions={"x": {"sql": "${TABLE}.raw_x"}})
    f = field(dataset(imp(files), "orders"), "x")
    assert expr_of(f) == "raw_x"


def test_omni_only_dimension_params_stash():
    files = minimal_files(dimensions={
        "amount": {"format": "usdcurrency_2", "hidden": True,
                   "group_label": "Money"}})
    f = field(dataset(imp(files), "orders"), "amount")
    assert stash_of(f) == {"_v": 1, "format": "usdcurrency_2", "hidden": True,
                           "group_label": "Money"}


def test_compound_primary_key_resolves_to_columns():
    files = minimal_files(
        custom_compound_primary_key_sql=["id", "line"],
        dimensions={"id": {}, "line": {"sql": "line_no"}})
    assert dataset(imp(files), "orders")["primary_key"] == ["id", "line_no"]


def test_view_extras_stash():
    files = minimal_files(label="Orders", hidden=True, tags=["fact"])
    ds = dataset(imp(files), "orders")
    assert stash_of(ds)["view_extras"] == {"label": "Orders", "hidden": True,
                                           "tags": ["fact"]}


# --- relationships ----------------------------------------------------------

def _two_view_files(rels):
    return {
        "views/orders.view.yaml": dump_yaml(
            {"schema": "s", "dimensions": {"id": {"primary_key": True},
                                           "user_id": {}}}),
        "views/users.view.yaml": dump_yaml(
            {"schema": "s", "dimensions": {"id": {"primary_key": True}}}),
        "relationships.yaml": dump_yaml(rels),
    }


def test_many_to_one_join_decomposes():
    model = imp(_two_view_files([
        {"join_from_view": "orders", "join_to_view": "users",
         "on_sql": "${orders.user_id} = ${users.id}",
         "relationship_type": "many_to_one"}]))
    rel = model["relationships"][0]
    assert (rel["from"], rel["to"]) == ("orders", "users")
    assert rel["from_columns"] == ["user_id"]
    assert rel["to_columns"] == ["id"]
    assert rel["name"] == "orders_to_users"


def test_one_to_many_join_flips_to_many_side_first():
    model = imp(_two_view_files([
        {"join_from_view": "users", "join_to_view": "orders",
         "on_sql": "${users.id} = ${orders.user_id}",
         "relationship_type": "one_to_many"}]))
    rel = model["relationships"][0]
    assert (rel["from"], rel["to"]) == ("orders", "users")
    assert rel["from_columns"] == ["user_id"]
    assert rel["to_columns"] == ["id"]
    assert stash_of(rel)["relationship_type"] == "one_to_many"


def test_composite_on_sql_decomposes_in_order():
    model = imp(_two_view_files([
        {"join_from_view": "orders", "join_to_view": "users",
         "on_sql": "${orders.a} = ${users.b} AND ${orders.c} = ${users.d}",
         "relationship_type": "many_to_one"}]))
    rel = model["relationships"][0]
    assert rel["from_columns"] == ["a", "c"]
    assert rel["to_columns"] == ["b", "d"]


def test_field_reference_in_on_sql_resolves_to_column():
    files = {
        "views/orders.view.yaml": dump_yaml(
            {"schema": "s",
             "dimensions": {"user_key": {"sql": "user_id_raw"}}}),
        "views/users.view.yaml": dump_yaml(
            {"schema": "s", "dimensions": {"id": {}}}),
        "relationships.yaml": dump_yaml([
            {"join_from_view": "orders", "join_to_view": "users",
             "on_sql": "${orders.user_key} = ${users.id}",
             "relationship_type": "many_to_one"}]),
    }
    rel = imp(files)["relationships"][0]
    assert rel["from_columns"] == ["user_id_raw"]


def test_non_equi_join_is_stashed_not_converted():
    # A range join is valid in Omni but has no OSI relationship form; it is
    # preserved verbatim (with its position) rather than failing the import.
    entry = {"join_from_view": "orders", "join_to_view": "users",
             "on_sql": "${orders.total} >= ${users.threshold}",
             "relationship_type": "many_to_one"}
    model = imp(_two_view_files([entry]))
    assert "relationships" not in model
    assert stash_of(model)["extra_relationships"] == [
        {"index": 0, "entry": entry}]


def test_join_referencing_third_view_is_stashed_not_converted():
    entry = {"join_from_view": "orders", "join_to_view": "users",
             "on_sql": "${orders.x} = ${ghost.y}",
             "relationship_type": "many_to_one"}
    model = imp(_two_view_files([entry]))
    assert "relationships" not in model
    assert stash_of(model)["extra_relationships"] == [
        {"index": 0, "entry": entry}]


def test_join_to_missing_view_rejected():
    with pytest.raises(ConversionError, match="has no view file"):
        imp({"views/orders.view.yaml": dump_yaml({"schema": "s"}),
             "relationships.yaml": dump_yaml([
                 {"join_from_view": "orders", "join_to_view": "ghost",
                  "on_sql": "${orders.x} = ${ghost.y}",
                  "relationship_type": "many_to_one"}])})


def test_join_type_and_where_sql_stash():
    model = imp(_two_view_files([
        {"join_from_view": "orders", "join_to_view": "users",
         "on_sql": "${orders.user_id} = ${users.id}",
         "relationship_type": "many_to_one", "join_type": "inner",
         "where_sql": "${users.active}", "reversible": True}]))
    stash = stash_of(model["relationships"][0])
    assert stash["join_type"] == "inner"
    assert stash["where_sql"] == "${users.active}"
    assert stash["reversible"] is True


# --- measures ---------------------------------------------------------------

def test_count_measure_becomes_count_star_metric():
    files = minimal_files(measures={"count": {"aggregate_type": "count"}})
    metric = imp(files)["metrics"][0]
    assert expr_of(metric) == "COUNT(*)"
    assert metric["name"] == "count"


def test_sum_measure_qualifies_operand():
    files = minimal_files(measures={
        "total": {"sql": "${amount}", "aggregate_type": "sum",
                  "description": "Total", "synonyms": ["revenue"]}})
    metric = imp(files)["metrics"][0]
    assert expr_of(metric) == "SUM(orders.amount)"
    assert metric["description"] == "Total"
    assert metric["ai_context"] == {"synonyms": ["revenue"]}


def test_count_distinct_measure():
    files = minimal_files(measures={
        "n_users": {"sql": "${amount}", "aggregate_type": "count_distinct"}})
    assert expr_of(imp(files)["metrics"][0]) == "COUNT(DISTINCT orders.amount)"


def test_raw_sql_measure_keeps_view_qualifiers():
    files = minimal_files(measures={
        "ratio": {"sql": "SUM(${orders.amount}) / COUNT(*)"}})
    assert expr_of(imp(files)["metrics"][0]) == "SUM(orders.amount) / COUNT(*)"


def test_filtered_measure_stashes_original():
    files = minimal_files(measures={
        "done": {"aggregate_type": "count",
                 "filters": {"status": {"is": "complete"}}}})
    metric = imp(files)["metrics"][0]
    assert expr_of(metric) == "COUNT(*)"
    stash = stash_of(metric)
    assert stash["measure"]["filters"] == {"status": {"is": "complete"}}
    assert stash["view"] == "orders"


def test_percentile_measure_best_effort_expression():
    files = minimal_files(measures={
        "p95": {"sql": "${amount}", "aggregate_type": "percentile",
                "percentile": 95}})
    metric = imp(files)["metrics"][0]
    assert expr_of(metric) == (
        "PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY orders.amount)")
    assert stash_of(metric)["measure"]["aggregate_type"] == "percentile"


def test_unknown_aggregate_type_rejected():
    files = minimal_files(measures={"x": {"aggregate_type": "mystery"}})
    with pytest.raises(ConversionError, match="unknown aggregate_type"):
        imp(files)


def test_colliding_measure_names_qualify_with_view():
    files = {
        "views/a.view.yaml": dump_yaml(
            {"schema": "s", "measures": {"count": {"aggregate_type": "count"}}}),
        "views/b.view.yaml": dump_yaml(
            {"schema": "s", "measures": {"count": {"aggregate_type": "count"}}}),
    }
    names = sorted(m["name"] for m in imp(files)["metrics"])
    assert names == ["a__count", "b__count"]


# --- topics and the model file ----------------------------------------------

def test_topic_maps_onto_model():
    model = imp(load_fixture_dir("fixtureB_omni"))
    assert model["name"] == "order_analysis"
    assert model["description"] == "Line-item order analysis"
    assert model["ai_context"] == {"instructions": "You are an ecommerce analyst."}
    stash = stash_of(model)
    assert stash["mapped_topic"] == "order_analysis"
    assert stash["base_view"] == "order_items"
    topic_stash = stash["topics"]["order_analysis"]
    assert "description" not in topic_stash  # natively mapped, not duplicated
    assert topic_stash["label"] == "Order Analysis"
    assert topic_stash["joins"] == {"users": {"orders": {}}}


def test_model_file_stashes_verbatim():
    model = imp(load_fixture_dir("fixtureB_omni"))
    model_file = stash_of(model)["model_file"]
    assert model_file["week_start_day"] == "Sunday"
    assert model_file["included_schemas"] == ["ecomm"]


def test_no_topics_stashes_empty_topic_set():
    model = imp(minimal_files())
    assert stash_of(model)["topics"] == {}


def test_topic_flag_selects_mapped_topic():
    files = dict(load_fixture_dir("fixtureB_omni"))
    files["topics/second.topic.yaml"] = dump_yaml(
        {"base_view": "users", "description": "Second"})
    model = imp(files, topic="second")
    assert model["description"] == "Second"


def test_unknown_topic_flag_rejected():
    with pytest.raises(ConversionError, match="not found"):
        imp(load_fixture_dir("fixtureB_omni"), topic="ghost")


def test_model_name_flag_overrides_topic_name():
    model = imp(load_fixture_dir("fixtureB_omni"), model_name="my_model")
    assert model["name"] == "my_model"


def test_query_view_preserved_as_extra_file():
    files = minimal_files()
    files["views/facts.query.view.yaml"] = "schema: s\nsql: SELECT 1\n"
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        model = parse(convert_omni_to_osi(files))["semantic_model"][0]
    assert any("query views" in str(w.message) for w in ws)
    assert "views/facts.query.view.yaml" in stash_of(model)["extra_files"]


def test_relationships_must_be_a_list():
    files = minimal_files()
    files["relationships.yaml"] = dump_yaml({"not": "a list"})
    with pytest.raises(ConversionError, match="top-level YAML list"):
        imp(files)
