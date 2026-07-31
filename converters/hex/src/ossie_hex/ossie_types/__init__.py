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

from .ossie_common import OSSIE_DIALECTS, OSSIE_VERSION, parse_ossie_dialect
from .ossie_field import (
    OSSIE_FIELD_PATTERN,
    OSSIE_FIELD_RE,
    OSSIE_QUALIFIED_FIELD_EXPR_PATTERN,
    OSSIE_QUALIFIED_FIELD_EXPR_RE,
    OSSIE_QUALIFIED_FIELD_EXPR_SCAN_RE,
    is_ossie_field,
)

__all__ = [
    "OSSIE_DIALECTS",
    "OSSIE_FIELD_PATTERN",
    "OSSIE_FIELD_RE",
    "OSSIE_QUALIFIED_FIELD_EXPR_PATTERN",
    "OSSIE_QUALIFIED_FIELD_EXPR_RE",
    "OSSIE_QUALIFIED_FIELD_EXPR_SCAN_RE",
    "OSSIE_VERSION",
    "is_ossie_field",
    "parse_ossie_dialect",
]
