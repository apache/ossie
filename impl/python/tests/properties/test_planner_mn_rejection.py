"""Law §4.12 — M:N Rejection at plan level.

Extends :mod:`tests.properties.test_mn_rejection` from the algebra to
the planner: any :class:`SemanticQuery` whose enrichment chain crosses
an N:N relationship with no bridge / stitch / EXISTS_IN route must
raise ``E3012_MN_NO_SAFE_REWRITE`` before any SQL is produced.

``E3011_MN_AGGREGATION_REJECTED`` is reserved as the engine-capability
opt-out (per ``Proposed_OSI_Semantics.md §6.8 Semantic guarantee``) —
emitted by engines that do not support M:N at all. This reference
implementation supports M:N, so the user-facing per-query failure surface is
``E3012`` / ``E3013``, not ``E3011``. The algebra layer raises
``E3011`` internally as a precondition signal on N:N edges; the
planner reclassifies it to the per-query code before returning.

Property target: :mod:`osi.planning.joins` — specifically
:func:`_classify_unsafe_step`.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIError
from osi.planning import Reference, SemanticQuery, plan
from tests.unit.planning.fixtures import mn_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


@pytest.mark.parametrize(
    "dimension,measure",
    [
        (_ref("courses", "subject"), _ref("grade_logs", "avg_grade")),
        (_ref("courses", "title"), _ref("grade_logs", "avg_grade")),
    ],
)
def test_any_query_spanning_m_n_edge_raises_E3012(
    dimension: Reference, measure: Reference
) -> None:
    ctx = mn_context()
    query = SemanticQuery(dimensions=(dimension,), measures=(measure,))
    with pytest.raises(OSIError) as excinfo:
        plan(query, ctx)
    assert excinfo.value.code is ErrorCode.E3012_MN_NO_SAFE_REWRITE
