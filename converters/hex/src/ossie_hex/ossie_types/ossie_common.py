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

from __future__ import annotations

from ossie import OSIDialect

from ..util.errors import ConversionError

OSSIE_VERSION = "0.2.0.dev0"
OSSIE_DIALECTS = [dialect.value for dialect in OSIDialect]


def parse_ossie_dialect(dialect: OSIDialect | str) -> OSIDialect:
    """Coerce a dialect name to an ``OSIDialect``, rejecting anything unknown."""
    raw = dialect.value if isinstance(dialect, OSIDialect) else str(dialect)
    try:
        return OSIDialect(raw.upper())
    except ValueError:
        supported = ", ".join(OSSIE_DIALECTS)
        raise ConversionError(
            f"Unknown OSI dialect '{dialect}'; expected one of {supported}"
        ) from None
