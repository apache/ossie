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
from typing import Any

import pytest
import yaml
from ossie import OSIDialect, OSIDialectExpression, OSIExpression

from ossie_hex._common import ConversionError, load_yaml
from ossie_hex.ossie_to_hex import (
    convert_ossie_to_hex,
    normalize_to_hex_id,
    pick_expression,
)


def test_coerce_hex_id_prefixes_number_with_underscore() -> None:
    assert normalize_to_hex_id('"123 Orders"', "dataset", set()) == "_123_orders"


def test_coerce_hex_id_replaces_empty_result() -> None:
    assert normalize_to_hex_id('"!"', "dataset", set()) == "_1"


def test_normalize_to_hex_id_preserves_valid_id() -> None:
    assert normalize_to_hex_id("order_items", "dataset", set()) == "order_items"


def test_normalize_to_hex_id_rejects_collisions() -> None:
    with pytest.raises(ConversionError, match="collides"):
        normalize_to_hex_id("Orders", "dataset", {"orders"})


@pytest.mark.parametrize("name", ["", "   "])
def test_normalize_to_hex_id_rejects_a_blank_name(name: str) -> None:
    """A name with nothing in it is not the same as one that coerces to nothing.

    ``"!"`` has a character to work from and lands on a placeholder ID, but a
    blank name would have the converter invent the whole thing.
    """
    with pytest.raises(ConversionError, match="dataset has a blank name"):
        normalize_to_hex_id(name, "dataset", set())


def test_pick_expression_prefers_requested_then_ansi_dialect() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="ansi_expr"),
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            ),
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.SNOWFLAKE) == "snowflake_expr"
    )
    assert pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "ansi_expr"


def test_pick_expression_falls_back_to_first_or_none() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            )
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "snowflake_expr"
    )
    assert pick_expression(None) is None


def test_export_uses_requested_osi_expression_dialect() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: amount
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: ansi_amount
                - dialect: SNOWFLAKE
                  expression: snowflake_amount
            dimension: {}
"""
    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.SNOWFLAKE.value)

    # `snowflake_amount` is a column of `s.orders`, not a dimension of the model,
    # so it stays raw SQL. Wrapping it would name a dimension that is not there.
    dimension = load_yaml(files["orders.yml"])["dimensions"][0]
    assert dimension["expr_sql"] == "snowflake_amount"


def test_export_ambiguous_metrics_require_base_model() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: a
        source: s.a
        fields:
          - name: x
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: x
            dimension: {}
            datatype: Integer
      - name: b
        source: s.b
        fields:
          - name: y
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: y
            dimension: {}
            datatype: Integer
    metrics:
      - name: weird
        datatype: Integer
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "1 + 1"
"""
    with pytest.raises(ConversionError, match="Could not assign metric"):
        convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    files, warnings = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="a"
    )

    # The unassignable metric lands on --base-model, not on the other dataset.
    # Single-character Ossie names are padded, since Hex IDs need two characters.
    assert (
        files["a_.yml"]
        == """\
id: a_
base_sql_table: s.a
dimensions:
- id: x_
  type: number
measures:
- id: weird
  func_sql: 1 + 1
"""
    )
    assert (
        files["b_.yml"]
        == """\
id: b_
base_sql_table: s.b
dimensions:
- id: y_
  type: number
"""
    )
    assert warnings == []


@pytest.mark.parametrize(
    ("source", "base_key"),
    [
        ("orders", "base_sql_table"),
        ("public.orders", "base_sql_table"),
        ("analytics.public.orders", "base_sql_table"),
        ('"Order Items".orders', "base_sql_table"),
        ("`proj-1`.ds.orders", "base_sql_table"),
        ("db . schema . orders", "base_sql_table"),
        ("SELECT 1 AS x", "base_sql_query"),
        ("WITH t AS (SELECT 1 AS x) SELECT * FROM t", "base_sql_query"),
        ("(SELECT 1 AS x)", "base_sql_query"),
        ("-- daily events\nSELECT 1 AS x", "base_sql_query"),
        ("/* daily events */ SELECT 1 AS x", "base_sql_query"),
        ("FROM raw.events SELECT id", "base_sql_query"),
        ("TABLE orders", "base_sql_query"),
        ("VALUES (1), (2)", "base_sql_query"),
        ("read_parquet('s3://bucket/f.parquet')", "base_sql_query"),
    ],
)
def test_export_classifies_source_without_hex_extension(
    source: str, base_key: str
) -> None:
    # A dataset from another tool carries no HEX extension, so the source kind has to
    # be recovered from the source text itself.
    ossie = f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: {json.dumps(source)}
