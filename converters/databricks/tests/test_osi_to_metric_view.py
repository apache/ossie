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

"""Tests for the Apache Ossie -> Databricks Metric View exporter."""

import json

import pytest

from ossie_databricks import ConversionError
from ossie_databricks import osi_to_metric_view as exporter
from _util import canon, load_fixture, parse


def test_fixtureA_export_matches_expected():
    out = exporter.convert_osi_to_metric_view(load_fixture("fixtureA_osi.yaml"))
    assert parse(out) == parse(load_fixture("fixtureA_metric_view.yaml"))


def test_tpcds_export_matches_expected():
    """A normalized TPC-DS star (fact + date/item/customer dims) exports to the expected
    Metric View: a join tree, primary keys bridged to rely.at_most_one_match, joined
    columns alias-qualified, and the filter/format stash carried through."""
    out = exporter.convert_osi_to_metric_view(load_fixture("tpcds_osi.yaml"))
    assert parse(out) == parse(load_fixture("tpcds_metric_view.yaml"))


def test_unsupported_version_rejected():
    osi = "version: '9.9.9'\nsemantic_model:\n  - name: m\n    datasets:\n      - {name: d, source: c.s.t}\n"
    with pytest.raises(ConversionError):
        exporter.convert_osi_to_metric_view(osi)


def _model(rels):
    return {
        "version": exporter.OSI_VERSION,
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "a", "source": "c.s.a"},
                    {"name": "b", "source": "c.s.b"},
                    {"name": "x", "source": "c.s.x"},
                ],
                "relationships": rels,
            }
        ],
    }


def _rel(name, frm, to):
    return {"name": name, "from": frm, "to": to,
            "from_columns": ["k"], "to_columns": ["k"]}


def test_multiple_roots_raises():
    import yaml
    # a->b leaves x as a second root.
    osi = yaml.safe_dump(_model([_rel("r1", "a", "b")]))
    with pytest.raises(ConversionError, match="multiple candidate fact"):
        exporter.convert_osi_to_metric_view(osi)


def test_triangle_is_rejected_as_cycle():
    import yaml
    # a->b, a->x, b->x : b and x are equidistant from a (a triangle) -> not a tree.
    osi = yaml.safe_dump(_model([_rel("r1", "a", "b"), _rel("r2", "a", "x"),
                                 _rel("r3", "b", "x")]))
    with pytest.raises(ConversionError, match="cycle"):
        exporter.convert_osi_to_metric_view(osi)


def _field(name, col):
    return {"name": name, "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": col}]}}


def test_mto_diamond_fans_out():
    """A shared dimension reached by two parents (orders->customers->regions and
    orders->suppliers->regions) is fanned out into two aliased joins, not rejected."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders", "fields": [_field("amt", "amount")]},
            {"name": "customers", "source": "c.s.customers"},
            {"name": "suppliers", "source": "c.s.suppliers"},
            {"name": "regions", "source": "c.s.regions", "fields": [_field("rname", "r_name")]},
        ],
        "relationships": [
            {"name": "r1", "from": "orders", "to": "customers", "from_columns": ["cid"], "to_columns": ["id"]},
            {"name": "r2", "from": "orders", "to": "suppliers", "from_columns": ["sid"], "to_columns": ["id"]},
            {"name": "r3", "from": "customers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
            {"name": "r4", "from": "suppliers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
        ],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi))
    region_joins = [j for top in out["joins"] for j in top.get("joins", []) if j["source"] == "c.s.regions"]
    assert {j["name"] for j in region_joins} == {"customers_regions", "suppliers_regions"}
    dims = {d["name"]: d["expr"] for d in out["dimensions"]}
    # the fanned `regions` is a depth-2 join, so its column is qualified by the full path
    assert dims["customers_regions_rname"] == "customers.customers_regions.r_name"
    assert dims["suppliers_regions_rname"] == "suppliers.suppliers_regions.r_name"


def test_otm_diamond_fans_out():
    """customers (fact) -> past_orders/future_orders -> line_items: the shared
    line_items is fanned out, and every join is one_to_many."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "customers", "source": "c.s.customers"},
            {"name": "past_orders", "source": "c.s.past_orders"},
            {"name": "future_orders", "source": "c.s.future_orders"},
            {"name": "line_items", "source": "c.s.line_items"},
        ],
        "relationships": [
            {"name": "r1", "from": "past_orders", "to": "customers", "from_columns": ["cid"], "to_columns": ["id"]},
            {"name": "r2", "from": "future_orders", "to": "customers", "from_columns": ["cid"], "to_columns": ["id"]},
            {"name": "r3", "from": "line_items", "to": "past_orders", "from_columns": ["oid"], "to_columns": ["id"]},
            {"name": "r4", "from": "line_items", "to": "future_orders", "from_columns": ["oid"], "to_columns": ["id"]},
        ],
        "metrics": [{"name": "cnt", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "COUNT(*)"}]}}],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi, source="customers"))
    leaf_names = {j["name"] for top in out["joins"] for j in top.get("joins", [])}
    assert leaf_names == {"past_orders_line_items", "future_orders_line_items"}

    def all_otm(joins):
        return all(j.get("cardinality") == "one_to_many" and all_otm(j.get("joins", []))
                   for j in joins)
    assert all_otm(out["joins"])


