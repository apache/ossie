"""Unit tests for the OSI -> Omni exporter."""

import warnings

import pytest

from osi_omni import ConversionError, convert_osi_to_omni
from osi_omni._common import dump_yaml
from _util import REPO_ROOT, load_fixture, load_fixture_dir, parse, parse_files


def export(osi_yaml, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_osi_to_omni(osi_yaml, **kwargs)


def minimal(**model_overrides):
    model = {
        "name": "m",
        "datasets": [{"name": "orders", "source": "db.sch.orders",
                      "fields": [_field("amount")]}],
    }
    model.update(model_overrides)
    return dump_yaml({"version": "0.2.0.dev0", "semantic_model": [model]})


def _field(name, expr=None, dialect="ANSI_SQL", **extra):
    f = {"name": name,
         "expression": {"dialects": [{"dialect": dialect,
                                      "expression": expr or name}]}}
    f.update(extra)
    return f


# --- fixtures ---------------------------------------------------------------

def test_fixtureA_export_matches_expected():
    files = export(load_fixture("fixtureA_osi.yaml"))
    assert parse_files(files) == parse_files(load_fixture_dir("fixtureA_omni"))


def test_tpcds_export_matches_expected():
    with open(REPO_ROOT / "examples" / "tpcds_semantic_model.yaml") as fh:
        files = export(fh.read())
    assert parse_files(files) == parse_files(load_fixture_dir("tpcds_omni"))


# --- structure --------------------------------------------------------------

def test_three_part_source_splits_into_catalog_schema_table():
    files = export(minimal())
    view = parse(files["views/orders.view.yaml"])
    assert view["catalog"] == "db"
    assert view["schema"] == "sch"
    assert "table_name" not in view  # table matches the view name


def test_table_name_emitted_when_it_differs():
    files = export(minimal(datasets=[{"name": "orders", "source": "sch.raw_orders"}]))
    view = parse(files["views/orders.view.yaml"])
    assert view == {"schema": "sch", "table_name": "raw_orders"}


def test_subquery_source_becomes_sql_view():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "SELECT * FROM t"}]))
    assert parse(files["views/orders.view.yaml"])["sql"] == "SELECT * FROM t"


def test_one_part_source_rejected():
    with pytest.raises(ConversionError, match="no schema part"):
        export(minimal(datasets=[{"name": "orders", "source": "orders"}]))


def test_field_same_named_bare_column_gets_no_sql():
    files = export(minimal())
    assert parse(files["views/orders.view.yaml"])["dimensions"]["amount"] == {}


def test_field_renamed_bare_column_gets_sql():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "fields": [_field("total", "amount")]}]))
    dims = parse(files["views/orders.view.yaml"])["dimensions"]
    assert dims["total"] == {"sql": "amount"}


def test_complex_expression_emitted_verbatim():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "fields": [_field("full_name", "first || ' ' || last")]}]))
    dims = parse(files["views/orders.view.yaml"])["dimensions"]
    assert dims["full_name"]["sql"] == "first || ' ' || last"


def test_is_time_maps_to_default_timeframes():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "fields": [_field("created_at", dimension={"is_time": True})]}]))
    dims = parse(files["views/orders.view.yaml"])["dimensions"]
    assert dims["created_at"]["timeframes"] == [
        "raw", "date", "week", "month", "quarter", "year"]


def test_single_primary_key_marks_dimension():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders", "primary_key": ["id"],
         "fields": [_field("id")]}]))
    assert parse(files["views/orders.view.yaml"])["dimensions"]["id"][
        "primary_key"] is True


def test_composite_primary_key_uses_compound_key():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "primary_key": ["id", "line"], "fields": [_field("id")]}]))
    view = parse(files["views/orders.view.yaml"])
    assert view["custom_compound_primary_key_sql"] == ["id", "line"]
    # The uncovered key column materialized as a hidden dimension.
    assert view["dimensions"]["line"] == {"hidden": True}


def test_field_metadata_maps():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "fields": [_field("amount", label="Amount", description="The amount",
                           ai_context={"synonyms": ["value"],
                                       "instructions": "Prefer this."})]}]))
    dim = parse(files["views/orders.view.yaml"])["dimensions"]["amount"]
    assert dim["label"] == "Amount"
    assert dim["description"] == "The amount"
    assert dim["synonyms"] == ["value"]
    assert dim["ai_context"] == "Prefer this."


