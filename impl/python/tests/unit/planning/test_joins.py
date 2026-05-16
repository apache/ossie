"""Unit tests for :mod:`osi.planning.joins`.

Exercises the path-finding helper :func:`find_enrichment_path` end-to-end:
N:1 success, unreachable target, ambiguous join path, and N:N rejection.
Error codes asserted: ``E2004`` (unreachable), ``E3001`` (ambiguous),
``E3011`` (M:N).
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning.algebra.operations import JoinType
from osi.planning.joins import JoinStep, find_enrichment_path
from tests.unit.planning.fixtures import mn_context, orders_context


class TestFindEnrichmentPath:
    def test_empty_targets_return_no_steps(self) -> None:
        ctx = orders_context()
        steps = find_enrichment_path(
            root=normalize_identifier("orders"),
            targets=frozenset(),
            graph=ctx.graph,
        )
        assert steps == ()

    def test_root_in_targets_is_not_planned(self) -> None:
        ctx = orders_context()
        steps = find_enrichment_path(
            root=normalize_identifier("orders"),
            targets=frozenset({normalize_identifier("orders")}),
            graph=ctx.graph,
        )
        assert steps == ()

    def test_single_hop_N_TO_ONE(self) -> None:
        ctx = orders_context()
        steps = find_enrichment_path(
            root=normalize_identifier("orders"),
            targets=frozenset({normalize_identifier("customers")}),
            graph=ctx.graph,
        )
        assert len(steps) == 1
        step = steps[0]
        assert isinstance(step, JoinStep)
        assert step.parent == normalize_identifier("orders")
        assert step.child == normalize_identifier("customers")
        # S-1: referential_integrity is deferred (Foundation v0.1
        # uses the LEFT default per D-001 / D-004 — see S-7). Without
        # the RI hint the enrichment path defaults to LEFT for every
        # N:1 enrichment.
        assert step.join_type is JoinType.LEFT

    def test_left_join_for_returns_to_customers(self) -> None:
        ctx = orders_context()
        steps = find_enrichment_path(
            root=normalize_identifier("returns"),
            targets=frozenset({normalize_identifier("customers")}),
            graph=ctx.graph,
        )
        assert steps[0].join_type is JoinType.LEFT

    def test_fan_trap_path_raises_E3011(self) -> None:
        """Surface a reachable-but-unsafe path as ``E3011``.

        ``orders → customers → returns`` has a path through customers,
        but the second hop is the 1→N reverse of ``returns_to_customers``
        (a fan trap). The planner surfaces this as ``E3011`` rather than
        ``E2004``: the target is reachable, it just isn't safely
        enrichable.
        """
        ctx = orders_context()
        with pytest.raises(OSIPlanningError) as excinfo:
            find_enrichment_path(
                root=normalize_identifier("orders"),
                targets=frozenset({normalize_identifier("returns")}),
                graph=ctx.graph,
            )
        assert excinfo.value.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED

    def test_truly_unreachable_target_E2004(self) -> None:
        """Raise ``E2004`` when no relationships connect root to target."""
        import textwrap

        from osi.parsing.graph import build_graph
        from osi.parsing.parser import parse_semantic_model

        disconnected = textwrap.dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: a
                    source: s.a
                    primary_key: [id]
                    fields:
                      - {name: id, expression: id, role: dimension}
                  - name: b
                    source: s.b
                    primary_key: [id]
                    fields:
                      - {name: id, expression: id, role: dimension}
            """)
        parsed = parse_semantic_model(disconnected)
        graph = build_graph(parsed.model)
        with pytest.raises(OSIPlanningError) as excinfo:
            find_enrichment_path(
                root=normalize_identifier("a"),
                targets=frozenset({normalize_identifier("b")}),
                graph=graph,
            )
        assert excinfo.value.code is ErrorCode.E2004_UNREACHABLE_DATASET

    def test_M_N_edge_rejected_E3012(self) -> None:
        """Declared N:N with no bridge / stitch / EXISTS_IN → ``E3012``.

        Per ``Proposed_OSI_Semantics.md §6.8 Semantic guarantee`` the
        user-facing per-query M:N failure surface for an M:N-supporting
        engine is ``E3012_MN_NO_STITCH_PATH`` (or ``E3013`` for the
        two-fact stitch case). ``E3011`` is reserved for the
        engine-capability opt-out (vendor declaring no M:N support at
        all) and never appears at the user-facing surface for
        ``osi_python``. The classifier upgrades the algebra-internal
        ``E3011`` precondition signal to ``E3012`` whenever the unsafe
        edge is N:N, surfacing the actionable resolution routes in the
        error message.
        """
        ctx = mn_context()
        with pytest.raises(OSIPlanningError) as excinfo:
            find_enrichment_path(
                root=normalize_identifier("grade_logs"),
                targets=frozenset({normalize_identifier("courses")}),
                graph=ctx.graph,
            )
        assert excinfo.value.code is ErrorCode.E3012_MN_NO_STITCH_PATH
