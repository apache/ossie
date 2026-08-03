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

"""Property-based round-trip tests.

Two drivers over the same builders (see _roundtrip_helpers):

  - Hypothesis, when installed: minimal failing examples and example databases.
  - A plain seeded random.Random sweep otherwise, so the property logic still
    runs in minimal environments.
"""

import pytest

from _roundtrip_helpers import (
    RandomRnd,
    assert_omni_roundtrip,
    assert_osi_roundtrip,
    build_omni,
    build_osi,
)

try:
    from hypothesis import given, settings, strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    HAVE_HYPOTHESIS = False


if HAVE_HYPOTHESIS:

    class _HypRnd:
        """The Rnd interface driven by hypothesis' `data` strategy."""

        def __init__(self, data):
            self.data = data

        def chance(self, p=0.5):
            return self.data.draw(st.floats(0, 1)) < p

        def count(self, lo, hi):
            return self.data.draw(st.integers(lo, hi))

        def pick(self, seq):
            return self.data.draw(st.sampled_from(list(seq)))

        def text(self):
            alnum = st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                                  max_codepoint=0x7A)
            head = self.data.draw(st.text(alphabet=alnum, min_size=1, max_size=1))
            body = self.data.draw(st.text(
                alphabet=st.sampled_from(
                    list("abcdefghijklmnopqrstuvwxyz"
                         "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")),
                max_size=10))
            return (head + body).strip()

        def colname(self):
            first = self.data.draw(st.sampled_from(
                list("abcdefghijklmnopqrstuvwxyz")))
            rest = self.data.draw(st.text(
                alphabet=st.sampled_from(
                    list("abcdefghijklmnopqrstuvwxyz0123456789_")),
                max_size=7))
            return first + rest

    @given(st.data())
    @settings(max_examples=50, deadline=None)
    def test_omni_roundtrip_property(data):
        assert_omni_roundtrip(build_omni(_HypRnd(data)))

    @given(st.data())
    @settings(max_examples=50, deadline=None)
    def test_osi_roundtrip_property(data):
        assert_osi_roundtrip(build_osi(_HypRnd(data)))

else:  # pragma: no cover - exercised only without hypothesis

    @pytest.mark.parametrize("seed", range(50))
    def test_omni_roundtrip_seeded(seed):
        assert_omni_roundtrip(build_omni(RandomRnd(seed)))

    @pytest.mark.parametrize("seed", range(50))
    def test_osi_roundtrip_seeded(seed):
        assert_osi_roundtrip(build_osi(RandomRnd(seed)))
