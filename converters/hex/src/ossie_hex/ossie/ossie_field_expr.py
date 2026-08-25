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

import re

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
"""The Ossie expression_language.md spec states that identifiers must follow
ANSI SQL naming rules:

    - First character: Must be a letter (A-Z, case-insensitive by default).
    - Subsequent characters: Letters, numbers (0-9), or underscores (_).
    - Length: Usually up to 128 characters.
    - Reserved words: Cannot be an unquoted SQL reserved keyword.
"""

# Matcher for the unqualified form of an Ossie field expression, such as `field`
# Can be used to validate a standalone field expression
OSSIE_UNQUALIFIED_FIELD_EXPR_PATTERN = rf"^{_IDENTIFIER}$"
OSSIE_UNQUALIFIED_FIELD_EXPR_RE = re.compile(OSSIE_UNQUALIFIED_FIELD_EXPR_PATTERN)


# Matcher for the qualified form of an Ossie field expression, such as `dataset.field`
# Can be used to validate a standalone field expression
OSSIE_QUALIFIED_FIELD_EXPR_PATTERN = rf"^({_IDENTIFIER})\.({_IDENTIFIER})$"
OSSIE_QUALIFIED_FIELD_EXPR_RE = re.compile(OSSIE_QUALIFIED_FIELD_EXPR_PATTERN)


# Matcher for the qualified form of an Ossie field expression, left unanchored.
# Can be used to find references embedded in a larger expression
OSSIE_QUALIFIED_FIELD_EXPR_SCAN_PATTERN = rf"\b({_IDENTIFIER})\.({_IDENTIFIER})\b"
OSSIE_QUALIFIED_FIELD_EXPR_SCAN_RE = re.compile(
    rf"\b({_IDENTIFIER})\.({_IDENTIFIER})\b"
)
