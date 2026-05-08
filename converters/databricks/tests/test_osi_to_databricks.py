"""Tests for the OSI -> Databricks UC Metric View converter."""

import warnings

import pytest
import yaml

from osi_to_databricks_metric_view import (
    OsiConversionError,
    _datasets_reachable_from,
    _emit_dimensions,
    _emit_joins,
    _emit_measures,
    _extract_databricks_extension,
    _extract_expression,
    _format_source,
    _pick_primary_dataset,
    convert_osi_to_databricks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap(model_dict):
    return yaml.dump(
        {"version": "0.1.1", "semantic_model": [model_dict]},
        default_flow_style=False,
    )


def _field(name, dialect="ANSI_SQL", expression=None, is_time=None):
    expr = expression if expression is not None else name
    field = {
        "name": name,
        "expression": {"dialects": [{"dialect": dialect, "expression": expr}]},
    }
    if is_time is not None:
        field["dimension"] = {"is_time": is_time}
    return field


def _metric(name, expr, dialect="ANSI_SQL"):
    return {
        "name": name,
        "expression": {"dialects": [{"dialect": dialect, "expression": expr}]},
    }


def _minimal_two_table_model():
    return {
        "name": "sales",
        "description": "Sales analytics",
        "datasets": [
            {
                "name": "orders",
                "source": "main.sales.orders",
                "fields": [
                    _field("order_id"),
                    _field("customer_id"),
                    _field("amount"),
                ],
            },
            {
                "name": "customers",
                "source": "main.sales.customers",
                "fields": [
                    _field("id"),
                    _field("email"),
                ],
            },
        ],
        "relationships": [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            }
        ],
        "metrics": [
            _metric("total_revenue", "SUM(orders.amount)"),
        ],
    }


# ---------------------------------------------------------------------------
# Top-level conversion
# ---------------------------------------------------------------------------

def test_minimal_model_top_level_shape():
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(_minimal_two_table_model())))

    assert out["version"] == "1.1"
    assert out["source"] == "main.sales.orders"
    assert out["comment"] == "Sales analytics"

    join_names = [j["name"] for j in out["joins"]]
    assert join_names == ["orders_to_customers"]
    assert out["joins"][0]["source"] == "main.sales.customers"
    assert out["joins"][0]["sql_on"] == "orders.customer_id = customers.id"

    dim_names = [d["name"] for d in out["dimensions"]]
    assert dim_names == ["order_id", "customer_id", "amount", "id", "email"]

    measure_names = [m["name"] for m in out["measures"]]
    assert measure_names == ["total_revenue"]
    assert out["measures"][0]["expr"] == "SUM(orders.amount)"


def test_dimension_expressions_are_table_qualified():
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(_minimal_two_table_model())))
    by_name = {d["name"]: d for d in out["dimensions"]}
    assert by_name["order_id"]["expr"] == "orders.order_id"
    assert by_name["email"]["expr"] == "customers.email"


def test_unsupported_osi_version_rejected():
    with pytest.raises(OsiConversionError, match="Unsupported OSI"):
        convert_osi_to_databricks(yaml.dump(
            {"version": "0.0.9", "semantic_model": [{"name": "x"}]}
        ))


def test_missing_semantic_model_rejected():
    with pytest.raises(OsiConversionError, match="non-empty list"):
        convert_osi_to_databricks(yaml.dump({"version": "0.1.1"}))


def test_root_must_be_mapping():
    with pytest.raises(OsiConversionError, match="mapping at the root"):
        convert_osi_to_databricks("- just_a_list_item")


def test_multiple_models_warns_and_uses_first():
    payload = {
        "version": "0.1.1",
        "semantic_model": [
            _minimal_two_table_model(),
            {**_minimal_two_table_model(), "name": "ignored"},
        ],
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_osi_to_databricks(yaml.dump(payload)))
    assert any("only the first will be converted" in str(w.message) for w in caught)
    assert out["source"] == "main.sales.orders"


# ---------------------------------------------------------------------------
# Dialect selection
# ---------------------------------------------------------------------------

def test_databricks_dialect_preferred_over_ansi():
    expr = {
        "dialects": [
            {"dialect": "ANSI_SQL", "expression": "LOWER(email)"},
            {"dialect": "DATABRICKS", "expression": "lower(email)"},
        ]
    }
    assert _extract_expression(expr, "f") == "lower(email)"


def test_ansi_fallback_when_no_databricks_dialect():
    expr = {"dialects": [{"dialect": "ANSI_SQL", "expression": "id"}]}
    assert _extract_expression(expr, "f") == "id"