def test_relationship_becomes_join_entry():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders",
         "fields": [_field("user_id")]},
        {"name": "users", "source": "db.sch.users", "fields": [_field("id")]},
    ], relationships=[
        {"name": "r", "from": "orders", "to": "users",
         "from_columns": ["user_id"], "to_columns": ["id"]}]))
    rels = parse(files["relationships.yaml"])
    assert rels == [{
        "join_from_view": "orders", "join_to_view": "users",
        "on_sql": "${orders.user_id} = ${users.id}",
        "relationship_type": "many_to_one"}]


def test_composite_relationship_joins_with_and():
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders"},
        {"name": "users", "source": "db.sch.users"},
    ], relationships=[
        {"name": "r", "from": "orders", "to": "users",
         "from_columns": ["a", "b"], "to_columns": ["c", "d"]}]))
    on_sql = parse(files["relationships.yaml"])[0]["on_sql"]
    assert on_sql == "${orders.a} = ${users.c} AND ${orders.b} = ${users.d}"


def test_topic_generated_with_fk_sink_base_and_join_tree():
    files = export(load_fixture("fixtureA_osi.yaml"))
    topic = parse(files["topics/sales.topic.yaml"])
    assert topic["base_view"] == "orders"
    assert topic["joins"] == {"customer": {}}
    assert topic["description"] == "Sales orders with customer attributes"
    assert topic["ai_context"] == "Use this model for order analysis."


def test_explicit_base_view_overrides_heuristic():
    files = export(load_fixture("fixtureA_osi.yaml"), base_view="customer")
    topic = parse(files["topics/sales.topic.yaml"])
    assert topic["base_view"] == "customer"
    assert topic["joins"] == {"orders": {}}


def test_unknown_base_view_rejected():
    with pytest.raises(ConversionError, match="not a dataset"):
        export(minimal(), base_view="nope")


def test_multiple_roots_require_base_view():
    osi = minimal(datasets=[{"name": "a", "source": "db.s.a"},
                            {"name": "b", "source": "db.s.b"}])
    with pytest.raises(ConversionError, match="--base-view"):
        export(osi)