def test_cycle_raises():
    import yaml
    # a->b->x->a : cycle, and no root.
    osi = yaml.safe_dump(_model([_rel("r1", "a", "b"), _rel("r2", "b", "x"),
                                 _rel("r3", "x", "a")]))
    with pytest.raises(ConversionError, match="cycle"):
        exporter.convert_osi_to_metric_view(osi)


def test_no_unknown_keys_leak():
    """Exporter output must contain no key outside the v1.1 schema (the strict-parse
    guard: no `custom_extensions`, no `sql_on`)."""
    out = exporter.convert_osi_to_metric_view(load_fixture("fixtureA_osi.yaml"))
    assert "custom_extensions" not in out
    assert "sql_on" not in out


def test_primary_key_is_dropped():
    out = parse(exporter.convert_osi_to_metric_view(load_fixture("fixtureA_osi.yaml")))
    assert "primary_key" not in json.dumps(out)


def _single_fact_model(metric_expr):
    import yaml
    return yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [{"name": "orders", "source": "c.s.orders",
                          "fields": [{"name": "k", "expression": {"dialects": [
                              {"dialect": "DATABRICKS", "expression": "k"}]}}]}],
            "metrics": [{"name": "rev", "expression": {"dialects": [
                {"dialect": "DATABRICKS", "expression": metric_expr}]}}],
        }],
    })


def test_measure_strips_fact_prefix():
    """Fact columns are bare in measure expressions (DBR idiom), not `source.`-qualified."""
    out = parse(exporter.convert_osi_to_metric_view(_single_fact_model("SUM(orders.amount)")))
    assert out["measures"][0]["expr"] == "SUM(amount)"


def test_measure_keeps_lookalike_table_prefix():
    """A table whose name merely ends with the fact name is not stripped."""
    out = parse(exporter.convert_osi_to_metric_view(_single_fact_model("SUM(store_orders.amount)")))
    assert out["measures"][0]["expr"] == "SUM(store_orders.amount)"


def test_invalid_source_rejected():
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{"name": "m", "datasets": [{"name": "d", "source": "justatable"}]}],
    })
    with pytest.raises(ConversionError, match="source"):
        exporter.convert_osi_to_metric_view(osi)


def test_duplicate_dimension_name_warns():
    import warnings
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [
                {"name": "orders", "source": "c.s.orders",
                 "fields": [{"name": "id", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "id"}]}}]},
                {"name": "customer", "source": "c.s.customer",
                 "fields": [{"name": "id", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "id"}]}}]},
            ],
            "relationships": [{"name": "r", "from": "orders", "to": "customer",
                              "from_columns": ["cid"], "to_columns": ["id"]}],
        }],
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exporter.convert_osi_to_metric_view(osi)
    assert any("unique" in str(w.message) for w in caught)


