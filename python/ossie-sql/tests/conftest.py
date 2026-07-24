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

"""Shared test fixtures.

Importing ``ossie_sql`` registers the ``"ossie"`` dialect with SQLGlot (via
the ``Ossie(Dialect)`` subclass's registration metaclass), so every test
module can call ``sqlglot.parse_one(sql, read="ossie")`` /
``expression.sql(dialect="ossie")`` without an explicit import of
``ossie_sql.dialect``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlglot
from sqlglot import exp

import ossie_sql  # noqa: F401  (registers the "ossie" dialect as a side effect)


@pytest.fixture
def parse_ossie() -> Callable[[str], exp.Expr]:
    """Return a helper that parses SQL text with the Ossie dialect."""

    def _parse(sql: str) -> exp.Expr:
        return sqlglot.parse_one(sql, read="ossie")

    return _parse
