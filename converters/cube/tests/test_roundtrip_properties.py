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

"""Property-based round-trip tests over generated Cube models.

The generators live in `_roundtrip_helpers` and depend only on a tiny
chance/count/pick/text interface, so the same model space is explored whether
Hypothesis is installed or not. Without it, a seeded sweep runs instead -- the
properties are still checked in CI on a Python where hypothesis fails to build.
"""

import pytest
from _roundtrip_helpers import RandomRnd, build_cube_model, check_model

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover - exercised only without hypothesis
    HAVE_HYPOTHESIS = False


SEEDS = list(range(60))


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_models_roundtrip(seed):
    """A deterministic sweep, so a failure names a reproducible seed."""
    check_model(build_cube_model(RandomRnd(seed)))


if HAVE_HYPOTHESIS:
    class _HypothesisRnd:
        """The `Rnd` interface backed by a Hypothesis data strategy."""

        def __init__(self, data):
            self.data = data

        def chance(self, p=0.5):
            # `st.booleans()` is unweighted, so it would ignore `p` and explore a
            # different distribution than RandomRnd -- defeating the point of the
            # two drivers sharing one generator. Drawn so the minimal value (0)
            # means False, which shrinks toward the smallest model rather than the
            # largest.
            return self.data.draw(
                st.integers(min_value=0, max_value=99)) >= 100 - round(p * 100)

        def count(self, lo, hi):
            return self.data.draw(st.integers(min_value=lo, max_value=hi))

        def pick(self, seq):
            return self.data.draw(st.sampled_from(list(seq)))

        def text(self):
            # Printable, no leading/trailing whitespace and no newlines, so the
            # value survives a YAML dump/load cycle verbatim. Round-tripping
            # arbitrary Unicode is a PyYAML property, not a converter one.
            #
            # Jinja delimiters are excluded because they are out of the
            # round-trippable subset by design: the converter treats a file
            # containing them as templated and preserves it whole, exactly as
            # Cube's own CubeSchemaConverter does. That behavior has its own
            # targeted test.
            return self.data.draw(st.text(
                alphabet=st.characters(min_codepoint=32, max_codepoint=126),
                min_size=1, max_size=24,
            ).map(str.strip).filter(
                lambda s: s and not s.startswith("#")
                and not any(t in s for t in ("{{", "}}", "{%", "%}"))))

    @settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(st.data())
    def test_generated_models_roundtrip(data):
        check_model(build_cube_model(_HypothesisRnd(data)))
