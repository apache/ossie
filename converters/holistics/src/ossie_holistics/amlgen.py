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

"""Writing AML source text.

The reverse path produces AML source rather than compiled JSON, because
`holistics aml compile` only runs one way and nothing reads its output back.
AML source is what a person edits, a repository holds, and the CLI accepts.

Everything here is about the text: quoting, indentation, identifiers and
heredocs. What to write is `ossie_to_aml`'s decision.
"""
from __future__ import annotations

import re

from .errors import ConversionError

def sanitize(name: str) -> str:
    """An Ossie name as a legal AML identifier.

    An Ossie `name` is any non-empty string, so a valid document can carry one
    AML cannot spell. `examples/flights.semantic_model.yaml` is named
    `Flights semantic model`. Each illegal character becomes an underscore, and
    a name starting with a digit gains a leading one.

    The caller reports the rename and checks for a collision. This function only
    does the spelling.
    """
    if not name:
        raise ConversionError("an Ossie name is empty, and AML has no spelling for that")
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return cleaned if cleaned[0].isalpha() or cleaned[0] == "_" else f"_{cleaned}"


def quote(value: str) -> str:
    """An AML single-quoted string. A backslash escapes a quote or a backslash."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def titleize(name: str) -> str:
    """The label Holistics generates for a field that declares none.

    `label` is required on a dimension and on a measure, so a document with no
    stashed label still needs one, and this is the spelling AML itself uses:
    `created_at` becomes `Created At`.
    """
    return " ".join(word.capitalize() for word in name.split("_") if word) or name


class Block:
    """One `Name header { ... }` block, rendered with its children."""

    def __init__(self, header: str):
        self.header = header
        self.lines: list[str | Block] = []

    def property(self, key: str, value: str) -> "Block":
        """A `key: value` line. `value` is already rendered AML."""
        self.lines.append(f"{key}: {value}")
        return self

    def text(self, key: str, value: str | None) -> "Block":
        """A `key: 'value'` line, skipped when `value` is None or empty."""
        if value:
            self.property(key, quote(value))
        return self

    def flag(self, key: str, value: bool) -> "Block":
        """A `key: true` line, written only when true.

        AML defaults every one of these to false, so writing `false` would add
        noise without adding meaning.
        """
        if value:
            self.property(key, "true")
        return self

    def heredoc(self, key: str, language: str, body: str) -> "Block":
        """A `key: @sql ... ;;` property.

        A single-line body stays on one line, which is how AML is normally
        written. A multi-line body is indented under the opener with the
        terminator on its own line, because `;;` on the last content line would
        read as part of that line.
        """
        if "\n" not in body:
            return self.property(key, f"@{language} {body};;")
        self.lines.append(_Heredoc(key, language, body))
        return self

    def block(self, header: str) -> "Block":
        child = Block(header)
        self.lines.append(child)
        return child

    def blank(self) -> "Block":
        self.lines.append("")
        return self

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        out = [f"{pad}{self.header} {{"]
        for line in self.lines:
            if isinstance(line, Block):
                out.append(line.render(indent + 1))
            elif isinstance(line, _Heredoc):
                out.append(line.render(indent + 1))
            elif line == "":
                out.append("")
            else:
                out.append(f"{pad}  {line}")
        out.append(f"{pad}}}")
        return "\n".join(out)


class _Heredoc:
    def __init__(self, key: str, language: str, body: str):
        self.key = key
        self.language = language
        self.body = body

    def render(self, indent: int) -> str:
        pad = "  " * indent
        body = "\n".join(f"{pad}  {line}" if line.strip() else "" for line in self.body.split("\n"))
        return f"{pad}{self.key}: @{self.language}\n{body}\n{pad};;"


def array(items: list[str], indent: int = 1) -> str:
    """A multi-line AML array with a trailing comma, as AML files are written."""
    if not items:
        return "[]"
    pad = "  " * (indent + 1)
    closing = "  " * indent
    body = "\n".join(f"{pad}{item}," for item in items)
    return f"[\n{body}\n{closing}]"


LICENSE_HEADER = """\
// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.
"""


def document(block: Block, license_header: bool) -> str:
    head = f"{LICENSE_HEADER}\n" if license_header else ""
    return f"{head}{block.render()}\n"