"""
    files, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    resource = load_yaml(files["orders.yml"])
    assert resource.get(base_key) == source
    assert warnings == []


def test_export_ignores_non_hex_custom_extensions() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    custom_extensions:
      - vendor_name: DBT
        data: '{}'
    datasets:
      - name: orders
        source: analytics.orders
        custom_extensions:
          - vendor_name: SNOWFLAKE
            data: '{}'
        fields:
          - name: order_id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: order_id
            dimension: {}
            datatype: Decimal
            custom_extensions:
              - vendor_name: DBT
                data: '{}'
    metrics:
      - name: order_count
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(*)
        datatype: Decimal
        custom_extensions:
          - vendor_name: DBT
            data: '{}'
"""
    files, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    assert files
    assert warnings == []


def _field_ossie(*, datatype: str | None, is_time: bool | None) -> str:
    """One dataset holding a single `created_at` field with optional metadata."""
    datatype_line = f"            datatype: {datatype}\n" if datatype else ""
    dimension_block = (
        "            dimension: {}\n"
        if is_time is None
        else f"            dimension:\n              is_time: {str(is_time).lower()}\n"
    )
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: created_at
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: created_at}}]
{datatype_line}{dimension_block}"""


@pytest.mark.parametrize(
    ("datatype", "is_time", "hex_type", "warning"),
    [
        # Ossie infers the role from a temporal datatype, which is how Hex reads
        # its own types, so an inferred role and an explicit one both agree.
        ("Date", None, "date", None),
        ("Date", True, "date", None),
        ("String", None, "string", None),
        ("String", False, "string", None),
        # A role Hex cannot infer from the type is dropped, as for a year grain
        # kept as an integer.
        ("Integer", True, "number", "is a time dimension"),
        # Ossie can hold a temporal column off the time axis; Hex cannot.
        ("Date", False, "date", "is marked is_time: false"),
        # `Time` is the one Ossie temporal type with no Hex equivalent, so it
        # lands on `other` and the role disagrees without anyone marking it.
        ("Time", None, "other", "is a time dimension"),
        ("Time", False, "other", None),
    ],
)
def test_time_role_survives_only_when_the_hex_type_carries_it(
    datatype: str | None,
    is_time: bool | None,
    hex_type: str,
    warning: str | None,
) -> None:
    files, warnings = convert_ossie_to_hex(
        _field_ossie(datatype=datatype, is_time=is_time),
        dialect=OSIDialect.ANSI_SQL.value,
    )
    dimension = yaml.safe_load(files["orders.yml"])["dimensions"][0]

    assert dimension["type"] == hex_type
    if warning is None:
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert warning in str(warnings[0])


def test_a_field_without_dimension_metadata_has_no_time_role_to_lose() -> None:
    """Every Ossie field becomes a Hex dimension, but only some carry a role.

    A field with no ``dimension`` block never opted out of the time axis, so a
    temporal datatype on it must not be reported as a dropped opt-out.
    """
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: created_at
            datatype: Date
            expression:
              dialects: [{dialect: ANSI_SQL, expression: created_at}]
"""
    _, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    assert warnings == []


def _one_metric_ossie(metric_expression: str, *, datatype: str = "Decimal") -> str:
    """An `orders` dataset whose only field is `amount`, carrying one metric."""
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: amount
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: amount}}]
            dimension: {{}}
    metrics:
      - name: total
        datatype: {datatype}
        expression:
          dialects: [{{dialect: ANSI_SQL, expression: "{metric_expression}"}}]
"""


def _measure_for(metric_expression: str) -> dict[str, Any]:
    """Convert one metric on an `orders` model whose only field is `amount`."""
    files, _ = convert_ossie_to_hex(
        _one_metric_ossie(metric_expression),
        dialect=OSIDialect.ANSI_SQL.value,
        base_model="orders",
    )
    return yaml.safe_load(files["orders.yml"])["measures"][0]


def test_a_metric_becomes_func_sql() -> None:
    """An Ossie metric is SQL, and `func_sql` is the Hex measure that holds SQL.

    A Hex-authored measure recovers its `func`/`of` from the stash instead, so
    nothing is served by parsing the aggregate back out of the expression here.
    """
    assert _measure_for("SUM(orders.amount)") == {
        "id": "total",
        "func_sql": "SUM(${amount})",
    }


@pytest.mark.parametrize(
    "expression",
    [
        "COUNT(*)",
        "COUNT(DISTINCT amount)",
        "SUM(amount * 2)",
        "SUM(CASE WHEN x THEN 1 END)",
        "SUM(a) / COUNT(*)",
        "AVG(price) OVER (PARTITION BY x)",
    ],
)
def test_a_metric_expression_carries_across_verbatim(expression: str) -> None:
    """Every shape of expression takes the same path, however complex.

    These are the cases an aggregate parser had to recognise and decline, since
    a Hex `of` names a single dimension and cannot hold a computed argument, a
    window, or an expression spanning two aggregates. Only a reference needs
    rewriting, and none of these carry one.
    """
    assert _measure_for(expression) == {"id": "total", "func_sql": expression}


def test_a_metric_datatype_becomes_the_measure_type() -> None:
    """MAX over a date column is a date, and `func_sql` can say so.

    Nothing here came from Hex, so there is no stash and the metric's own
    datatype is all the importer has to go on. This is the type a `func` measure
    could not have held, since Hex pins those to `number`.
    """
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: order_date
            datatype: Date
            expression:
              dialects: [{dialect: ANSI_SQL, expression: order_date}]
            dimension: {}
    metrics:
      - name: latest_order
        datatype: Date
        expression:
          dialects: [{dialect: ANSI_SQL, expression: "MAX(orders.order_date)"}]
"""
    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="orders"
    )

    assert yaml.safe_load(files["orders.yml"])["measures"][0] == {
        "id": "latest_order",
        "func_sql": "MAX(${order_date})",
        "type": "date",
    }


