"""Round-trip tests in both directions."""

from osi_databricks import metric_view_to_osi as importer
from osi_databricks import osi_to_metric_view as exporter
from _util import canon, load_fixture, parse, strip_dropped


def test_osi_to_mv_to_osi():
    """OSI -> MV -> OSI preserves everything except the documented drops
    (model name, primary_key/unique_keys)."""
    osi_in = load_fixture("fixtureA_osi.yaml")
    mv = exporter.convert_osi_to_metric_view(osi_in)
    osi_out = importer.convert_metric_view_to_osi(mv)
    assert strip_dropped(parse(osi_out)) == strip_dropped(parse(osi_in))


def test_using_clause_round_trips():
    """A `using` join survives MV -> OSI -> MV (equal column lists re-emit as `using`)."""
    mv_in = (
        "version: '1.1'\nsource: c.s.fact\n"
        "joins:\n- name: dim\n  source: c.s.dim\n  using: [id]\n"
        "measures:\n- name: n\n  expr: count(*)\n"
    )
    osi = importer.convert_metric_view_to_osi(mv_in)
    join = parse(exporter.convert_osi_to_metric_view(osi))["joins"][0]
    assert join.get("using") == ["id"]
    assert "on" not in join


def test_mv_to_osi_to_mv_is_lossless():
    """MV -> OSI -> MV is byte-faithful (structurally): the stash carries every
    MV-only feature through OSI and back."""
    mv_in = load_fixture("fixtureB_metric_view.yaml")
    osi = importer.convert_metric_view_to_osi(mv_in)
    mv_out = exporter.convert_osi_to_metric_view(osi)
    assert parse(mv_out) == parse(mv_in)


def test_tpcds_mv_round_trips():
    """The TPC-DS Metric View (multi-join star with rely/filter/format) survives
    MV -> OSI -> MV unchanged."""
    mv_in = load_fixture("tpcds_metric_view.yaml")
    osi = importer.convert_metric_view_to_osi(mv_in)
    mv_out = exporter.convert_osi_to_metric_view(osi)
    assert parse(mv_out) == parse(mv_in)


def test_one_to_many_round_trips_mv_osi_mv():
    """A one_to_many Metric View survives MV -> OSI -> MV: cardinality rides the
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
    osi = importer.convert_metric_view_to_osi(mv_in)
    mv_out = exporter.convert_osi_to_metric_view(osi)
    assert parse(mv_out) == parse(mv_in)


# Property-based round-trip coverage. The Hypothesis driver lives in
# test_roundtrip_properties.py; these run the same generators/assertions under a plain
# seeded RNG so the property coverage also holds where Hypothesis is not installed.

def test_property_mv_to_osi_to_mv_seeded():
    from _roundtrip_helpers import RandomRnd, assert_mv_roundtrip, build_metric_view
    for seed in range(250):
        assert_mv_roundtrip(build_metric_view(RandomRnd(seed)))


def test_property_osi_to_mv_to_osi_seeded():
    from _roundtrip_helpers import RandomRnd, assert_osi_roundtrip, build_osi
    for seed in range(250):
        assert_osi_roundtrip(build_osi(RandomRnd(1_000_000 + seed)))
