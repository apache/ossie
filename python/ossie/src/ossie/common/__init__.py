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

"""Shared primitives used by all layers.

Contains:

- :mod:`ossie.common.identifiers` — ``Identifier`` NewType, normalization,
  validation.
- :mod:`ossie.common.sql_expr` — thin wrappers over SQLGlot for frozen,
  comparable ASTs.
- :mod:`ossie.common.types` — cross-layer NewTypes (``DimensionSet``,
  ``CTEName``, ``ExpressionId``, ``SourceLocation``).
"""

from ossie.common.identifiers import (
    Identifier,
    identifiers_equal,
    is_valid_identifier,
    normalize_identifier,
)
from ossie.common.sql_expr import FrozenSQL, parse_sql_expr, sql_expr_equal
from ossie.common.types import CTEName, DimensionSet, ExpressionId, SourceLocation

__all__ = [
    "CTEName",
    "DimensionSet",
    "ExpressionId",
    "FrozenSQL",
    "Identifier",
    "SourceLocation",
    "identifiers_equal",
    "is_valid_identifier",
    "normalize_identifier",
    "parse_sql_expr",
    "sql_expr_equal",
]