def test_a_stashed_func_keeps_its_number_type_over_a_conflicting_datatype() -> None:
    """A stashed `func` is only reachable if Hex typed the measure as a number.

    The `Date` here is deliberately impossible: `MAX` over the numeric `amount`
    is a number, and Hex would not have accepted a `func` measure typed anything
    else. It stands for a datatype edited after export, which is the only way
    the two can disagree. Carrying it across builds a measure `HexMeasure`
    rejects, which surfaced as a raw pydantic error rather than a
    ConversionError.
    """
    stash = json.dumps(
        {
            "model_id": "orders",
            "display_name": "Total",
            "func": "max",
            "of": "amount",
        }
    )
    ossie = _one_metric_ossie("MAX(orders.amount)", datatype="Date").replace(
        '          dialects: [{dialect: ANSI_SQL, expression: "MAX(orders.amount)"}]',
        '          dialects: [{dialect: ANSI_SQL, expression: "MAX(orders.amount)"}]\n'
        "        custom_extensions:\n"
        "          - vendor_name: HEX\n"
        f"            data: '{stash}'",
    )

    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="orders"
    )

    assert yaml.safe_load(files["orders.yml"])["measures"][0] == {
        "id": "total",
        "func": "max",
        "of": "amount",
    }


def test_a_qualifier_inside_a_string_literal_is_not_a_reference() -> None:
    # The same text twice: rewritten as a reference, left alone as a literal.
    measure = _measure_for(
        "COUNT(CASE WHEN orders.amount > 0 THEN 'orders.amount' END)"
    )

    assert measure["func_sql"] == (
        "COUNT(CASE WHEN ${amount} > 0 THEN 'orders.amount' END)"
    )


def test_a_metric_named_like_a_qualified_one_is_taken_at_face_value() -> None:
    """Only the HEX payload can say a name was qualified to dodge a collision.

    Export renames a colliding measure to ``<model>__<measure>``, but nothing
    stops an Ossie author from writing that name and meaning it. With no
    payload to say otherwise, the name is the measure ID, prefix and all.
    """
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: amount
            expression:
              dialects: [{dialect: ANSI_SQL, expression: amount}]
            dimension: {}
    metrics:
      - name: orders__revenue
        datatype: Decimal
        expression:
          dialects: [{dialect: ANSI_SQL, expression: "SUM(orders.amount)"}]
"""

    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)
    measure = yaml.safe_load(files["orders.yml"])["measures"][0]

    assert measure == {"id": "orders__revenue", "func_sql": "SUM(${amount})"}


def _two_dataset_ossie(metric_expression: str, *, related: bool) -> str:
    relationships = (
        """
    relationships:
      - name: buyer
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
"""
        if related
        else ""
    )
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: customer_id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: customer_id}}]
            dimension: {{}}
            datatype: String
          - name: amount
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: amount}}]
            dimension: {{}}
            datatype: Decimal
      - name: customers
        source: s.customers
        fields:
          - name: id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: id}}]
            dimension: {{}}
            datatype: String
{relationships}
    metrics:
      - name: value_per_customer
        datatype: Decimal
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "{metric_expression}"
"""


def _cross_dataset_measure(
    metric_expression: str, *, related: bool
) -> tuple[dict[str, Any], list[str]]:
    files, warnings = convert_ossie_to_hex(
        _two_dataset_ossie(metric_expression, related=related),
        dialect=OSIDialect.ANSI_SQL.value,
        base_model="orders",
    )
    measure = yaml.safe_load(files["orders.yml"])["measures"][0]
    return measure, [str(w) for w in warnings]


def test_every_reference_in_a_raw_sql_measure_is_rewritten() -> None:
    measure, warnings = _cross_dataset_measure(
        "SUM(orders.amount) / COUNT(DISTINCT customers.id)", related=True
    )

    assert measure["func_sql"] == "SUM(${amount}) / COUNT(DISTINCT ${buyer.id})"
    assert warnings == []