def test_cascade_drop_downstream_measure_reference():
    """A measure that references a dropped measure via measure() is itself dropped
    (transitively), so no dangling reference is emitted."""
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [{"name": "f", "source": "c.s.f"}],
            "metrics": [
                {"name": "base", "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "SUM(x)"}]}},   # dropped (no DBX/ANSI)
                {"name": "derived", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "measure(base) * 2"}]}},
                {"name": "derived2", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "measure(derived) + 1"}]}},  # transitive
                {"name": "ok", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "COUNT(*)"}]}},
            ],
        }],
    })
    out = parse(exporter.convert_osi_to_metric_view(osi))
    names = [m["name"] for m in out.get("measures", [])]
    assert names == ["ok"]   # base dropped; derived + derived2 cascade-dropped; ok survives


def test_cascade_drop_downstream_dimension_reference():
    """A field/measure referencing a dropped dimension by name is also dropped."""
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [{"name": "f", "source": "c.s.f", "fields": [
                {"name": "region", "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "r"}]}},   # dropped dim
                {"name": "label", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "upper(region)"}]}},  # references region
                {"name": "keep", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "id"}]}},
            ]}],
        }],
    })
    out = parse(exporter.convert_osi_to_metric_view(osi))
    dims = [d["name"] for d in out.get("dimensions", [])]
    assert dims == ["keep"]   # region dropped; label cascade-dropped; keep survives


def test_orientation_unverifiable_when_to_side_has_no_key_warns():
    """If the `from` columns are a declared key but the `to` side declares no key, the
    from/to orientation can't be verified; the converter leaves it as-is (no reorient)
    and warns, rather than silently producing a possibly-inverted cardinality."""
    import warnings
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [
                {"name": "a", "source": "c.s.a", "primary_key": ["a_id"], "fields": [
                    {"name": "a_name", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "a_name"}]}}]},
                {"name": "b", "source": "c.s.b", "fields": [
                    {"name": "b_name", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "b_name"}]}}]},
            ],
            # from columns cover a's PK, but b (the `to` side) declares no key
            "relationships": [{"name": "a_to_b", "from": "a", "to": "b",
                               "from_columns": ["a_id"], "to_columns": ["b_x"]}],
        }],
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exporter.convert_osi_to_metric_view(osi)
    assert any("orientation can't be verified" in str(w.message) for w in caught)


def test_cascade_drop_skips_qualified_join_alias_collision():
    """A dropped field whose name collides with a join alias must NOT cascade-drop a
    *qualified* `alias.col` reference. A genuine dimension reference is unqualified;
    `region.r_name` points at the join `region`, a different thing than a dropped bare
    `region`, so the cascade must leave it (and the joined column) alone."""
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [
                {"name": "orders", "source": "c.s.orders", "fields": [
                    # dropped (no DBX/ANSI dialect); its name collides with the `region` join
                    {"name": "region", "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "r"}]}},
                    # references the join alias `region`, not the dropped field -> must survive
                    {"name": "summary", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "region.r_name"}]}},
                ]},
                {"name": "region", "source": "c.s.region", "primary_key": ["r_key"], "fields": [
                    {"name": "r_name", "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": "r_name"}]}},
                ]},
            ],
            "relationships": [{"name": "orr", "from": "orders", "to": "region",
                               "from_columns": ["o_rkey"], "to_columns": ["r_key"]}],
        }],
    })
    out = parse(exporter.convert_osi_to_metric_view(osi))
    dims = [d["name"] for d in out.get("dimensions", [])]
    # `region` field dropped; `summary` (refs alias region.r_name) and the joined
    # `r_name` (qualified to region.r_name on export) both survive -- no false cascade.
    assert "region" not in dims
    assert "summary" in dims and "r_name" in dims


def _orders_lineitems_osi():
    """Apache Ossie: line_items (many, FK l_order_id) -> orders (one, PK order_id)."""
    import yaml
    return yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "sales",
            "datasets": [
                {"name": "orders", "source": "c.s.orders", "primary_key": ["order_id"],
                 "fields": [{"name": "order_date", "expression": {"dialects": [
                     {"dialect": "DATABRICKS", "expression": "o_order_date"}]}}]},
                {"name": "line_items", "source": "c.s.line_items",
                 "fields": [{"name": "product_sk", "expression": {"dialects": [
                     {"dialect": "DATABRICKS", "expression": "l_product_sk"}]}}]},
            ],
            "relationships": [{"name": "li_to_order", "from": "line_items", "to": "orders",
                               "from_columns": ["l_order_id"], "to_columns": ["order_id"]}],
            "metrics": [{"name": "order_count", "expression": {"dialects": [
                {"dialect": "DATABRICKS", "expression": "COUNT(*)"}]}}],
        }],
    })


