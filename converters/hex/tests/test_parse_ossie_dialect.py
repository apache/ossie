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

import pytest
from ossie import OSIDialect

from ossie_hex.ossie_types import parse_ossie_dialect
from ossie_hex.util.errors import ConversionError


def test_parse_dialect_accepts_either_case_and_an_enum() -> None:
    assert parse_ossie_dialect("snowflake") == OSIDialect.SNOWFLAKE
    assert parse_ossie_dialect("SNOWFLAKE") == OSIDialect.SNOWFLAKE
    assert parse_ossie_dialect(OSIDialect.SNOWFLAKE) == OSIDialect.SNOWFLAKE


def test_parse_dialect_rejects_an_unknown_name() -> None:
    with pytest.raises(ConversionError, match="Unknown OSI dialect 'klingon'"):
        parse_ossie_dialect("klingon")
