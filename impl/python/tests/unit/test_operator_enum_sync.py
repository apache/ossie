"""Invariant: the two operator enums must stay in lockstep.

The repo has two parallel enumerations of the nine algebra operators:

* :class:`osi.planning.algebra.grain.OperatorTag` — used by the
  symbolic grain simulator. Lives next to the algebra so a simulation
  can run without importing :mod:`osi.planning.plan`.
* :class:`osi.planning.plan.PlanOperation` — used by
  :class:`PlanStep` and codegen.

These two enums need to enumerate exactly the same nine operators with
exactly the same string values. They are kept separate (rather than
collapsed into one) on purpose: the algebra layer must not depend on
the plan-data-model layer (``invariant I-9``). This test is the
single source of truth that they cannot drift.

When you add a new operator (which itself is rare — the closed algebra
has nine), update **both** enums and the assertions below.
"""

from __future__ import annotations

from osi.planning.algebra.grain import OperatorTag
from osi.planning.plan import PlanOperation

_EXPECTED_NAMES = frozenset(
    {
        "SOURCE",
        "FILTER",
        "ENRICH",
        "AGGREGATE",
        "PROJECT",
        "ADD_COLUMNS",
        "MERGE",
        "FILTERING_JOIN",
        "BROADCAST",
    }
)


def test_plan_and_grain_enums_agree_on_names() -> None:
    """Both enums must enumerate exactly the closed-algebra nine."""
    plan_names = {member.name for member in PlanOperation}
    tag_names = {member.name for member in OperatorTag}
    assert plan_names == _EXPECTED_NAMES
    assert tag_names == _EXPECTED_NAMES


def test_plan_and_grain_enums_agree_on_values() -> None:
    """Same names ⇒ same string values, so a test using one matches the other."""
    plan_pairs = {member.name: member.value for member in PlanOperation}
    tag_pairs = {member.name: member.value for member in OperatorTag}
    assert plan_pairs == tag_pairs
