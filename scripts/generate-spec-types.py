 #!/usr/bin/env python3
 #
 # /// script
 # requires-python = ">=3.11"
 # dependencies = [
 #     "pyyaml>=6.0.3",
 # ]
 # ///

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

"""
Generate enums for spec.yaml from ossie-schema.json.

Usage:
  python3 scripts/generate-spec-types.py --check    # exit 1 if spec.yaml is out of sync
  python3 scripts/generate-spec-types.py --apply    # update spec.yaml in-place
"""

import difflib
import json
import sys
import argparse
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Missing dependency. Install with:", file=sys.stderr)
    print("  python3 -m pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "core-spec" / "ossie-schema.json"
SPEC = REPO / "core-spec" / "spec.yaml"

SECTIONS = [
    {
        "key": "dialects",
        "schema_path": ["$defs", "Dialect", "enum"],
        "comment_col": 28,
        "header": "# Supported expression language dialects",
        "descriptions": {
            "ANSI_SQL": "Standard SQL dialect",
            "SNOWFLAKE": "Snowflake",
            "MDX": "Multi-Dimensional Expressions",
            "TABLEAU": "Tableau",
            "DATABRICKS": "Databricks SQL",
            "MAQL": "GoodData MAQL (Multi-Dimensional Analytical Query Language)",
            "BIGQUERY": "Google BigQuery GoogleSQL",
            "THOUGHTSPOT": "ThoughtSpot formula language (not SQL)",
        },
    },
    {
        "key": "datatypes",
        "schema_path": ["$defs", "DataType", "enum"],
        "comment_col": 28,
        "header": "# Supported logical data types for fields and metrics",
        "descriptions": {
            "String": "Variable-length Unicode character data",
            "Integer": "Exact integral number",
            "Decimal": "Exact base-10 number",
            "Float": "Approximate floating-point number",
            "Boolean": "Logical two-valued truth type",
            "Date": "Calendar date without time of day",
            "Time": "Time of day without a date or timezone",
            "DateTime": "Date and time without a timezone or offset",
            "DateTimeTz": "Instant identified using offset or timezone context",
            "Opaque": "Known type outside the portable vocabulary",
        },
    },
]


def _schema_values(section: dict) -> list[str]:
    path = section["schema_path"]
    with SCHEMA.open(encoding="utf-8") as f:
        data = json.load(f)
    for part in path:
        data = data[part]
    return list(data)


def _generate_block(section: dict, values: list[str]) -> str:
    key = section["key"]
    comment_col = section["comment_col"]
    descs = section["descriptions"]
    ref = ".".join(section["schema_path"])

    lines = [
        section["header"],
        "# Auto-generated from ossie-schema.json ({}).".format(ref),
        "{}:".format(key),
    ]
    for v in values:
        desc = descs.get(v)
        if desc is None:
            print(f"Warning: no description for '{key}' value '{v}'", file=sys.stderr)
        entry = '  - "{}"'.format(v)
        if desc:
            pad = max(1, comment_col - len(entry))
            lines.append('{}{}# {}'.format(entry, " " * pad, desc))
        else:
            lines.append(entry)

    return "\n".join(lines) + "\n\n"


def _replace_section(text: str, key: str, block: str) -> str:
    lines = text.splitlines(keepends=True)

    key_idx = None
    for idx, line in enumerate(lines):
        if line.rstrip() == f"{key}:":
            key_idx = idx
            break

    if key_idx is None:
        print(f"Error: '{key}:' not found in spec.yaml", file=sys.stderr)
        sys.exit(1)

    start = key_idx
    idx = key_idx - 1
    while idx >= 0 and lines[idx].strip() == "":
        start = idx
        idx -= 1
    while idx >= 0 and lines[idx].lstrip().startswith("#"):
        start = idx
        idx -= 1

    end = key_idx + 1
    while end < len(lines) and (lines[end].strip() == "" or lines[end][0].isspace()):
        end += 1

    return "".join(lines[:start]) + block + "".join(lines[end:])


def _render(text: str) -> str:
    for section in SECTIONS:
        values = _schema_values(section)
        block = _generate_block(section, values)
        text = _replace_section(text, section["key"], block)

    # parse and validate before returning
    try:
        list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        print(f"Error: Generated YAML is invalid: {e}", file=sys.stderr)
        sys.exit(1)
    return text


def _apply():
    rendered = _render(SPEC.read_text())
    SPEC.write_text(rendered)
    print(f"Wrote {SPEC}", file=sys.stderr)


def _check() -> list:
    current = SPEC.read_text()
    rendered = _render(current)
    if current == rendered:
        return 0

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile=f"{SPEC} (current)",
        tofile=f"{SPEC} (expected)"
    )
    sys.stderr.writelines(diff)
    print(f"{SPEC} is out of sync with {SCHEMA}. Run --apply to fix.", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Check spec.yaml is in sync")
    group.add_argument("--apply", action="store_true", help="Update spec.yaml in-place")
    args = parser.parse_args()

    if args.apply:
        _apply()
        return
    if args.check:
        sys.exit(_check())
    parser.print_help()


if __name__ == "__main__":
    main()