def test_metric_aggregate_becomes_structured_measure():
    files = export(minimal(metrics=[
        {"name": "total", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": "SUM(orders.amount)"}]}}]))
    measures = parse(files["views/orders.view.yaml"])["measures"]
    assert measures["total"] == {"sql": "${amount}", "aggregate_type": "sum"}


def test_count_star_becomes_count_measure_on_base_view():
    files = export(minimal(metrics=[
        {"name": "n", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}]))
    assert parse(files["views/orders.view.yaml"])["measures"]["n"] == {
        "aggregate_type": "count"}


def test_count_distinct_metric():
    files = export(minimal(metrics=[
        {"name": "n", "expression": {"dialects": [
            {"dialect": "ANSI_SQL",
             "expression": "COUNT(DISTINCT orders.amount)"}]}}]))
    assert parse(files["views/orders.view.yaml"])["measures"]["n"] == {
        "sql": "${amount}", "aggregate_type": "count_distinct"}


def test_ratio_metric_becomes_raw_sql_measure():
    files = export(minimal(metrics=[
        {"name": "avg_x", "expression": {"dialects": [
            {"dialect": "ANSI_SQL",
             "expression": "SUM(orders.amount) / COUNT(*)"}]}}]))
    measure = parse(files["views/orders.view.yaml"])["measures"]["avg_x"]
    assert measure == {"sql": "SUM(${orders.amount}) / COUNT(*)"}


def test_dialect_preference():
    field = {"name": "x", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "ansi_col"},
        {"dialect": "SNOWFLAKE", "expression": "snow_col"}]}}
    files = export(minimal(datasets=[
        {"name": "orders", "source": "db.sch.orders", "fields": [field]}]),
        dialect="SNOWFLAKE")
    assert parse(files["views/orders.view.yaml"])["dimensions"]["x"][
        "sql"] == "snow_col"


def test_names_are_sanitized():
    files = export(dump_yaml({"version": "0.2.0.dev0", "semantic_model": [{
        "name": "My Model",
        "datasets": [{"name": "Order Items", "source": "db.sch.t",
                      "fields": [_field("Total Price", "p")]}]}]}))
    assert "views/order_items.view.yaml" in files
    assert "topics/my_model.topic.yaml" in files
    dims = parse(files["views/order_items.view.yaml"])["dimensions"]
    assert dims == {"total_price": {"sql": "p"}}


def test_sanitization_collision_rejected():
    osi = minimal(datasets=[{"name": "Orders", "source": "db.s.a"},
                            {"name": "orders", "source": "db.s.b"}])
    with pytest.raises(ConversionError, match="collides"):
        export(osi)


def test_duplicate_metric_names_rejected():
    metric = {"name": "total", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(orders.amount)"}]}}
    with pytest.raises(ConversionError, match="two metrics"):
        export(minimal(metrics=[metric, dict(metric, name="Total")]))


def test_relationship_column_count_mismatch_rejected():
    osi = minimal(datasets=[{"name": "a", "source": "db.s.a"},
                            {"name": "b", "source": "db.s.b"}],
                  relationships=[{"name": "r", "from": "a", "to": "b",
                                  "from_columns": ["x"], "to_columns": ["y", "z"]}])
    with pytest.raises(ConversionError, match="same length"):
        export(osi)


def test_unknown_relationship_dataset_rejected():
    osi = minimal(relationships=[{"name": "r", "from": "orders", "to": "ghost",
                                  "from_columns": ["x"], "to_columns": ["y"]}])
    with pytest.raises(ConversionError, match="unknown dataset"):
        export(osi)


def test_unsupported_version_rejected():
    with pytest.raises(ConversionError, match="Unsupported OSI version"):
        export(dump_yaml({"version": "9.9.9", "semantic_model": [{"name": "m"}]}))


# --- warnings ---------------------------------------------------------------

def _warnings_of(osi_yaml, **kwargs):
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        convert_osi_to_omni(osi_yaml, **kwargs)
    return [str(w.message) for w in ws]


def test_unique_keys_warn():
    msgs = _warnings_of(minimal(datasets=[
        {"name": "orders", "source": "db.s.t", "primary_key": ["id"],
         "unique_keys": [["email"]], "fields": [_field("id")]}]))
    assert any("unique_keys" in m for m in msgs)


def test_unique_keys_restating_primary_key_do_not_warn():
    msgs = _warnings_of(minimal(datasets=[
        {"name": "orders", "source": "db.s.t", "primary_key": ["id"],
         "unique_keys": [["id"]], "fields": [_field("id")]}]))
    assert not any("unique_keys" in m for m in msgs)


def test_field_without_usable_dialect_warns_and_drops():
    osi = minimal(datasets=[
        {"name": "orders", "source": "db.s.t",
         "fields": [_field("x", dialect="MDX")]}])
    msgs = _warnings_of(osi)
    assert any("no usable" in m for m in msgs)
    assert "dimensions" not in parse(export(osi)["views/orders.view.yaml"])


def test_relationship_ai_context_warns():
    msgs = _warnings_of(minimal(datasets=[
        {"name": "a", "source": "db.s.a"}, {"name": "b", "source": "db.s.b"}],
        relationships=[{"name": "r", "from": "a", "to": "b",
                        "from_columns": ["x"], "to_columns": ["y"],
                        "ai_context": {"synonyms": ["join"]}}]))
    assert any("relationship ai_context" in m for m in msgs)


def test_foreign_vendor_extensions_warn():
    msgs = _warnings_of(minimal(custom_extensions=[
        {"vendor_name": "DBT", "data": "{}"}]))
    assert any("foreign-vendor" in m for m in msgs)


def test_multiple_models_warn_and_first_converts():
    osi = parse(minimal())
    osi["semantic_model"].append({"name": "second", "datasets": [
        {"name": "x", "source": "db.s.x"}]})
    msgs = _warnings_of(dump_yaml(osi))
    assert any("multiple semantic models" in m for m in msgs)