def test_source_on_to_side_derives_one_to_many():
    """Naming the PK/one-side dataset as the source makes its join one_to_many, and
    the many-side table's columns drop (a field must resolve to one value/source row)."""
    out = parse(exporter.convert_osi_to_metric_view(_orders_lineitems_osi(), source="orders"))
    assert out["source"] == "c.s.orders"
    join = out["joins"][0]
    assert join["name"] == "line_items"
    assert join["cardinality"] == "one_to_many"
    assert join["on"] == "source.order_id = line_items.l_order_id"
    assert [d["name"] for d in out.get("dimensions", [])] == ["order_date"]  # product_sk dropped


def test_default_fact_is_fk_sink_and_many_to_one():
    """Without an explicit source the fact is the FK-sink (line_items) and the join to
    orders is the default many_to_one (no explicit cardinality)."""
    out = parse(exporter.convert_osi_to_metric_view(_orders_lineitems_osi()))
    assert out["source"] == "c.s.line_items"
    join = out["joins"][0]
    assert join["name"] == "orders"
    assert "cardinality" not in join
    assert join["on"] == "source.l_order_id = orders.order_id"


def test_unknown_source_rejected():
    with pytest.raises(ConversionError, match="not a dataset"):
        exporter.convert_osi_to_metric_view(_orders_lineitems_osi(), source="nope")


def test_one_to_many_subtree_must_stay_one_to_many():
    """A many_to_one join descending from a one_to_many join is rejected (DBR rule)."""
    import yaml
    osi = yaml.safe_dump({
        "version": exporter.OSI_VERSION,
        "semantic_model": [{
            "name": "m",
            "datasets": [
                {"name": "orders", "source": "c.s.orders"},
                {"name": "line_items", "source": "c.s.line_items"},
                {"name": "product", "source": "c.s.product"},
            ],
            "relationships": [
                {"name": "li_to_order", "from": "line_items", "to": "orders",     # orders->li : OTM
                 "from_columns": ["l_order_id"], "to_columns": ["order_id"]},
                {"name": "li_to_product", "from": "line_items", "to": "product",  # li->product : MTO
                 "from_columns": ["l_product_sk"], "to_columns": ["p_sk"]},
            ],
        }],
    })
    with pytest.raises(ConversionError, match="one-to-many"):
        exporter.convert_osi_to_metric_view(osi, source="orders")


def test_primary_key_deduces_at_most_one_match():
    """A many_to_one join whose to_columns cover the target's declared primary_key
    gets rely.at_most_one_match; a join to a key-less dataset does not."""
    import yaml

    def model(dim_extra):
        dim = {"name": "customer", "source": "c.s.customer"}
        dim.update(dim_extra)
        return yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
            "name": "m",
            "datasets": [{"name": "orders", "source": "c.s.orders"}, dim],
            "relationships": [{"name": "r", "from": "orders", "to": "customer",
                               "from_columns": ["cid"], "to_columns": ["id"]}],
        }]})

    join = parse(exporter.convert_osi_to_metric_view(model({"primary_key": ["id"]})))["joins"][0]
    assert join.get("rely") == {"at_most_one_match": True}
    join2 = parse(exporter.convert_osi_to_metric_view(model({})))["joins"][0]
    assert "rely" not in join2


def test_mislabeled_from_to_reoriented_by_key():
    """When from/to is swapped but the declared keys show the real one-side, the
    converter re-orients to the key side (warns) -- so fact selection and cardinality
    come out identical to the well-formed model."""
    import warnings
    import yaml

    def model(frm, to, from_cols, to_cols):
        return yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
            "name": "m",
            "datasets": [
                {"name": "orders", "source": "c.s.orders", "primary_key": ["order_id"],
                 "fields": [_field("amt", "amount")]},
                {"name": "customer", "source": "c.s.customer", "primary_key": ["c_id"],
                 "fields": [_field("cname", "c_name")]},
            ],
            "relationships": [{"name": "r", "from": frm, "to": to,
                               "from_columns": from_cols, "to_columns": to_cols}],
        }]})

    well = parse(exporter.convert_osi_to_metric_view(
        model("orders", "customer", ["cust_id"], ["c_id"])))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        swapped = parse(exporter.convert_osi_to_metric_view(
            model("customer", "orders", ["c_id"], ["cust_id"])))
    assert any("mislabeled" in str(w.message) for w in caught)
    assert swapped["source"] == "c.s.orders"   # fact selection corrected to the FK holder
    assert swapped == well                      # identical to the well-formed model


