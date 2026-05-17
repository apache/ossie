"""Unit tests for :mod:`osi.planning.planner_bridge`.

The bridge resolution path turns an unsafe M:N traversal into a safe
plan when an intermediate bridge dataset exists (``D-026`` / ``D-027``,
``Proposed_OSI_Semantics.md §6.8.1``). These tests pin the discovery
and applicability helpers in isolation so a regression in either lands
in this file rather than fanning out to e2e SQL diffs:

* :func:`find_bridge_resolutions` — pure discovery over a
  :class:`RelationshipGraph`.
* :func:`can_apply_bridge_resolution` — precheck over a
  :class:`MeasureGroup` carrying resolved metrics.
* :func:`_resolved_bridge_unique` (indirectly via
  :func:`find_bridge_resolutions` + planner integration) — ambiguous
  bridge raises ``E3001_AMBIGUOUS_JOIN_PATH`` and the remediation
  message no longer suggests the deferred ``joins.using_relationships``
  surface (F-18).
"""

from __future__ import annotations

import textwrap

import pytest

from osi.common.identifiers import normalize_identifier
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.parser import parse_semantic_model
from osi.planning.planner_bridge import (
    can_apply_bridge_resolution,
    find_bridge_resolutions,
)
from osi.planning.planner_mn import MeasureGroup
from osi.planning.resolve import ResolvedMetric

