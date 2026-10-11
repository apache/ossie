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

"""The issue-code table in the documentation matches the source.

A reader who sees `HOLISTICS_WINDOW_FUNCTION_NEEDS_A_GROUPING` in an issue log
looks it up in `docs/limitations.md`. A code the converter raises and the table
omits leaves them with nothing to find, and a code the table lists and the
converter never raises sends them looking for a cause that cannot occur.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMITATIONS = (ROOT / "docs" / "limitations.md").read_text(encoding="utf-8")


def raised_codes() -> dict[str, set[str]]:
    """Each issue code the converter can raise, and the severities it uses."""
    found: dict[str, set[str]] = {}
    for module in ("aml_to_ossie", "ossie_to_aml"):
        text = (ROOT / "src" / "ossie_holistics" / f"{module}.py").read_text(encoding="utf-8")
        constants = dict(re.findall(r'^(ISSUE_\w+) = "(\w+)"', text, re.M))
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add"):
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            code, severity = keywords.get("code"), keywords.get("severity")
            if isinstance(code, ast.Name) and isinstance(severity, ast.Attribute):
                found.setdefault(constants[code.id], set()).add(severity.attr)
    return found


def documented_codes() -> dict[str, str]:
    return dict(re.findall(r"^\| `(HOLISTICS_\w+)` \| (\w+) \|", LIMITATIONS, re.M))


def test_every_raised_code_is_documented():
    assert set(raised_codes()) - set(documented_codes()) == set()


def test_every_documented_code_is_raised():
    assert set(documented_codes()) - set(raised_codes()) == set()


def test_the_documented_severity_matches_the_source():
    documented = documented_codes()
    mismatched = {
        code: (documented[code], sorted(severities))
        for code, severities in raised_codes().items()
        if {documented[code]} != severities
    }
    assert not mismatched
