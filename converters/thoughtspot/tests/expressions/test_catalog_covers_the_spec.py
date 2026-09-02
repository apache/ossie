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
"""
import pytest

from ossie_thoughtspot.expressions import CATALOG, spec_construct_names


@pytest.mark.xfail(reason="catalog is populated across Tasks 2-7", strict=True)
def test_every_spec_construct_has_a_catalog_entry():
    missing = spec_construct_names() - set(CATALOG)
    assert missing == set(), f"constructs in the spec with no catalog entry: {sorted(missing)}"


def test_no_catalog_entry_invents_a_construct_the_spec_does_not_have():
    invented = set(CATALOG) - spec_construct_names()
    assert invented == set(), f"catalog entries not found in the spec: {sorted(invented)}"


@pytest.mark.xfail(reason="catalog is populated across Tasks 2-7", strict=True)
def test_the_total_matches_the_mapping_document_census():
    # 146 is the figure the mapping document's coverage summary reports, arrived at
    # by rule E1 (one row per construct; argument vocabularies are not constructs).
    assert len(CATALOG) == 146
