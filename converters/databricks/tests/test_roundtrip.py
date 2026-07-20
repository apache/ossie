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

"""Round-trip tests in both directions."""

from ossie_databricks import metric_view_to_ossie as importer
from ossie_databricks import ossie_to_metric_view as exporter
from _util import canon, load_fixture, parse, strip_dropped


def test_ossie_to_mv_to_ossie():
    """Apache Ossie -> MV -> Apache Ossie preserves everything except the documented drops
    (model name, primary_key/unique_keys)."""
    ossie_in = load_fixture("fixtureA_ossie.yaml")
    mv = exporter.convert_ossie_to_metric_view(ossie_in)
    ossie_out = importer.convert_metric_view_to_ossie(mv)
    assert strip_dropped(parse(ossie_out)) == strip_dropped(parse(ossie_in))


def test_using_clause_round_trips():
    """A `using` join survives MV -> Apache Ossie -> MV (equal column lists re-emit as `using`)."""
    mv_in = (
        "version: '1.1'\nsource: c.s.fact\n"
        "joins:\n- name: dim\n  source: c.s.dim\n  using: [id]\n"
        "measures:\n- name: n\n  expr: count(*)\n"
    )
    ossie = importer.convert_metric_view_to_ossie(mv_in)
    join = parse(exporter.convert_ossie_to_metric_view(ossie))["joins"][0]
    assert join.get("using") == ["id"]
    assert "on" not in join


def test_mv_to_ossie_to_mv_is_lossless():
    """MV -> Apache Ossie -> MV is byte-faithful (structurally): the stash carries every
    MV-only feature through Apache Ossie and back."""
    mv_in = load_fixture("fixtureB_metric_view.yaml")
    ossie = importer.convert_metric_view_to_ossie(mv_in)
    mv_out = exporter.convert_ossie_to_metric_view(ossie)
    assert parse(mv_out) == parse(mv_in)


def test_tpcds_mv_round_trips():
    """The TPC-DS Metric View (multi-join star with rely/filter/format) survives
    MV -> Apache Ossie -> MV unchanged."""
    mv_in = load_fixture("tpcds_metric_view.yaml")
    ossie = importer.convert_metric_view_to_ossie(mv_in)
    mv_out = exporter.convert_ossie_to_metric_view(ossie)
    assert parse(mv_out) == parse(mv_in)


def test_one_to_many_round_trips_mv_ossie_mv():
    """A one_to_many Metric View survives MV -> Apache Ossie -> MV: cardinality rides the
    relationship direction (+ stash), and the source/grain rides the model stash, so
    the exporter re-roots at `orders` rather than the FK-sink `line_items`."""
    mv_in = (
        "version: '1.1'\nsource: c.s.orders\ncomment: Orders\n"
        "joins:\n- name: line_items\n  source: c.s.line_items\n"
        "  on: source.order_id = line_items.l_order_id\n"
        "  cardinality: one_to_many\n"
        "dimensions:\n- {name: order_date, expr: o_order_date}\n"
        "measures:\n- {name: order_count, expr: COUNT(*)}\n"
    )
    ossie = importer.convert_metric_view_to_ossie(mv_in)
    mv_out = exporter.convert_ossie_to_metric_view(ossie)
    assert parse(mv_out) == parse(mv_in)


# Property-based round-trip coverage. The Hypothesis driver lives in
# test_roundtrip_properties.py; these run the same generators/assertions under a plain
# seeded RNG so the property coverage also holds where Hypothesis is not installed.

def test_property_mv_to_ossie_to_mv_seeded():
    from _roundtrip_helpers import RandomRnd, assert_mv_roundtrip, build_metric_view
    for seed in range(250):
        assert_mv_roundtrip(build_metric_view(RandomRnd(seed)))


def test_property_ossie_to_mv_to_ossie_seeded():
    from _roundtrip_helpers import RandomRnd, assert_ossie_roundtrip, build_ossie
    for seed in range(250):
        assert_ossie_roundtrip(build_ossie(RandomRnd(1_000_000 + seed)))
