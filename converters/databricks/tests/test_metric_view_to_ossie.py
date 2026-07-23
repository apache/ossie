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

"""Tests for the Databricks Metric View -> Apache Ossie importer."""

import pytest

from ossie_databricks import ConversionError
from ossie_databricks import metric_view_to_ossie as importer
from _util import canon, load_fixture, parse


def test_fixtureB_import_matches_expected():
    out = importer.convert_metric_view_to_ossie(load_fixture("fixtureB_metric_view.yaml"))
    assert canon(parse(out)) == canon(parse(load_fixture("fixtureB_ossie.yaml")))


def test_fields_is_accepted_as_alias_for_dimensions():
    """`fields:` is a v1.1 alias for `dimensions:` (the form the DBR docs use); the
    importer must read it, not silently drop the columns."""
    mv = (
        "version: '1.1'\nsource: c.s.orders\n"
        "fields:\n- {name: region, expr: region}\n"
    )
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    fields = ossie["semantic_model"][0]["datasets"][0].get("fields", [])
    assert [f["name"] for f in fields] == ["region"]


def test_unsupported_version_rejected():
    with pytest.raises(ConversionError):
        importer.convert_metric_view_to_ossie("version: '0.1'\nsource: c.s.t\n")


def test_both_dimensions_and_fields_present_warns_and_uses_dimensions():
    """`fields` is a v1.1 alias for `dimensions`; if a (malformed) view sets both, the
    importer uses `dimensions` and warns that the `fields` list is ignored."""
    import warnings
    mv = (
        "version: '1.1'\nsource: c.s.orders\n"
        "dimensions:\n- {name: kept, expr: kept}\n"
        "fields:\n- {name: ignored, expr: ignored}\n"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ossie = parse(importer.convert_metric_view_to_ossie(mv))
    names = [f["name"] for f in ossie["semantic_model"][0]["datasets"][0].get("fields", [])]
    assert names == ["kept"]
    assert any("fields" in str(w.message) and "ignored" in str(w.message) for w in caught)


def test_stash_written_at_each_level():
    ossie = parse(importer.convert_metric_view_to_ossie(load_fixture("fixtureB_metric_view.yaml")))
    model = ossie["semantic_model"][0]

    # model-level filter
    assert any(e["vendor_name"] == "DATABRICKS" and "filter" in e["data"]
               for e in model["custom_extensions"])
    # relationship-level rely
    rel = model["relationships"][0]
    assert any("rely" in e["data"] for e in rel["custom_extensions"])
    # metric-level format
    revenue = next(m for m in model["metrics"] if m["name"] == "revenue")
    assert any("format" in e["data"] for e in revenue["custom_extensions"])


def test_name_override():
    ossie = parse(importer.convert_metric_view_to_ossie(
        load_fixture("fixtureB_metric_view.yaml"), model_name="custom"))
    assert ossie["semantic_model"][0]["name"] == "custom"


def test_cross_join_rejected():
    mv = "version: '1.1'\nsource: c.s.fact\njoins:\n- name: dim\n  source: c.s.dim\n"
    with pytest.raises(ConversionError, match="cross"):
        importer.convert_metric_view_to_ossie(mv)


def test_duplicate_join_name_rejected():
    # a join named like the fact (derived from the source's last identifier) collides
    mv = "version: '1.1'\nsource: c.s.fact\njoins:\n- name: fact\n  source: c.s.other\n  using: [id]\n"
    with pytest.raises(ConversionError, match="Duplicate"):
        importer.convert_metric_view_to_ossie(mv)


def test_complex_joined_dimension_filed_under_join_dataset():
    mv = (
        "version: '1.1'\nsource: c.s.fact\n"
        "joins:\n- name: cust\n  source: c.s.cust\n  on: source.cid = cust.id\n"
        "dimensions:\n- name: full\n  expr: cust.a || cust.b\n"
    )
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    cust = next(d for d in ossie["semantic_model"][0]["datasets"] if d["name"] == "cust")
    assert any(f["name"] == "full" for f in cust.get("fields", []))


def test_non_equi_on_rejected():
    """Apache Ossie relationships are equi-joins (from_columns/to_columns required, minItems 1), so a
    non-equi `on` has no Apache Ossie representation and is rejected on import (rather than emitting
    a relationship with empty column lists, which is invalid per the Apache Ossie schema)."""
    mv = (
        "version: '1.1'\n"
        "source: c.s.fact\n"
        "joins:\n"
        "- name: dim\n"
        "  source: c.s.dim\n"
        "  on: source.a >= dim.b\n"
    )
    with pytest.raises(ConversionError, match="non-equi"):
        importer.convert_metric_view_to_ossie(mv)


def test_complex_equi_on_rejected():
    """An equi `on` whose operand is a SQL fragment (OR, computed) can't be decomposed
    into from/to columns, so it's rejected rather than producing schema-invalid Apache Ossie with
    empty column lists."""
    for cond in ("source.a = dim.b OR source.c = dim.d", "source.a = dim.b + 1"):
        mv = (
            "version: '1.1'\nsource: c.s.fact\n"
            f"joins:\n- name: dim\n  source: c.s.dim\n  on: {cond}\n"
        )
        with pytest.raises(ConversionError, match="non-equi"):
            importer.convert_metric_view_to_ossie(mv)


def test_one_to_many_join_flips_from_to_and_stashes_source():
    """A one_to_many MV join becomes an Apache Ossie relationship with the MANY side as `from`
    (the joined table), the source/grain on the `to` side, and the grain recorded in
    the model-level stash so re-export re-roots correctly."""
    mv = (
        "version: '1.1'\nsource: c.s.orders\n"
        "joins:\n- name: line_items\n  source: c.s.line_items\n"
        "  on: source.order_id = line_items.l_order_id\n"
        "  cardinality: one_to_many\n"
        "measures:\n- {name: order_count, expr: COUNT(*)}\n"
    )
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    model = ossie["semantic_model"][0]
    rel = model["relationships"][0]
    assert rel["from"] == "line_items"          # many side (holds the FK)
    assert rel["to"] == "orders"                # one side (holds the PK)
    assert rel["from_columns"] == ["l_order_id"]
    assert rel["to_columns"] == ["order_id"]
    assert any(e["vendor_name"] == "DATABRICKS" and "source_dataset" in e["data"]
               for e in model["custom_extensions"])


def test_at_most_one_match_recovers_unique_key():
    """A many_to_one join with rely.at_most_one_match records the join key as a
    unique_keys entry on the joined dataset (recovering key info Apache Ossie would lack)."""
    mv = (
        "version: '1.1'\nsource: c.s.orders\n"
        "joins:\n- name: customer\n  source: c.s.customer\n"
        "  on: source.cid = customer.id\n"
        "  rely: {at_most_one_match: true}\n"
    )
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    cust = next(d for d in ossie["semantic_model"][0]["datasets"] if d["name"] == "customer")
    assert cust.get("unique_keys") == [["id"]]


def test_join_named_source_rejected():
    """`source` is reserved for the fact; a join named `source` is rejected (DBR
    forbids it too) rather than silently overwriting the fact alias."""
    mv = ("version: '1.1'\nsource: c.s.fact\n"
          "joins:\n- name: source\n  source: c.s.dim\n  using: [id]\n")
    with pytest.raises(ConversionError, match="reserved"):
        importer.convert_metric_view_to_ossie(mv)


def test_sql_source_name_defaults_to_metric_view():
    """A SELECT/WITH source has no table name, so the model name defaults to
    `metric_view` (not a token sliced out of the SQL)."""
    mv = "version: '1.1'\nsource: SELECT a, b FROM main.sales.orders\n"
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    assert ossie["semantic_model"][0]["name"] == "metric_view"


def test_join_missing_source_raises():
    """Missing required keys surface as ConversionError, not a raw KeyError."""
    mv = "version: '1.1'\nsource: c.s.fact\njoins:\n- name: dim\n  using: [id]\n"
    with pytest.raises(ConversionError, match="missing required 'source'"):
        importer.convert_metric_view_to_ossie(mv)


def test_measure_rewrite_with_regex_special_name():
    r"""The `source.` -> fact-name rewrite in measures inserts the name literally, so a
    --name containing regex backreference syntax (e.g. \1) does not raise a re.error."""
    mv = "version: '1.1'\nsource: c.s.fact\nmeasures:\n- {name: rev, expr: SUM(source.amount)}\n"
    ossie = parse(importer.convert_metric_view_to_ossie(mv, model_name=r"a\1b"))
    expr = ossie["semantic_model"][0]["metrics"][0]["expression"]["dialects"][0]["expression"]
    assert expr == r"SUM(a\1b.amount)"


def test_invalid_source_rejected():
    """A malformed source (not 3-part / not SELECT) is rejected on import, matching the
    exporter -- rather than passing through and only failing on a later re-export."""
    mv = "version: '1.1'\nsource: a.b\n"  # 2-part, invalid
    with pytest.raises(ConversionError, match="3-part"):
        importer.convert_metric_view_to_ossie(mv)


def test_malformed_yaml_raises_conversion_error():
    """Invalid YAML surfaces as ConversionError, not a raw yaml.YAMLError traceback."""
    with pytest.raises(ConversionError, match="Invalid YAML"):
        importer.convert_metric_view_to_ossie("source: c.s.t\njoins: [oops\n")


def test_empty_using_rejected():
    """An empty `using: []` is a condition-less join -- rejected at import rather than
    silently producing a relationship with empty key columns (which would then fail on
    re-export)."""
    mv = "version: '1.1'\nsource: c.s.fact\njoins:\n- name: dim\n  source: c.s.dim\n  using: []\n"
    with pytest.raises(ConversionError, match="cross"):
        importer.convert_metric_view_to_ossie(mv)


def test_boollike_string_values_stay_strings_for_a_yaml_1_1_reader():
    """Bool-like string scalars (e.g. the synonyms `on`/`off`) must be emitted quoted so a
    stock YAML 1.1 reader reads them back as strings, not booleans."""
    import yaml
    mv = ("version: '1.1'\nsource: c.s.t\n"
          "dimensions:\n- {name: status, expr: status, synonyms: [on, off]}\n")
    ossie_out = importer.convert_metric_view_to_ossie(mv)
    field = yaml.safe_load(ossie_out)["semantic_model"][0]["datasets"][0]["fields"][0]
    assert field["ai_context"]["synonyms"] == ["on", "off"]


def test_fact_qualifier_variants_in_on_decompose():
    """The fact side of an `on` is valid MV YAML whether qualified with `source`, the
    source table name, or left bare; all decompose to the same equi-join columns rather
    than being wrongly rejected as a non-equi condition (bug-bash finding)."""
    base = ("version: '1.1'\nsource: c.s.orders\n"
            "joins:\n- name: customer\n  source: c.s.customer\n  on: {cond}\n")
    for cond in (
        "source.o_custkey = customer.c_custkey",   # `source` qualifier
        "orders.o_custkey = customer.c_custkey",   # source table name
        "o_custkey = customer.c_custkey",          # bare fact column
        "customer.c_custkey = o_custkey",          # reversed operand order, bare fact
    ):
        rel = parse(importer.convert_metric_view_to_ossie(
            base.format(cond=cond)))["semantic_model"][0]["relationships"][0]
        assert rel["from"] == "orders" and rel["to"] == "customer"
        assert rel["from_columns"] == ["o_custkey"]
        assert rel["to_columns"] == ["c_custkey"]


def test_multi_column_on_with_bare_and_tablename_fact():
    """Composite keys decompose with bare / source-table-name fact qualifiers too."""
    mv = ("version: '1.1'\nsource: c.s.orders\n"
          "joins:\n- name: customer\n  source: c.s.customer\n"
          "  on: o_a = customer.c_a AND orders.o_b = customer.c_b\n")
    rel = parse(importer.convert_metric_view_to_ossie(mv))["semantic_model"][0]["relationships"][0]
    assert rel["from_columns"] == ["o_a", "o_b"]
    assert rel["to_columns"] == ["c_a", "c_b"]


def test_join_named_source_rejected_any_case():
    """`source` is reserved case-insensitively (DBR identifiers are case-insensitive),
    so `Source`/`SOURCE` are rejected too (bug-bash finding)."""
    for name in ("Source", "SOURCE", "SoUrCe"):
        mv = (f"version: '1.1'\nsource: c.s.fact\n"
              f"joins:\n- name: {name}\n  source: c.s.dim\n  using: [id]\n")
        with pytest.raises(ConversionError, match="reserved"):
            importer.convert_metric_view_to_ossie(mv)


def test_empty_source_part_rejected():
    """A 3-dot source with an empty part (`.s.t`, `c..t`, `c.s.`) is not a valid 3-part
    identifier and is rejected -- the dot count alone is not enough (bug-bash finding)."""
    for src in (".s.t", "c..t", "c.s."):
        mv = f"version: '1.1'\nsource: {src}\n"
        with pytest.raises(ConversionError, match="3-part"):
            importer.convert_metric_view_to_ossie(mv)


def test_whitespace_source_part_rejected():
    """A 3-dot source with a whitespace-laden part (`cat . sch . tbl`) is rejected --
    a space is not part of a valid identifier (review finding)."""
    with pytest.raises(ConversionError, match="3-part"):
        importer.convert_metric_view_to_ossie("version: '1.1'\nsource: cat . sch . tbl\n")


def test_with_paren_subquery_source_accepted():
    """A `WITH(...)` subquery with no space after the keyword is recognized as SQL,
    not mistaken for a (non-3-part) identifier (review finding)."""
    mv = "version: '1.1'\nsource: WITH(t AS (SELECT 1 AS a)) SELECT a FROM t\n"
    ossie = parse(importer.convert_metric_view_to_ossie(mv))
    assert ossie["semantic_model"][0]["datasets"][0]["source"].startswith("WITH(")


def test_nested_join_bare_column_rejected():
    """A bare (unqualified) operand in a NESTED join's `on` is ambiguous (parent vs.
    fact), so it is rejected rather than silently attributed to the immediate parent.
    A bare fact column is still accepted at the top level (review finding)."""
    mv = ("version: '1.1'\nsource: c.s.orders\n"
          "joins:\n- name: customer\n  source: c.s.customer\n  on: source.ckey = customer.c_key\n"
          "  joins:\n  - name: nation\n    source: c.s.nation\n    on: o_nkey = nation.n_key\n")
    with pytest.raises(ConversionError, match="non-equi or unsupported"):
        importer.convert_metric_view_to_ossie(mv)


def test_case_variant_duplicate_name_rejected():
    """DBR identifiers are case-insensitive, so `dim`/`Dim` collide and must be rejected
    (consistent with the case-insensitive reserved-`source` check) (review finding)."""
    mv = ("version: '1.1'\nsource: c.s.x\n"
          "joins:\n- {name: dim, source: c.s.a, using: [id]}\n- {name: Dim, source: c.s.b, using: [id]}\n")
    with pytest.raises(ConversionError, match="[Dd]uplicate"):
        importer.convert_metric_view_to_ossie(mv)


def test_falsy_dimension_name_is_not_a_wildcard():
    """A present-but-falsy name (`0`) is a malformed column, not a wildcard projection;
    it raises a clean error rather than being silently dropped (review finding)."""
    with pytest.raises(ConversionError):
        importer.convert_metric_view_to_ossie("version: '1.1'\nsource: c.s.o\ndimensions:\n- {name: 0, expr: x}\n")


def test_non_string_scalars_raise_clean_error():
    """Non-string scalars where a string is required (join name, measure expr) raise a
    ConversionError, not a raw AttributeError/TypeError (review finding)."""
    for mv in ("version: '1.1'\nsource: c.s.f\njoins:\n- {name: 5, source: c.s.d, using: [id]}\n",
               "version: '1.1'\nsource: c.s.o\nmeasures:\n- {name: rev, expr: 5}\n"):
        with pytest.raises(ConversionError):
            importer.convert_metric_view_to_ossie(mv)


def test_using_join_emits_no_yaml_anchor():
    """`from_columns`/`to_columns` of a `using` join are distinct list objects, so the
    output has no YAML anchor/alias (`&id`/`*id`) -- safer for other Apache Ossie consumers."""
    out = importer.convert_metric_view_to_ossie(
        "version: '1.1'\nsource: c.s.a\njoins:\n- {name: b, source: c.s.b, using: [id]}\n")
    assert "&id" not in out and "*id" not in out
