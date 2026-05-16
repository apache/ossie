"""Unit tests for :mod:`osi.planning.algebra.grain` symbolic simulation.

Full property-based coverage lives in
``tests/properties/test_grain_closure.py`` (Phase 1 law §4.4). These
unit tests pin down specific shapes so the symbolic simulator can be
trusted before property generators are pointed at it.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.planning.algebra.grain import (
    AggregateStep,
    BroadcastStep,
    EnrichStep,
    GrainSimulationError,
    MergeStep,
    OperatorTag,
    SimpleStep,
    SourceStep,
    combine_grains,
    is_coarser,
    simulate,
    simulate_grain,
)


def I(s: str) -> str:  # noqa: E743
    return normalize_identifier(s)


class TestSimulateGrain:
    def test_source_only(self):
        pk = frozenset({I("a"), I("b")})
        assert simulate_grain((SourceStep(OperatorTag.SOURCE, pk),)) == pk

    def test_filter_preserves_grain(self):
        pk = frozenset({I("a")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            SimpleStep(OperatorTag.FILTER),
        )
        assert simulate_grain(steps) == pk

    def test_aggregate_coarsens(self):
        pk = frozenset({I("a"), I("b")})
        target = frozenset({I("a")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            AggregateStep(OperatorTag.AGGREGATE, target),
        )
        assert simulate_grain(steps) == target

    def test_aggregate_rejects_coarser_than_parent(self):
        steps = (
            SourceStep(OperatorTag.SOURCE, frozenset({I("a")})),
            AggregateStep(OperatorTag.AGGREGATE, frozenset({I("b")})),
        )
        with pytest.raises(GrainSimulationError):
            simulate_grain(steps)

    def test_merge_requires_matching_grain(self):
        steps = (
            SourceStep(OperatorTag.SOURCE, frozenset({I("a")})),
            MergeStep(OperatorTag.MERGE, frozenset({I("b")})),
        )
        with pytest.raises(GrainSimulationError):
            simulate_grain(steps)

    def test_empty_sequence_rejected(self):
        with pytest.raises(GrainSimulationError):
            simulate_grain(())

    def test_missing_source_rejected(self):
        with pytest.raises(GrainSimulationError):
            simulate_grain((SimpleStep(OperatorTag.FILTER),))

    def test_source_after_start_rejected(self):
        steps = (
            SourceStep(OperatorTag.SOURCE, frozenset({I("a")})),
            SourceStep(OperatorTag.SOURCE, frozenset({I("b")})),
        )
        with pytest.raises(GrainSimulationError):
            simulate_grain(steps)

    def test_simple_step_with_wrong_tag_rejected(self):
        steps = (
            SourceStep(OperatorTag.SOURCE, frozenset({I("a")})),
            SimpleStep(OperatorTag.AGGREGATE),
        )
        with pytest.raises(GrainSimulationError):
            simulate_grain(steps)


class TestEnrichBroadcastSimulation:
    """Simulator tracks single-valued columns from ``enrich``/``broadcast``.

    Without this, aggregating by an enriched-in dimension (the hot path
    for star-schema BI queries) would be rejected by the simulator even
    though the concrete algebra accepts it. The Foundation promotes
    grain to first-class state in the simulator: ``(grain, single_valued)``.
    """

    def test_enrich_preserves_grain_and_extends_single_valued(self) -> None:
        pk = frozenset({I("order_id")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            EnrichStep(
                OperatorTag.ENRICH,
                adds=frozenset({I("region"), I("segment")}),
            ),
        )
        sim = simulate(steps)
        assert sim.grain == pk
        assert {I("region"), I("segment")} <= sim.single_valued

    def test_aggregate_by_enriched_dimension_accepted(self) -> None:
        pk = frozenset({I("order_id")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            EnrichStep(OperatorTag.ENRICH, adds=frozenset({I("region")})),
            AggregateStep(OperatorTag.AGGREGATE, frozenset({I("region")})),
        )
        # Hot path: aggregate by an enriched-in dim. Symbolic must accept.
        sim = simulate(steps)
        assert sim.grain == frozenset({I("region")})

    def test_aggregate_by_enriched_dimension_returns_target_via_simulate_grain(
        self,
    ) -> None:
        pk = frozenset({I("order_id")})
        target = frozenset({I("region")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            EnrichStep(OperatorTag.ENRICH, adds=target),
            AggregateStep(OperatorTag.AGGREGATE, target),
        )
        assert simulate_grain(steps) == target

    def test_broadcast_extends_single_valued(self) -> None:
        pk = frozenset({I("order_id")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            BroadcastStep(OperatorTag.BROADCAST, adds=I("global_total")),
        )
        sim = simulate(steps)
        assert sim.grain == pk
        assert I("global_total") in sim.single_valued

    def test_aggregate_rejects_grain_that_is_neither_in_source_nor_enriched(
        self,
    ) -> None:
        pk = frozenset({I("order_id")})
        steps = (
            SourceStep(OperatorTag.SOURCE, pk),
            EnrichStep(OperatorTag.ENRICH, adds=frozenset({I("region")})),
            AggregateStep(OperatorTag.AGGREGATE, frozenset({I("nope")})),
        )
        with pytest.raises(GrainSimulationError):
            simulate_grain(steps)


class TestIsCoarser:
    def test_equal_grains(self):
        g = frozenset({I("a"), I("b")})
        assert is_coarser(g, g)

    def test_strict_subset(self):
        assert is_coarser(frozenset({I("a")}), frozenset({I("a"), I("b")}))

    def test_not_subset(self):
        assert not is_coarser(frozenset({I("c")}), frozenset({I("a"), I("b")}))


class TestCombineGrains:
    def test_union_of_disjoint(self):
        assert combine_grains(frozenset({I("a")}), frozenset({I("b")})) == frozenset(
            {I("a"), I("b")}
        )

    def test_overlap_deduplicated(self):
        assert combine_grains(
            frozenset({I("a"), I("b")}), frozenset({I("b")})
        ) == frozenset({I("a"), I("b")})

    def test_empty_inputs(self):
        assert combine_grains() == frozenset()