def test_a_reference_to_an_unrelated_model_is_left_verbatim() -> None:
    """Better to leave SQL a human can fix than invent a ref Hex cannot follow."""
    measure, warnings = _cross_dataset_measure(
        "SUM(orders.amount) / COUNT(DISTINCT customers.id)", related=False
    )

    assert measure["func_sql"] == "SUM(${amount}) / COUNT(DISTINCT customers.id)"
    assert warnings == [
        (
            "metric 'value_per_customer' references customers, which 'orders' has "
            "no relation to; the SQL was kept verbatim and needs review"
        )
    ]


def _dimension_for(field_expression: str, *, related: bool = True) -> dict[str, Any]:
    """Convert an `orders.label` field, with `customers` reachable as `buyer`."""
    relationships = (
        """
    relationships:
      - name: buyer
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [name]
"""
        if related
        else ""
    )
    ossie = f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: customer_id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: customer_id}}]
            dimension: {{}}
          - name: label
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: "{field_expression}"}}]
            dimension: {{}}
      - name: customers
        source: s.customers
        fields:
          - name: name
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: name}}]
            dimension: {{}}
{relationships}
"""
    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="orders"
    )
    dimensions = yaml.safe_load(files["orders.yml"])["dimensions"]
    return next(d for d in dimensions if d["id"] == "label")


def test_a_dimension_expression_reaching_another_model_uses_the_relation() -> None:
    dimension = _dimension_for("UPPER(customers.name)")

    assert dimension["expr_sql"] == "UPPER(${buyer.name})"


def test_a_dimension_expression_reaching_an_unrelated_model_is_left_verbatim() -> None:
    dimension = _dimension_for("UPPER(customers.name)", related=False)

    assert dimension["expr_sql"] == "UPPER(customers.name)"


def test_a_dimension_that_only_reads_its_own_column_has_no_expression() -> None:
    """Hex derives expr_sql from the dimension ID, so emitting it would be noise."""
    assert "expr_sql" not in _dimension_for("label")
    assert "expr_sql" not in _dimension_for("orders.label")


def test_export_maps_dataset_names_that_are_not_hex_ids() -> None:
    """Ossie names are free-form, so every ref must go through the ID mapping."""
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: Order Items
        source: s.order_items
        fields:
          - name: CustomerID
            expression:
              dialects: [{dialect: ANSI_SQL, expression: CustomerID}]
            dimension: {}
          - name: Amount
            expression:
              dialects: [{dialect: ANSI_SQL, expression: Amount}]
            dimension: {}
      - name: Customers
        source: s.customers
        primary_key: [CustomerID]
        fields:
          - name: CustomerID
            expression:
              dialects: [{dialect: ANSI_SQL, expression: CustomerID}]
            dimension: {}
    relationships:
      - name: items_to_customers
        from: Order Items
        to: Customers
        from_columns: [CustomerID]
        to_columns: [CustomerID]
    metrics:
      - name: total
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "SUM(Order Items.Amount)"
"""
    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="Order Items"
    )
    items = yaml.safe_load(files["order_items.yml"])

    assert items["id"] == "order_items"
    # `target` must name the coerced Hex model, not the original Ossie name.
    assert items["relations"] == [
        {
            "id": "items_to_customers",
            "target": "customers",
            "type": "many_to_one",
            "join_sql": "${customerid} = ${items_to_customers.customerid}",
        }
    ]


def test_export_is_deterministic_for_synthesized_key_dimensions() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: facts
        source: s.facts
        primary_key: [k_alpha, k_bravo, k_charlie, k_delta, k_echo]
        fields:
          - name: v
            expression:
              dialects: [{dialect: ANSI_SQL, expression: v}]
            dimension: {}
"""
    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)
    ids = [d["id"] for d in yaml.safe_load(files["facts.yml"])["dimensions"]]

    # Declaration order, not set-iteration order, which varies per process.
    assert ids == ["v_", "k_alpha", "k_bravo", "k_charlie", "k_delta", "k_echo"]


def test_export_rejects_an_unknown_base_model() -> None:
    """A name that matches no dataset would silently swallow the metrics it takes."""
    with pytest.raises(ConversionError, match="--base-model 'nope'"):
        convert_ossie_to_hex(
            _one_metric_ossie("COUNT(*)"),
            dialect=OSIDialect.ANSI_SQL.value,
            base_model="nope",
        )


def test_export_reports_a_malformed_document_as_a_conversion_error() -> None:
    """Callers catch ConversionError, so neither validator may surface raw."""
    with pytest.raises(ConversionError, match="Invalid Ossie document"):
        convert_ossie_to_hex("version: 0.2.0.dev0\nname: not-a-core-document\n")
