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

"""TML's structural half: the 1+N document set, and the serialisation invariants.

Deliberately holds no Ossie vocabulary — it is the ThoughtSpot file format and nothing else,
which is what makes it unit-testable without a fixture from the other side.

Four invariants live here so no caller has to carry them. `guid` is read and never written:
it belongs at the document root, and a nested one is *silently ignored* while ThoughtSpot
creates a duplicate object with the same name. A formula expression containing braces is
emitted as a `>-` block scalar or the YAML will not parse on re-read. Tables are emitted
before the model, which references each one by name, so ordering is load-bearing. And
everything goes through the YAML 1.2 codec so a column, synonym, or parameter value of
`on`, `off`, `yes`, or `no` survives as the string it is instead of being coerced to a
boolean.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import yaml

from . import _yaml
from .errors import ConversionError

#: The TML root keys this converter handles. Ossie's scope is the semantic model, so
#: answers, liveboards and the rest are not merely unsupported but out of scope.
_KINDS = ("model", "table", "sql_view")

#: Filename suffix per kind, matching ThoughtSpot's own export convention.
_SUFFIX = {"model": "model.tml", "table": "table.tml", "sql_view": "sql_view.tml"}


class _BlockScalar(str):
    """A string the dumper must emit as a folded block scalar. See `block_scalar`."""


def _represent_block(dumper: yaml.SafeDumper, data: _BlockScalar) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style=">")


yaml.add_representer(_BlockScalar, _represent_block, Dumper=_yaml.Yaml12Dumper)


def block_scalar(text: str) -> str:
    """Mark `text` for `>-` emission. Returns a `str`, so callers need not care."""
    return _BlockScalar(text)


@dataclass(frozen=True)
class TmlDocument:
    kind: str
    body: dict
    guid: str | None
    source: str | None = None


@dataclass(frozen=True)
class DocumentSet:
    model: TmlDocument
    tables: tuple[TmlDocument, ...]

    def table_by_name(self, name: str) -> TmlDocument | None:
        """The table or SQL view whose `name` matches, or `None`.

        Model `model_tables[]` entries reference a table by this name (or by an `alias`
        that the caller resolves first), so this is the join between the two documents.
        """
        for table in self.tables:
            if table.body.get("name") == name:
                return table
        return None


def load_document(text: str, *, source: str | None = None) -> TmlDocument:
    """Parse one TML document. Raises `ConversionError` rather than a bare YAML error."""
    data = _yaml.load(text)
    if not isinstance(data, dict):
        raise ConversionError(f"{source or '<input>'} is not a TML document: expected a mapping")
    present = [kind for kind in _KINDS if kind in data]
    if not present:
        raise ConversionError(
            f"{source or '<input>'} is not a TML document this converter handles: "
            f"expected one of {', '.join(_KINDS)} at the root"
        )
    if len(present) > 1:
        raise ConversionError(
            f"{source or '<input>'} declares more than one root kind ({', '.join(present)})"
        )
    kind = present[0]
    body = data[kind]
    if not isinstance(body, dict):
        raise ConversionError(f"{source or '<input>'}: {kind} must be a mapping")
    return TmlDocument(kind=kind, body=body, guid=data.get("guid"), source=source)


def load_document_set(texts: Sequence[tuple[str, str]]) -> DocumentSet:
    """Load `(source, text)` pairs into exactly one model plus its tables, in any order."""
    documents = [load_document(text, source=source) for source, text in texts]
    models = [d for d in documents if d.kind == "model"]
    tables = tuple(d for d in documents if d.kind in ("table", "sql_view"))
    if not models:
        raise ConversionError("the document set contains no model document")
    if len(models) > 1:
        names = ", ".join(str(m.body.get("name")) for m in models)
        raise ConversionError(f"the document set contains more than one model document: {names}")
    return DocumentSet(model=models[0], tables=tables)


def dump_document(document: TmlDocument) -> str:
    """Serialise one document. `guid` is omitted unconditionally."""
    return _yaml.dump({document.kind: document.body})


def dump_document_set(document_set: DocumentSet) -> list[tuple[str, str]]:
    """`(filename, text)` for every document, tables first — the model references them
    by name, so they must exist before it does."""
    out = [
        (f"{table.body.get('name', 'table')}.{_SUFFIX[table.kind]}", dump_document(table))
        for table in document_set.tables
    ]
    model = document_set.model
    out.append((f"{model.body.get('name', 'model')}.{_SUFFIX['model']}", dump_document(model)))
    return out
