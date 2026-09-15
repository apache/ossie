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

"""SQLGlot dialect and validation helpers for the Ossie expression language.

See ``core-spec/expression_language.md`` for the language spec this package
implements, and this package's README for scope and usage.
"""

from ossie_sql.dialect import Ossie
from ossie_sql.functions import ComplianceLevel, compliance_level
from ossie_sql.identifiers import (
    InvalidIdentifierError,
    identifiers_equal,
    normalize_identifier,
    normalize_identifier_text,
)
from ossie_sql.validate import UnsupportedConstructError, validate_expression

__all__ = [
    "ComplianceLevel",
    "InvalidIdentifierError",
    "Ossie",
    "UnsupportedConstructError",
    "compliance_level",
    "identifiers_equal",
    "normalize_identifier",
    "normalize_identifier_text",
    "validate_expression",
]
