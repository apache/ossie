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

"""Shared checks for the one finance Ossie document."""

from __future__ import annotations

import re
import sys

import yaml

SPEC_VERSION = "0.2.0.dev0"
BARE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_AGGREGATE = "SUM(bookings.amount)"

# Snowflake semantic-view data_type to Ossie datatype. This fixture only.
SNOWFLAKE_TO_OSSIE = {
    "VARCHAR": "String",
    "NUMBER(38,0)": "Integer",
    "NUMBER(18,2)": "Decimal",
    "DATE": "Date",
}
OSSIE_TO_SNOWFLAKE = {ossie: snowflake for snowflake, ossie in SNOWFLAKE_TO_OSSIE.items()}

# Ossie datatype to the Wisdom column dataType used in the format 1.0 subset.
OSSIE_TO_WISDOM = {
    "String": "VARCHAR",
    "Integer": "INT64",
    "Decimal": "DECIMAL",
    "Date": "DATE",
}
WISDOM_TO_OSSIE = {wisdom: ossie for ossie, wisdom in OSSIE_TO_WISDOM.items()}


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def reject_key(key: str) -> None:
    print(key, file=sys.stderr)
    raise SystemExit(1)


def require_keys(node: object, allowed: set[str]) -> dict:
    if not isinstance(node, dict):
        die("mapping")
    for key in node:
        if key not in allowed:
            reject_key(str(key))
    return node


def check_expression(expression: str) -> None:
    if expression == ALLOWED_AGGREGATE or BARE_COLUMN.fullmatch(expression):
        return
    die(f"unsupported expression: {expression}")


def referenced_datasets(expression: str, dataset_names: list[str]) -> list[str]:
    found = []
    for name in dataset_names:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}\.", expression):
            found.append(name)
    return found


def check_metric_expression(expression: str, dataset_names: list[str]) -> None:
    check_expression(expression)
    named = referenced_datasets(expression, dataset_names)
    if named != ["bookings"]:
        die(expression)


def snowflake_expression(expression: str) -> dict:
    check_expression(expression)
    return {"dialects": [{"dialect": "SNOWFLAKE", "expression": expression}]}


def only_snowflake(expression: dict) -> str:
    dialects = expression.get("dialects") if isinstance(expression, dict) else None
    if not isinstance(dialects, list) or len(dialects) != 1:
        die("dialects")
    entry = dialects[0]
    if not isinstance(entry, dict) or entry.get("dialect") != "SNOWFLAKE":
        reject_key(str(entry.get("dialect", "dialect")) if isinstance(entry, dict) else "dialect")
    value = entry.get("expression")
    if not isinstance(value, str):
        die("expression")
    check_expression(value)
    return value


def dimension_for_datatype(datatype: str) -> dict | None:
    """Role for this fixture's four datatypes.

    Decimals are facts. Dates are time dimensions. Strings and integers are
    dimensions. Wisdom columns do not carry a dimension block, so the reverse
    mapping restores the block from the datatype.
    """

    if datatype == "Decimal":
        return None
    if datatype == "Date":
        return {"is_time": True}
    if datatype in ("String", "Integer"):
        return {"is_time": False}
    die(datatype)
    return None


def dump_ossie(model: dict) -> str:
    return yaml.safe_dump(model, sort_keys=False, width=4096, allow_unicode=True)


def load_ossie(text: str) -> dict:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        die("version")
    return loaded
