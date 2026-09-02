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

"""The catalog must cover the specification's construct inventory, one to one.

This test reads the UPSTREAM core-spec/expression_language.md rather than any
document of our own. Oracling against our own mapping notes would only prove we
are self-consistent; reading the spec means a construct added upstream fails this
build instead of silently going unsupported.

spec_construct_names() and the mapping document's 146-row census count by
different units — one parseable table row/heading vs. one construct under rule
E1, which also counts a handful of constructs the spec only describes in prose.
CONVENTION_DIVERGENCES (catalog.py) is the exact, reasoned list of the 9 where
that difference shows up; test_the_two_counts_reconcile pins the arithmetic so
the two counts cannot drift apart silently.
"""
from ossie_thoughtspot.expressions import CATALOG, CONVENTION_DIVERGENCES, spec_construct_names


def test_every_spec_construct_has_a_catalog_entry():
    missing = spec_construct_names() - set(CATALOG)
    assert missing == set(), f"constructs in the spec with no catalog entry: {sorted(missing)}"


def test_no_catalog_entry_invents_a_construct_the_spec_does_not_have():
    # CONVENTION_DIVERGENCES is the one deliberate exception: constructs the
    # mapping document counts as their own row that core-spec/expression_language.md
    # never gives a discrete table row of their own (see catalog.py for why, per
    # entry). Everything else in CATALOG must trace to a real spec row.
    invented = set(CATALOG) - spec_construct_names() - set(CONVENTION_DIVERGENCES)
    assert invented == set(), (
        f"catalog entries not found in the spec or CONVENTION_DIVERGENCES: {sorted(invented)}"
    )


def test_the_two_counts_reconcile():
    # The spec's parseable rows (137) plus the deliberate divergences (9) must
    # equal the mapping document's rule-E1 census (146). If this drifts, either
    # spec_construct_names() regressed or CONVENTION_DIVERGENCES needs an entry
    # added or removed - it must not be "fixed" by changing the 146 constant.
    assert len(spec_construct_names()) + len(CONVENTION_DIVERGENCES) == 146


def test_the_total_matches_the_mapping_document_census():
    # 146 is the figure the mapping document's coverage summary reports, arrived at
    # by rule E1 (one row per construct; argument vocabularies are not constructs).
    assert len(CATALOG) == 146
