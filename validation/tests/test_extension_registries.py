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

"""Pin the token order of the registries in extensions/.

dialects.md and vendors.md list their tokens alphabetically (ASCII order) so a
new entry has one obvious place to go and merges do not conflict on a shared
append position. These tests fail when a row is added out of order.
"""

import re
from pathlib import Path

import pytest

_EXTENSIONS = Path(__file__).parents[2] / "extensions"

# (file, heading of the section whose table is the registry)
_REGISTRIES = [
    ("dialects.md", "## Known dialects"),
    ("vendors.md", "## Known vendors"),
]

_NOTICE = "Rows are sorted alphabetically by token."
_ROW = re.compile(r"^\| `([^`]+)` \|")


def _tokens(filename: str, heading: str) -> list[str]:
    """Return the first-column tokens of the table under `heading`."""
    lines = (_EXTENSIONS / filename).read_text(encoding="utf-8").splitlines()
    start = lines.index(heading) + 1
    tokens = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        match = _ROW.match(line)
        if match:
            tokens.append(match.group(1))
    return tokens


@pytest.mark.parametrize(("filename", "heading"), _REGISTRIES)
def test_tokens_are_sorted_alphabetically(filename, heading):
    tokens = _tokens(filename, heading)
    # Guard against a table format change that would make the check vacuous.
    assert tokens, f"no tokens parsed from {filename} under {heading!r}"
    assert tokens == sorted(tokens), f"{filename} must list tokens alphabetically; expected order: {sorted(tokens)}"


@pytest.mark.parametrize(("filename", "heading"), _REGISTRIES)
def test_tokens_are_unique(filename, heading):
    tokens = _tokens(filename, heading)
    assert len(tokens) == len(set(tokens))


@pytest.mark.parametrize(("filename", "_heading"), _REGISTRIES)
def test_registry_states_its_order(filename, _heading):
    text = (_EXTENSIONS / filename).read_text(encoding="utf-8")
    assert _NOTICE in text, f"{filename} must state that its rows are sorted"