def test_dataset_named_source_is_renamed():
    """A dataset literally named `source` must not collide with the fact's reserved
    `source` alias (would otherwise emit an ambiguous join)."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders"},
            {"name": "source", "source": "c.s.dim", "fields": [_field("x", "xcol")]},
        ],
        "relationships": [{"name": "r", "from": "orders", "to": "source",
                           "from_columns": ["sid"], "to_columns": ["id"]}],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi))
    join = out["joins"][0]
    assert join["name"] != "source"
    assert join["on"] == f"source.sid = {join['name']}.id"
    assert out["dimensions"][0]["expr"] == f"{join['name']}.xcol"


def test_fanout_alias_collision_deduped():
    """A real dataset whose name equals a synthesized fan-out alias still gets a
    distinct alias -- no two joins share a name."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders"},
            {"name": "customers", "source": "c.s.customers"},
            {"name": "suppliers", "source": "c.s.suppliers"},
            {"name": "regions", "source": "c.s.regions"},
            {"name": "customers_regions", "source": "c.s.cr"},  # collides with fan-out alias
        ],
        "relationships": [
            {"name": "r1", "from": "orders", "to": "customers", "from_columns": ["cid"], "to_columns": ["id"]},
            {"name": "r2", "from": "orders", "to": "suppliers", "from_columns": ["sid"], "to_columns": ["id"]},
            {"name": "r3", "from": "customers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
            {"name": "r4", "from": "suppliers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
            {"name": "r5", "from": "orders", "to": "customers_regions", "from_columns": ["xid"], "to_columns": ["id"]},
        ],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi))
    names = []

    def collect(joins):
        for j in joins:
            names.append(j["name"])
            collect(j.get("joins", []))

    collect(out["joins"])
    assert len(names) == len(set(names)), names  # all join names unique


def test_malformed_input_raises_conversion_error():
    """Missing required keys surface as ConversionError, not a raw KeyError traceback."""
    import yaml
    bad = yaml.safe_dump({"version": exporter.OSI_VERSION,
                          "semantic_model": [{"name": "m", "datasets": [{"source": "c.s.t"}]}]})
    with pytest.raises(ConversionError, match="missing required 'name'"):
        exporter.convert_osi_to_metric_view(bad)


def test_nameless_relationship_with_ai_context_does_not_crash():
    """A relationship may omit `name`; the dropped-ai_context warning must not raise a
    raw KeyError when it has ai_context but no name."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders", "fields": [_field("amt", "amt")]},
            {"name": "customers", "source": "c.s.customers"},
        ],
        "relationships": [
            {"from": "orders", "to": "customers", "from_columns": ["cid"],
             "to_columns": ["id"], "ai_context": "joins orders to customers"},
        ],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi))  # must not raise
    assert out["joins"][0]["name"] == "customers"


def _cfield(name, expr):
    return {"name": name, "expression": {"dialects": [{"dialect": "DATABRICKS", "expression": expr}]}}


def test_fanout_complex_expr_dropped_not_emitted_ambiguously():
    """On a fanned-out (diamond) dataset, a simple column fans out into one aliased
    dimension per instance, but a complex expression -- which cannot be attributed to a
    single instance -- is dropped rather than emitted ambiguously."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders"},
            {"name": "customers", "source": "c.s.customers"},
            {"name": "suppliers", "source": "c.s.suppliers"},
            {"name": "regions", "source": "c.s.regions",
             "fields": [_cfield("r_name", "r_name"), _cfield("rfull", "r_a || r_b")]},
        ],
        "relationships": [
            {"name": "r1", "from": "orders", "to": "customers", "from_columns": ["cid"], "to_columns": ["id"]},
            {"name": "r2", "from": "orders", "to": "suppliers", "from_columns": ["sid"], "to_columns": ["id"]},
            {"name": "r3", "from": "customers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
            {"name": "r4", "from": "suppliers", "to": "regions", "from_columns": ["rid"], "to_columns": ["id"]},
        ],
    }]})
    dims = parse(exporter.convert_osi_to_metric_view(osi)).get("dimensions", [])
    # the simple column fans out into two unambiguous, alias-qualified dimensions ...
    assert sum(1 for d in dims if d["name"].endswith("_r_name")) == 2
    # ... while the ambiguous complex expression is dropped (never emitted unqualified)
    assert not any("||" in d["expr"] for d in dims)


