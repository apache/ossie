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

**Quoting note — doubling works, backslash-escaping is out of scope on purpose.**
ThoughtSpot's own convention for an embedded quote in a string literal is doubling it
(`'it''s'`), and `_scan` handles that correctly even though it has no explicit doubling
case: the character that closes a quote and the character that immediately reopens it are
both reported as quoted, so nothing in between ever reads as outside the literal. A
backslash before a quote is *not* an escape in ThoughtSpot's grammar — it is an ordinary
character — so `'a\'b'` genuinely ends the literal at the escaped quote, and `_scan`
splitting there is correct behaviour for this language, not a bug to fix.
"""
from __future__ import annotations

import re
from typing import Callable

from . import identifiers

_CALL_HEAD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z_][A-Za-z0-9_]*)*)\s*\(")
#: Same call-head shape as `_CALL_HEAD`, but unanchored (no `^`) so it matches a call
#: starting anywhere in the text, and guarded on the left by a negative lookbehind so
#: a match can never start mid-identifier (e.g. inside "ground" when scanning for a
#: call literally named "round"). Used by `find_call_names` to find every call in an
#: expression, not just the single outer one `split_call` answers about.
_CALL_HEAD_ANYWHERE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z_][A-Za-z0-9_]*)*)\s*\("
)
_BRACKETED = re.compile(r"\[([^\]]*)\]")
_CLOSERS = {"(": ")", "[": "]", "{": "}"}
_QUOTES = ("'", '"')

# An operator/control-flow keyword can never be part of a function name, so a call head
# that includes one of these as a space-separated word is not a call — see
# `test_a_keyword_prefix_is_not_mistaken_for_a_function_name`. This is a blocklist, not a
# catalog lookup, so this leaf module stays free of an import edge to the function catalog.
_KEYWORDS = frozenset(
    {"and", "or", "not", "if", "then", "else", "true", "false", "in", "between", "like"}
)


def _scan(text: str):
    """Yield `(index, char, depth, in_quote)` with depth counted before the char is applied.

    One pass shared by every function here so that quoting and nesting are treated
    identically everywhere — a divergence between two hand-rolled scanners is exactly the
    kind of bug that would surface as a mis-split argument list months later.

    A `[...]` body is an opaque identifier, not code — a quote character inside one (a
    display name like `Manager's Bonus`) is part of the name, not a string delimiter. So
    quote-toggling is suppressed for as long as the innermost open bracket is `[`; a bracket
    stack (not just the depth counter) tracks which opener is innermost so this only applies
    to `[...]`, not `(...)` or `{...}`, and correctly un-suppresses again once that `[`
    closes, however deeply it is nested inside calls. The stack pops only when a closer
    matches the type of its top entry — a mismatched or stray closer leaves the stack
    untouched rather than popping the wrong entry and desynchronising which bracket type is
    considered innermost for the rest of the scan.
    """
    depth = 0
    quote: str | None = None
    bracket_stack: list[str] = []
    for i, ch in enumerate(text):
        if quote is not None:
            yield i, ch, depth, True
            if ch == quote:
                quote = None
            continue
        in_bracket_body = bool(bracket_stack) and bracket_stack[-1] == "["
        if not in_bracket_body and ch in _QUOTES:
            quote = ch
            yield i, ch, depth, True
            continue
        if ch in _CLOSERS:
            yield i, ch, depth, False
            bracket_stack.append(ch)
            depth += 1
            continue
        if ch in (")", "]", "}"):
            depth -= 1
            if bracket_stack and _CLOSERS[bracket_stack[-1]] == ch:
                bracket_stack.pop()
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
    name = head.group(1)
    if any(word.lower() in _KEYWORDS for word in name.split()):
        # `true and count (...)` is an operator expression whose last operand happens to
        # look like a call head, not a call named "true and count". `unique count (...)`
        # is unaffected — none of its words are in the blocklist.
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


def find_call_names(expression: str) -> list[str]:
    """Every function-call name in `expression`, at any nesting depth, duplicates kept.

    `split_call` deliberately answers only about the single *outer* call — exactly
    what building a rendering around a whole expression needs. This answers a
    different question a safety check needs instead: whether a call with a
    particular name appears *anywhere* inside the expression, however deeply
    nested — `round ( sum ( [T::x] ) , 2 )` has `round` as its outer call but
    `sum` buried one level inside it, and a caller checking only the outer call
    would miss that the expression already aggregates.

    A name is only reported at a genuine call site: immediately followed by `(`,
    not inside a quoted string literal, and not inside the opaque body of a
    `[...]` reference — so a display name or string literal that happens to
    contain text like `sum (` is never mistaken for a real call.

    The same keyword handling `split_call` applies also applies here, adapted for
    scanning mid-expression rather than judging one candidate outer call: a
    leading keyword word (`true and count ( ... )`) means that word is part of an
    operator expression, not the call's own name, so it is stripped one word at a
    time from the front of the matched run until either a non-keyword word starts
    the remainder (the real call name — `count`, not `true and count`) or nothing
    is left (the whole run was keywords, so it names no call at all).
    """
    opaque = {i for i, _ch, _d, in_quote in _scan(expression) if in_quote}
    for start, end, _body in _bracketed_spans(expression):
        opaque.update(range(start, end))

    names: list[str] = []
    for match in _CALL_HEAD_ANYWHERE.finditer(expression):
        if match.start() in opaque:
            continue
        words = match.group(1).split()
        while words and words[0].lower() in _KEYWORDS:
            words = words[1:]
        if words:
            names.append(" ".join(words))
    return names


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
    `find_parameter_refs`. Splitting delegates to `identifiers.split_column_ref` rather
    than a bare `str.split("::", 1)`, so an ambiguous reference (more than one `::`
    delimiter) raises `ValueError` instead of silently taking the first one — consistent
    with every other reader of this reference shape, and because silently misreading one
    reference in an otherwise-valid expression is worse than failing the whole call.
    """
    return [
        identifiers.split_column_ref(f"[{body}]")
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
    return identifiers.split_column_ref(f"[{body}]")


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
        table, column = identifiers.split_column_ref(f"[{body}]")
        out.append(expression[cursor:start])
        out.append(rename(table, column))
        cursor = end
    out.append(expression[cursor:])
    return "".join(out)