_BRIDGE_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: bridge_demo
        dialect: ANSI_SQL
        datasets:
          - name: students
            source: schools.students
            primary_key: [student_id]
            fields:
              - {name: student_id, expression: student_id, role: dimension}
              - {name: name,       expression: name,       role: dimension}
          - name: courses
            source: schools.courses
            primary_key: [course_id]
            fields:
              - {name: course_id, expression: course_id, role: dimension}
              - {name: title,     expression: title,     role: dimension}
          - name: enrollments
            source: schools.enrollments
            primary_key: [enrollment_id]
            fields:
              - {name: enrollment_id, expression: enrollment_id, role: dimension}
              - {name: student_id,    expression: student_id,    role: dimension}
              - {name: course_id,     expression: course_id,     role: dimension}
              - {name: credit,        expression: credit,        role: fact}
        relationships:
          - {name: enroll_to_student, from: enrollments, to: students, from_columns: [student_id], to_columns: [student_id]}
          - {name: enroll_to_course,  from: enrollments, to: courses,  from_columns: [course_id],  to_columns: [course_id]}
        metrics:
          - {name: total_credits, expression: "SUM(enrollments.credit)"}
          - {name: avg_credit,    expression: "AVG(enrollments.credit)"}
    """)


def _ctx():
    return parse_semantic_model(_BRIDGE_MODEL, flags=FoundationFlags.strict())


def _id(s: str):
    return normalize_identifier(s)


class TestFindBridgeResolutions:
    """``find_bridge_resolutions`` discovers safe (fact, bridge, target) triples."""

    def test_no_outstanding_targets_returns_empty(self) -> None:
        parsed = _ctx()
        # ``enrollments`` is the fact; its safe-reachable closure
        # already includes both dim datasets, so there's nothing for
        # a bridge to resolve.
        bridges = find_bridge_resolutions(
            fact=_id("enrollments"),
            needed=frozenset({_id("students"), _id("courses")}),
            graph=parsed.graph,
        )
        assert bridges == ()

    def test_bridge_route_from_dim_fact_view(self) -> None:
        """Querying a fact via a dim-side anchor surfaces the bridge.

        Anchor ``students`` cannot reach ``courses`` directly — only
        through ``enrollments``. ``enrollments`` therefore acts as the
        bridge between the (students)-safe closure and the outstanding
        ``courses`` target.
        """
        parsed = _ctx()
        bridges = find_bridge_resolutions(
            fact=_id("students"),
            needed=frozenset({_id("courses")}),
            graph=parsed.graph,
        )
        assert bridges, "expected a bridge candidate; got none"
        bridge_ds = {b.bridge for b in bridges}
        assert _id("enrollments") in bridge_ds
        # Every candidate's left link must sit in the fact's safe set
        # (``students`` itself, plus reflexive entries) and the right
        # target must be the outstanding ``courses``.
        for b in bridges:
            assert b.right_target == _id("courses")
            assert b.left_link == _id("students")


class TestCanApplyBridgeResolution:
    """``can_apply_bridge_resolution`` precheck on the metric shape."""

    def test_no_measures_rejected(self) -> None:
        group = MeasureGroup(fact_dataset=_id("enrollments"), measures=())
        applicable, reason = can_apply_bridge_resolution(group)
        assert not applicable
        assert reason is not None and "at least one measure" in reason

    def test_distributive_sum_accepted(self) -> None:
        parsed = _ctx()
        total_credits = parsed.model.metrics[0]  # total_credits
        resolved = ResolvedMetric(dataset=None, metric=total_credits)
        group = MeasureGroup(fact_dataset=_id("enrollments"), measures=(resolved,))
        applicable, reason = can_apply_bridge_resolution(group)
        assert applicable, reason
        assert reason is None

    def test_avg_rejected_with_distributive_hint(self) -> None:
        """``AVG`` over a bridge is the D-027 gap surfaced in F-17."""
        parsed = _ctx()
        avg_credit = parsed.model.metrics[1]  # avg_credit
        resolved = ResolvedMetric(dataset=None, metric=avg_credit)
        group = MeasureGroup(fact_dataset=_id("enrollments"), measures=(resolved,))
        applicable, reason = can_apply_bridge_resolution(group)
        assert not applicable
        assert reason is not None
        assert "SUM" in reason and "MIN" in reason


class TestAmbiguousBridgeMessage:
    """The ambiguous-bridge diagnostic must NOT suggest deferred keys (F-18)."""

    def test_ambiguous_bridge_error_avoids_deferred_suggestion(self) -> None:
        # Build a model with two equally-good bridge datasets so we
        # can trigger the ambiguity path through the public planner
        # entry point.
        model = textwrap.dedent("""\
            semantic_model:
              - name: ambig_bridges
                dialect: ANSI_SQL
                datasets:
                  - name: courses
                    source: schools.courses
                    primary_key: [course_id]
                    fields:
                      - {name: course_id, expression: course_id, role: dimension}
                      - {name: title,     expression: title,     role: dimension}
                  - name: instructors
                    source: schools.instructors
                    primary_key: [instructor_id]
                    fields:
                      - {name: instructor_id, expression: instructor_id, role: dimension}
                      - {name: name,          expression: name,          role: dimension}
                  - name: assignments_a
                    source: schools.assignments_a
                    primary_key: [aid]
                    fields:
                      - {name: aid,           expression: aid,           role: dimension}
                      - {name: course_id,     expression: course_id,     role: dimension}
                      - {name: instructor_id, expression: instructor_id, role: dimension}
                  - name: assignments_b
                    source: schools.assignments_b
                    primary_key: [bid]
                    fields:
                      - {name: bid,           expression: bid,           role: dimension}
                      - {name: course_id,     expression: course_id,     role: dimension}
                      - {name: instructor_id, expression: instructor_id, role: dimension}
                relationships:
                  - {name: a_course,     from: assignments_a, to: courses,      from_columns: [course_id],     to_columns: [course_id]}
                  - {name: a_instructor, from: assignments_a, to: instructors,  from_columns: [instructor_id], to_columns: [instructor_id]}
                  - {name: b_course,     from: assignments_b, to: courses,      from_columns: [course_id],     to_columns: [course_id]}
                  - {name: b_instructor, from: assignments_b, to: instructors,  from_columns: [instructor_id], to_columns: [instructor_id]}
            """)
        parsed = parse_semantic_model(model, flags=FoundationFlags.strict())
        # Both ``assignments_a`` and ``assignments_b`` can bridge
        # courses ↔ instructors. ``find_bridge_resolutions`` returns
        # both, and the planner-side dedup raises E3001.
        bridges = find_bridge_resolutions(
            fact=_id("courses"),
            needed=frozenset({_id("instructors")}),
            graph=parsed.graph,
        )
        bridge_ds = {b.bridge for b in bridges}
        assert {_id("assignments_a"), _id("assignments_b")}.issubset(bridge_ds)

        # The ambiguity surface itself is exercised inside the planner;
        # the diagnostic message must not suggest the deferred
        # ``joins.using_relationships`` surface — that key would
        # itself be rejected with ``E_DEFERRED_KEY_REJECTED``.
        from osi.planning.planner_bridge import _resolved_bridge_unique

        with pytest.raises(OSIPlanningError) as excinfo:
            _resolved_bridge_unique(bridges)
        assert excinfo.value.code is ErrorCode.E3001_AMBIGUOUS_JOIN_PATH
        message = str(excinfo.value)
        # F-18: the user-actionable remediation must point at
        # supported surfaces (model restructure / rename), not at the
        # deferred ``joins.using_relationships`` key. The message MAY
        # mention the deferred surface in a parenthetical "this is
        # why we don't suggest X" note — but the *primary* advice
        # must be a supported one.
        assert "Restructure" in message
        assert (
            "deferred" in message.lower() and "E_DEFERRED_KEY_REJECTED" in message
        ), "should explain that per-metric using_relationships is deferred"
