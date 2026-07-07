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

"""Property-based round-trip tests (Hypothesis).

For any generated model in the round-trippable subset, converting one direction and
back preserves content:

  - MV  -> Apache Ossie -> MV : source, every dimension/measure name+expression+metadata, every
    join (name, source, condition, cardinality, rely) and the join nesting, and the
    model-level filter/comment/materialization.
  - Apache Ossie -> MV -> Apache Ossie : dataset names+sources+fields, relationship from/to/columns,
    metric name+expression, and the model description.

The model generation and the assertions live in `_roundtrip_helpers` (no test-framework
dependency) so the exact same logic also runs under a plain seeded RNG where Hypothesis
is unavailable (see the `*_seeded` tests in test_roundtrip.py). This file is the thin
Hypothesis driver: it maps draws into the shared `Rnd` interface and runs the properties.

Run: `pytest test_roundtrip_properties.py` (needs `hypothesis`).
"""

import pytest

pytest.importorskip("hypothesis")  # skip cleanly if hypothesis is not installed

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _roundtrip_helpers import (
    assert_mv_roundtrip,
    assert_osi_roundtrip,
    build_metric_view,
    build_osi,
)

# Alphanumeric metadata text with optional interior spaces (no leading/trailing space,
# no YAML-special characters), so values survive a dump/load cycle verbatim.
_safe_text = st.from_regex(r"[A-Za-z0-9]([A-Za-z0-9 ]{0,18}[A-Za-z0-9])?", fullmatch=True)
# A SQL column-style identifier.
_colident = st.from_regex(r"[a-z_][a-z0-9_]{0,7}", fullmatch=True)

_SETTINGS = settings(
    max_examples=300,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


class _HypothesisRnd:
    """The `Rnd` interface backed by a Hypothesis `draw`. `chance(p)` ignores `p`
    (Hypothesis explores both branches regardless)."""

    def __init__(self, draw):
        self._draw = draw

    def chance(self, p=0.5):
        return self._draw(st.booleans())

    def count(self, lo, hi):
        return self._draw(st.integers(min_value=lo, max_value=hi))

    def pick(self, seq):
        return self._draw(st.sampled_from(list(seq)))

    def text(self):
        return self._draw(_safe_text)

    def colname(self):
        return self._draw(_colident)


@st.composite
def metric_views(draw):
    return build_metric_view(_HypothesisRnd(draw))


@st.composite
def osi_models(draw):
    return build_osi(_HypothesisRnd(draw))


class TestMetricViewRoundTrip:
    @given(mv=metric_views())
    @_SETTINGS
    def test_mv_to_osi_to_mv(self, mv):
        assert_mv_roundtrip(mv)


class TestOSIRoundTrip:
    @given(osi=osi_models())
    @_SETTINGS
    def test_osi_to_mv_to_osi(self, osi):
        assert_osi_roundtrip(osi)