def test_malformed_yaml_raises_conversion_error():
    """Invalid YAML surfaces as ConversionError, not a raw yaml.YAMLError traceback."""
    with pytest.raises(ConversionError, match="Invalid YAML"):
        exporter.convert_osi_to_metric_view("semantic_model: [oops\n")


def test_nested_join_uses_full_path_qualification():
    """A snowflake (orders -> customer -> nation) qualifies a column from the nested
    `nation` join by its full join path from the source (`customer.nation.n_name`) -- the
    Databricks nested-join rule -- not the single-level `nation.n_name`. A depth-1 join
    stays single-name."""
    import yaml
    osi = yaml.safe_dump({"version": exporter.OSI_VERSION, "semantic_model": [{
        "name": "m",
        "datasets": [
            {"name": "orders", "source": "c.s.orders", "fields": [_field("amt", "amount")]},
            {"name": "customer", "source": "c.s.customer", "fields": [_field("cname", "c_name")]},
            {"name": "nation", "source": "c.s.nation", "fields": [_field("nname", "n_name")]},
        ],
        "relationships": [
            {"name": "r1", "from": "orders", "to": "customer", "from_columns": ["ckey"], "to_columns": ["c_key"]},
            {"name": "r2", "from": "customer", "to": "nation", "from_columns": ["nkey"], "to_columns": ["n_key"]},
        ],
    }]})
    out = parse(exporter.convert_osi_to_metric_view(osi))
    exprs = {d["name"]: d["expr"] for d in out["dimensions"]}
    assert exprs["cname"] == "customer.c_name"            # depth-1: the join's own name
    assert exprs["nname"] == "customer.nation.n_name"     # depth-2: full path from source
    # the nested join's `on:` still uses immediate names (single-level)
    nation_join = out["joins"][0]["joins"][0]
    assert nation_join["on"] == "customer.nkey = nation.n_key"


def test_case_variant_dataset_name_rejected():
    """DBR identifiers are case-insensitive, so two datasets differing only in case
    (`customer`/`Customer`) collide and are rejected (review finding)."""
    osi = ("version: 0.2.0.dev0\nsemantic_model:\n- name: m\n  datasets:\n"
           "  - {name: customer, source: c.s.c}\n  - {name: Customer, source: c.s.c2}\n")
    with pytest.raises(ConversionError, match="duplicate"):
        exporter.convert_osi_to_metric_view(osi)


def test_non_string_field_expression_raises_clean_error():
    """A non-string dialect expression raises a ConversionError, not a raw crash
    (review finding)."""
    osi = ("version: 0.2.0.dev0\nsemantic_model:\n- name: m\n  datasets:\n"
           "  - name: o\n    source: c.s.o\n    fields:\n    - name: d\n      expression:\n"
           "        dialects:\n        - {dialect: DATABRICKS, expression: 123}\n")
    with pytest.raises(ConversionError, match="must be a string"):
        exporter.convert_osi_to_metric_view(osi)


def test_scalar_join_columns_rejected():
    """`from_columns`/`to_columns` given as a scalar string (not a list) raise a clear
    'must be lists' error rather than a misleading character-count length error."""
    osi = ("version: 0.2.0.dev0\nsemantic_model:\n- name: m\n  datasets:\n"
           "  - {name: a, source: c.s.a}\n  - {name: b, source: c.s.b}\n  relationships:\n"
           "  - {name: ab, from: a, to: b, from_columns: cid, to_columns: id}\n")
    with pytest.raises(ConversionError, match="must be lists"):
        exporter.convert_osi_to_metric_view(osi)
