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

Four invariants live here so no caller has to carry them. `guid` is read and never written,
at any depth: it belongs at the document root, and a nested one — anywhere in the body, not
only there — is *silently ignored* on import while ThoughtSpot creates a duplicate object
with the same name. A formula expression containing braces is emitted as a `>-` block scalar
or the YAML will not parse on re-read. Tables are emitted before the model, which references
each one by name, so ordering is load-bearing. And everything goes through the YAML 1.2 codec
so a column, synonym, or parameter value of `on`, `off`, `yes`, or `no` survives as the string
it is instead of being coerced to a boolean.

Filenames minted for a document set are sanitised and length-capped: a table name is
user-controlled data and may contain characters a filesystem treats specially — a path
separator, a `..` component, a Windows-reserved device name, more bytes than a single path
component allows — so `dump_document_set` never writes one through unexamined, and never
lets two documents land on the same filename.
"""
from __future__ import annotations

import re
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

#: Characters forbidden in a filename component on POSIX (`/`) or Windows
#: (`< > : " / \ | ? *` plus control characters). Anything else — including a plain
#: `.` — is left alone so an ordinary name is emitted unchanged.
_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Windows device names that are reserved regardless of extension (`CON`, `CON.txt`,
#: `com1.bak`, ... are all reserved) and regardless of case.
_RESERVED_WINDOWS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: Most filesystems cap a single path component at 255 bytes. A ThoughtSpot table or
#: model name carries no length limit of its own, so a name at or past that boundary
#: has to be shortened before it becomes a filename, not left to fail at write time.
_MAX_FILENAME_BYTES = 255


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


def _strip_nested_guids(value: object) -> object:
    """A copy of `value` with every `guid` key removed, at every depth.

    A nested `guid` — on a column entry, a join, anywhere below the document root — is
    silently ignored on import and ThoughtSpot creates a duplicate object rather than
    updating the existing one, exactly like a root-level `guid` would; this closes that
    off at every level rather than only the root. `fqn` is left alone: it is legitimate
    inside a model's table references and stripping it would break them. The input is
    never mutated — `TmlDocument.body` belongs to the caller, who may reasonably dump
    the same document twice or inspect it afterwards.
    """
    if isinstance(value, dict):
        return {k: _strip_nested_guids(v) for k, v in value.items() if k != "guid"}
    if isinstance(value, list):
        return [_strip_nested_guids(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_strip_nested_guids(v) for v in value)
    return value


def dump_document(document: TmlDocument) -> str:
    """Serialise one document. `guid` is stripped unconditionally, at every depth of
    the body — not only at the document root."""
    return _yaml.dump({document.kind: _strip_nested_guids(document.body)})


def _safe_filename_component(name: object) -> str:
    """A ThoughtSpot table/model name, made safe to use as a filename stem.

    Every character forbidden by POSIX (`/`) or Windows (`< > : " / \\ | ? *` and
    control characters) is replaced with `_`. Trailing dots and spaces are trimmed —
    Windows drops them silently, which could otherwise make two distinct names collide
    invisibly. A name that is empty, `.`, or `..` after that, and a Windows-reserved
    device name (`CON`, `COM1`, ...) regardless of what follows the first dot, each get
    a safe fallback. An ordinary name such as `ORDERS` or `store_sales` is returned
    exactly as given. Length is not handled here — `dump_document_set` caps it once it
    knows how much room the suffix and a possible disambiguating counter need.
    """
    text = name if isinstance(name, str) else ""
    cleaned = _FORBIDDEN_FILENAME_CHARS.sub("_", text).rstrip(" .")
    if cleaned in ("", ".", ".."):
        cleaned = "_unnamed"
    elif cleaned.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """`text`, cut down to at most `max_bytes` UTF-8 bytes, never splitting a
    multi-byte character in half."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = max_bytes
    while cut > 0:
        try:
            return encoded[:cut].decode("utf-8")
        except UnicodeDecodeError:
            cut -= 1
    return ""


def dump_document_set(document_set: DocumentSet) -> list[tuple[str, str]]:
    """`(filename, text)` for every document, tables first — the model references them
    by name, so they must exist before it does.

    Each filename's stem is sanitised (`_safe_filename_component`) and truncated to
    leave room, within the 255-byte filesystem component limit, for both the suffix and
    a disambiguating counter. Every candidate filename is reserved as it is minted: if
    it is already taken — two source names sanitising to the same stem, a truncated
    long name colliding with another, or a counter-suffixed name happening to land on
    some other document's plain name — the counter advances and a fresh candidate is
    tried until one is free. No two documents in one `DocumentSet` can ever be handed
    the same filename.
    """
    documents = list(document_set.tables) + [document_set.model]
    # However many documents there are, that is also the most candidates any single one
    # could need to try before finding a free filename (there are only that many
    # filenames already claimed to collide with) — one extra digit of headroom besides.
    counter_reserve = len(f"-{len(documents) + 1}")

    used: set[str] = set()
    out = []
    for document in documents:
        suffix = _SUFFIX[document.kind]
        max_stem_bytes = _MAX_FILENAME_BYTES - len(f".{suffix}") - counter_reserve
        stem = _safe_filename_component(document.body.get("name", document.kind))
        stem = _truncate_utf8(stem, max_stem_bytes).rstrip(" .") or "_unnamed"

        candidate = f"{stem}.{suffix}"
        counter = 1
        while candidate in used:
            counter += 1
            candidate = f"{stem}-{counter}.{suffix}"
        used.add(candidate)
        out.append((candidate, dump_document(document)))
    return out
