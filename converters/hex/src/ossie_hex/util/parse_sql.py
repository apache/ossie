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

from dataclasses import dataclass

from hex_sl_utils._vendor.sqlglot import Dialects as SQLGlotDialect
from hex_sl_utils._vendor.sqlglot import exp, parse_one


@dataclass
class ParsedExpr:
    expr: exp.Expression
    dialect: SQLGlotDialect | None


__all__ = [
    "ParsedExpr",
    "SQLGlotDialect",
    "exp",
    "parse_one",
]