def test_no_compatible_dialect_warns_and_skips():
    expr = {"dialects": [{"dialect": "MAQL", "expression": "..."}]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _extract_expression(expr, "skip_me")
    assert result is None
    assert any("Skipping field/metric 'skip_me'" in str(w.message) for w in caught)


def test_missing_dialects_raises():
    with pytest.raises(OsiConversionError, match="Missing dialects"):
        _extract_expression({}, "f")


def test_missing_expression_raises():
    with pytest.raises(OsiConversionError, match="malformed expression"):
        _extract_expression(None, "f")


# ---------------------------------------------------------------------------
# Dataset and relationship handling
# ---------------------------------------------------------------------------

def test_no_datasets_raises():
    with pytest.raises(OsiConversionError, match="no datasets"):
        convert_osi_to_databricks(_wrap({"name": "empty"}))


def test_primary_picked_by_most_from_count():
    model = _minimal_two_table_model()
    primary = _pick_primary_dataset(model, model["datasets"], model["relationships"])
    assert primary["name"] == "orders"


def test_primary_overridden_by_databricks_extension_hint():
    model = _minimal_two_table_model()
    model["custom_extensions"] = [
        {"vendor_name": "DATABRICKS", "data": '{"primary_dataset": "customers"}'}
    ]
    primary = _pick_primary_dataset(model, model["datasets"], model["relationships"])
    assert primary["name"] == "customers"


def test_invalid_primary_hint_raises():
    model = _minimal_two_table_model()
    model["custom_extensions"] = [
        {"vendor_name": "DATABRICKS", "data": '{"primary_dataset": "ghost"}'}
    ]
    with pytest.raises(OsiConversionError, match="primary_dataset hint"):
        _pick_primary_dataset(model, model["datasets"], model["relationships"])


def test_composite_relationship_emits_multi_column_sql_on():
    model = _minimal_two_table_model()
    model["relationships"][0] = {
        "name": "order_lines_to_products",
        "from": "orders",
        "to": "customers",
        "from_columns": ["customer_id", "region"],
        "to_columns": ["id", "region"],
    }
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    assert out["joins"][0]["sql_on"] == (
        "orders.customer_id = customers.id AND orders.region = customers.region"
    )


def test_relationship_column_count_mismatch_raises():
    model = _minimal_two_table_model()
    model["relationships"][0]["to_columns"] = ["id", "extra"]
    with pytest.raises(OsiConversionError, match="must have the same"):
        convert_osi_to_databricks(_wrap(model))


def test_unreachable_dataset_warns_and_skips():
    model = _minimal_two_table_model()
    model["datasets"].append(
        {"name": "orphan", "source": "main.misc.orphan", "fields": [_field("k")]}
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    assert any("not reachable from primary" in str(w.message) for w in caught)
    assert all(d["name"] != "k" for d in out["dimensions"])


def test_datasets_reachable_traverses_both_directions():
    by_name = {"a": {}, "b": {}, "c": {}}
    rels = [
        {"from": "a", "to": "b", "from_columns": ["x"], "to_columns": ["y"]},
        {"from": "c", "to": "b", "from_columns": ["x"], "to_columns": ["y"]},
    ]
    assert _datasets_reachable_from("a", rels, by_name) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Field / metric mapping
# ---------------------------------------------------------------------------

def test_metric_with_no_compatible_dialect_skipped_with_warning():
    model = _minimal_two_table_model()
    model["metrics"].append(_metric("untranslatable", "...", dialect="MAQL"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    measure_names = [m["name"] for m in out["measures"]]
    assert "untranslatable" not in measure_names
    assert any("untranslatable" in str(w.message) for w in caught)


def test_dimension_name_collision_disambiguated():
    model = _minimal_two_table_model()
    # Add a duplicate "id" field to orders so it collides with customers.id
    model["datasets"][0]["fields"].append(_field("id"))
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    names = [d["name"] for d in out["dimensions"]]
    # Primary's "id" wins; customers' "id" gets prefixed
    assert "id" in names
    assert "customers_id" in names


def test_description_and_string_ai_context_merged_into_comment():
    model = _minimal_two_table_model()
    model["ai_context"] = "Prefer the orders fact for revenue queries."
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    assert "Sales analytics" in out["comment"]
    assert "orders fact" in out["comment"]


def test_format_source_passes_through_subqueries_and_three_part():
    assert _format_source("main.sales.orders") == "main.sales.orders"
    assert _format_source("SELECT * FROM main.sales.orders") == (
        "SELECT * FROM main.sales.orders"
    )
    assert _format_source(None) is None
    assert _format_source("   ") is None


def test_extract_databricks_extension_handles_invalid_json():
    osi = {"custom_extensions": [{"vendor_name": "DATABRICKS", "data": "{not json"}]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _extract_databricks_extension(osi)
    assert result == {}
    assert any("could not parse" in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# Direct unit calls into emit helpers
# ---------------------------------------------------------------------------

def test_emit_joins_skips_self_loop_and_visited():
    by_name = {"a": {"source": "a"}, "b": {"source": "b"}}
    rels = [{
        "name": "a_b",
        "from": "a", "to": "b",
        "from_columns": ["x"], "to_columns": ["y"],
    }]
    joins = _emit_joins("a", by_name, rels)
    assert len(joins) == 1
    assert joins[0]["sql_on"] == "a.x = b.y"


def test_emit_dimensions_includes_unqualified_when_expr_already_qualified():
    by_name = {
        "a": {
            "name": "a",
            "fields": [_field("col1", expression="a.col1")],
        }
    }
    dims = _emit_dimensions("a", by_name, {"a"})
    assert dims == [{"name": "col1", "expr": "a.col1"}]


# ---------------------------------------------------------------------------
# Multi-token expression handling (Bug 1 regression coverage)
# ---------------------------------------------------------------------------

def test_multi_token_expression_emitted_verbatim_on_primary():
    """A computed field on the primary dataset is emitted as-is — the
    metric view's `source` makes bare references unambiguous, so no
    auto-qualification is required.
    """
    model = _minimal_two_table_model()
    model["datasets"][0]["fields"].append(
        _field("upper_id", expression="UPPER(order_id)")
    )
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    by_name = {d["name"]: d for d in out["dimensions"]}
    # Function call expression must NOT have a stray qualifier prepended.
    assert by_name["upper_id"]["expr"] == "UPPER(order_id)"


def test_multi_token_expression_on_join_warns_when_unqualified():
    """A computed field on a *non-primary* dataset with a multi-token
    expression that doesn't already mention the dataset name should warn
    the user to provide a DATABRICKS dialect with explicit qualifiers.
    """
    model = _minimal_two_table_model()
    model["datasets"][1]["fields"].append(
        _field("full_name", expression="first_name || ' ' || last_name")
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    by_name = {d["name"]: d for d in out["dimensions"]}
    # Critically, we do NOT emit `customers.first_name || ' ' || last_name`
    # — that would only qualify the first column and leave `last_name`
    # ambiguous after joins. The expression is left verbatim instead.
    assert by_name["full_name"]["expr"] == "first_name || ' ' || last_name"
    assert any("multi-token expression" in str(w.message) for w in caught)


def test_databricks_dialect_with_qualified_expression_silences_warning():
    """If the OSI author supplies a DATABRICKS dialect entry that already
    contains the dataset-qualified references, the converter prefers it
    and does not warn.
    """
    model = _minimal_two_table_model()
    model["datasets"][1]["fields"].append({
        "name": "full_name",
        "expression": {"dialects": [
            {"dialect": "ANSI_SQL",
             "expression": "first_name || ' ' || last_name"},
            {"dialect": "DATABRICKS",
             "expression": "customers.first_name || ' ' || customers.last_name"},
        ]},
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    by_name = {d["name"]: d for d in out["dimensions"]}
    assert by_name["full_name"]["expr"] == (
        "customers.first_name || ' ' || customers.last_name"
    )
    assert not any("multi-token expression" in str(w.message) for w in caught)


def test_raw_joins_from_databricks_extension_round_tripped():
    """An OSI model carrying a DATABRICKS custom_extension with raw_joins
    (preserved from a previous UC -> OSI conversion) re-emits those joins
    verbatim alongside the relationship-derived ones.
    """
    model = _minimal_two_table_model()
    model["custom_extensions"] = [{
        "vendor_name": "DATABRICKS",
        "data": (
            '{"raw_joins": [{"name": "complex_join", '
            '"source": "main.sales.tax_rates", '
            '"sql_on": "DATEDIFF(orders.dt, tax_rates.dt) < 30"}]}'
        ),
    }]
    out = yaml.safe_load(convert_osi_to_databricks(_wrap(model)))
    join_names = [j["name"] for j in out["joins"]]
    assert "orders_to_customers" in join_names
    assert "complex_join" in join_names


def test_emit_measures_skips_metric_with_only_unsupported_dialects():
    metrics = [_metric("ok", "SUM(x)"), _metric("bad", "...", dialect="MAQL")]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        measures = _emit_measures(metrics)
    names = [m["name"] for m in measures]
    assert names == ["ok"]
