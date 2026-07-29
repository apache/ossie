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

"""Shared patterns for Ossie model expressions."""

from __future__ import annotations

import re

from ossie import OSIDialect

OSSIE_VERSION = "0.2.0.dev0"
OSI_DIALECTS = [dialect.value for dialect in OSIDialect]

# Matcher for an Ossie ``Field``, such as ``order_id``.
# Quoted identifiers and compound SQL expressions are intentionally excluded.
OSSIE_FIELD_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
OSSIE_FIELD_RE = re.compile(OSSIE_FIELD_PATTERN)


# Matcher for the qualified form of an Ossie ``FieldExpr``, such as
# ``customers.id``. Ossie defines ``FieldExpr`` as one or two fields.
OSSIE_QUALIFIED_FIELD_EXPR_PATTERN = (
    r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$"
)
OSSIE_QUALIFIED_FIELD_EXPR_RE = re.compile(OSSIE_QUALIFIED_FIELD_EXPR_PATTERN)


def is_ossie_field(expr: str | None) -> bool:
    """
    True if ``expr`` is an Ossie ``Field``, such as ``order_id``. Does not
    match a qualified field expression, such as ``customers.id``.
    """
    return isinstance(expr, str) and bool(OSSIE_FIELD_RE.match(expr.strip()))
