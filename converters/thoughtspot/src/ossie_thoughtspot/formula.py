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

"""A shallow tokenizer for ThoughtSpot formulas.

Deliberately not a parser. It answers only what the two conversion directions need: is an
expression a single outer call and what are its parts, where are its column references, and
what does it look like with those references rewritten. Building an expression tree would be
the SQL-parser decision this converter does not take — expressions pass through under a
tagged dialect rather than being translated across dialects, matching every other converter
in this repository and the specification's stated default.

Three ThoughtSpot syntax features drive the implementation and are why an off-the-shelf SQL
tokenizer is not usable here: column references are bracketed and doubly-colon-qualified
(`[TABLE::Column]`), grouping uses braces (`{ }`), and a bare bracketed name with no `::` is
a runtime parameter rather than a column.
"""
from __future__ import annotations

import re
from typing import Callable

_CALL_HEAD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z_][A-Za-z0-9_]*)*)\s*\(")
_BRACKETED = re.compile(r"\[([^\]]*)\]")
_CLOSERS = {"(": ")", "[": "]", "{": "}"}
_QUOTES = ("'", '"')


def _scan(text: str):
    """Yield `(index, char, depth, in_quote)` with depth counted before the char is applied.

    One pass shared by every function here so that quoting and nesting are treated
    identically everywhere — a divergence between two hand-rolled scanners is exactly the
    kind of bug that would surface as a mis-split argument list months later.
    """
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            yield i, ch, depth, True
            if ch == quote:
                quote = None
            continue
        if ch in _QUOTES:
            quote = ch
            yield i, ch, depth, True
            continue
        if ch in _CLOSERS:
            yield i, ch, depth, False
            depth += 1
            continue
        if ch in (")", "]", "}"):
            depth -= 1
            yield i, ch, depth, False
            continue
        yield i, ch, depth, False


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for i, ch, depth, in_quote in _scan(text):
        if ch == "," and depth == 0 and not in_quote:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail or parts:
        parts.append(tail)
    return parts


def split_call(expression: str) -> tuple[str, list[str]] | None:
    """`sum ( [A::x] , 2 )` -> `("sum", ["[A::x]", "2"])`; `None` if not a single outer call.

    Returns `None` — never a partial answer — for anything that merely *contains* a call,
    such as `sum ( [A::x] ) + 1`. A caller that received `("sum", ["[A::x]"])` for that
    input would silently drop the `+ 1`, which is precisely the class of silent loss this
    converter exists to prevent.
    """
    text = expression.strip()
    head = _CALL_HEAD.match(text)
    if head is None:
        return None
    open_at = head.end() - 1
    close_at = None
    for i, ch, depth, in_quote in _scan(text[open_at:]):
        if ch == ")" and depth == 0 and not in_quote:
            close_at = open_at + i
            break
    if close_at is None:
        return None
    if close_at != len(text) - 1:
        return None
    inner = text[open_at + 1 : close_at].strip()
    if not inner:
        return head.group(1), []
    return head.group(1), _split_top_level_commas(inner)


def _bracketed_spans(expression: str) -> list[tuple[int, int, str]]:
    """Every `[...]` span that is not inside a quoted literal, as `(start, end, body)`."""
    quoted = {i for i, _ch, _d, in_quote in _scan(expression) if in_quote}
    return [
        (m.start(), m.end(), m.group(1))
        for m in _BRACKETED.finditer(expression)
        if m.start() not in quoted
    ]


def find_column_refs(expression: str) -> list[tuple[str, str]]:
    """Every `[TABLE::Column]` reference, in order, duplicates kept.

    A bracketed name with no `::` is a runtime parameter, not a column — see
    `find_parameter_refs`.
    """
    return [
        (body.split("::", 1)[0], body.split("::", 1)[1])
        for _s, _e, body in _bracketed_spans(expression)
        if "::" in body
    ]


def find_parameter_refs(expression: str) -> list[str]:
    """Every bracketed name with no table qualifier — a ThoughtSpot runtime parameter.

    Ossie has no equivalent, so an expression carrying one is not portable and the caller
    raises an issue rather than emitting a portable sibling.
    """
    return [body for _s, _e, body in _bracketed_spans(expression) if "::" not in body]


def is_bare_column_ref(expression: str) -> tuple[str, str] | None:
    """`(table, column)` when the whole expression is one column reference, else `None`.

    The common case by a wide margin: most fields are physical columns, and this is what
    lets those fields carry a portable sibling for free.
    """
    text = expression.strip()
    spans = _bracketed_spans(text)
    if len(spans) != 1:
        return None
    start, end, body = spans[0]
    if start != 0 or end != len(text) or "::" not in body:
        return None
    table, column = body.split("::", 1)
    return table, column


def rewrite_column_refs(
    expression: str, rename: Callable[[str, str], str]
) -> str:
    """Replace each `[TABLE::Column]` with `rename(table, column)`, byte-preserving elsewhere.

    Everything between references — whitespace, literals, operators — is copied verbatim, so
    an expression whose references are unchanged is returned unchanged. Parameter references
    and bracketed text inside quoted literals are left alone.
    """
    out: list[str] = []
    cursor = 0
    for start, end, body in _bracketed_spans(expression):
        if "::" not in body:
            continue
        table, column = body.split("::", 1)
        out.append(expression[cursor:start])
        out.append(rename(table, column))
        cursor = end
    out.append(expression[cursor:])
    return "".join(out)
