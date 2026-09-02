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

from ossie_thoughtspot import constants


def test_vendor_key_and_dialect_are_distinct_constants():
    # P6: same value today, different upstream governance. Must not be one name.
    assert constants.VENDOR_KEY == "THOUGHTSPOT"
    assert constants.DIALECT == "THOUGHTSPOT"
    # Both names must exist independently, so a later divergence touches one call site.
    assert "VENDOR_KEY" in vars(constants)
    assert "DIALECT" in vars(constants)


def test_dialect_is_registered_upstream():
    # apache/ossie#351 merged 2026-09-01: THOUGHTSPOT is a registered Dialect.
    # ANSI_SQL is still emitted alongside it for portable expressions (P8).
    assert constants.DIALECT_IS_REGISTERED is True
    assert constants.PORTABLE_DIALECT == "ANSI_SQL"


def test_spec_series_is_major_minor_not_an_exact_version():
    # Upstream's first release is proposed as 0.3.0; an exact pin on 0.2.0.dev0 would break.
    assert constants.SPEC_SERIES == "0.2"
    assert constants.STASH_VERSION == 1
