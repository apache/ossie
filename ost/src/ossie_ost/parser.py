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

"""Parses an .ost script into chunks.

A script is a sequence of chunks: verbatim text (blank lines and
comments) and commands, each with its expected output. Concatenating
the text of the chunks gives back the script, byte for byte.

The parser is lexical. It finds where each command ends, and which
lines are expected output, but it does not parse arguments; see
:func:`split_call` and :func:`parse_args` for that.
"""

from __future__ import annotations

import bisect
import itertools
import json
import re
from collections.abc import Container, Iterator
from dataclasses import dataclass
from enum import Enum

ROW_COUNT = re.compile(r"\((?:1 row|\d+ rows)\)")
RAW_OPEN = re.compile(r"\{([a-z_]*)\|")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INTEGER = re.compile(r"-?[0-9]+")
FLOAT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")


@dataclass(frozen=True)
class Problem:
    """A problem in a script, at a 1-based line and column."""

    line: int
    column: int
    message: str


class ScriptError(Exception):
    """A script is malformed, and cannot be run."""

    def __init__(self, path: str, problems: list[Problem]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(str(self))

    def __str__(self) -> str:
        return "\n".join(
            f"{self.path}:{p.line}:{p.column}: {p.message}" for p in self.problems
        )


@dataclass(frozen=True)
class Verbatim:
    """Blank lines and comments, copied unchanged."""

    text: str


@dataclass(frozen=True)
class Command:
    """A command and its expected output.

    ``text`` is the command as written, through the newline after its
    terminating ``;``. ``body`` is the command without the terminating
    ``;`` and anything after it. ``expected`` is the expected output as
    written (possibly empty), and ``blocks`` splits it into tables,
    values and errors.
    """

    line: int
    text: str
    body: str
    expected: str
    blocks: tuple[str, ...]

    @property
    def full_text(self) -> str:
        return self.text + self.expected


Chunk = Verbatim | Command


@dataclass(frozen=True)
class Script:
    path: str
    chunks: tuple[Chunk, ...]

    def text(self) -> str:
        return "".join(
            c.full_text if isinstance(c, Command) else c.text for c in self.chunks
        )


def check_whitespace(text: str) -> list[Problem]:
    """Returns a problem for each tab, carriage return and trailing space."""
    problems = []
    for i, line in enumerate(text.split("\n"), 1):
        for j, c in enumerate(line, 1):
            if c == "\t":
                problems.append(Problem(i, j, "tab"))
            elif c == "\r":
                problems.append(Problem(i, j, "carriage return"))
        stripped = line.rstrip(" ")
        if len(stripped) < len(line):
            problems.append(Problem(i, len(stripped) + 1, "trailing space"))
    return problems


def parse(text: str, path: str = "<string>") -> Script:
    """Parses a script.

    Raises :class:`ScriptError` if the script contains a tab, a carriage
    return or a trailing space, or if a command is malformed.
    """
    problems = check_whitespace(text)
    if problems:
        raise ScriptError(path, problems)
    return _Parser(text, path).parse()


def split_lines(text: str) -> list[str]:
    """Splits text into lines, each with its terminating newline.

    Unlike :meth:`str.splitlines`, breaks only at ``\\n``, not at other
    characters such as form feed or ``U+2028``."""
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    return lines if lines[-1] else lines[:-1]


def is_output_line(line: str) -> bool:
    return line.startswith(("+", "|", ">"))


class _Parser:
    def __init__(self, text: str, path: str) -> None:
        self.path = path
        self.text = text
        self.lines = split_lines(text)
        # The offset in the text at which each line starts.
        self.starts = [0, *itertools.accumulate(len(line) for line in self.lines)]

    def error(self, line: int, column: int, message: str) -> ScriptError:
        return ScriptError(self.path, [Problem(line, column, message)])

    def parse(self) -> Script:
        chunks: list[Chunk] = []
        verbatim: list[str] = []
        i = 0
        while i < len(self.lines):
            line = self.lines[i]
            if line.strip() == "" or line.startswith("#"):
                verbatim.append(line)
                i += 1
                continue
            if is_output_line(line):
                raise self.error(i + 1, 1, "output without a command")
            if verbatim:
                chunks.append(Verbatim("".join(verbatim)))
                verbatim = []
            end, body = self.scan_command(i)
            j = end + 1
            blocks = []
            while j < len(self.lines) and is_output_line(self.lines[j]):
                k = self.scan_block(j)
                blocks.append("".join(self.lines[j:k]))
                j = k
            chunks.append(
                Command(
                    line=i + 1,
                    text="".join(self.lines[i : end + 1]),
                    body=body,
                    expected="".join(blocks),
                    blocks=tuple(blocks),
                )
            )
            i = j
        if verbatim:
            chunks.append(Verbatim("".join(verbatim)))
        return Script(self.path, tuple(chunks))

    def scan_command(self, start: int) -> tuple[int, str]:
        """Scans a command that starts at line ``start``.

        Returns the index of the line on which the command ends, and the
        command's body (its text, up to its terminating ``;``).
        """
        scanner = Scanner(self.text)
        try:
            pos = scanner.find_terminator(self.starts[start])
        except ScanError as e:
            line, column = scanner.position(e.offset)
            raise self.error(line + 1, column, e.message) from None
        if pos is None:
            message = "unterminated command"
            if scanner.open is not None:
                construct, offset = scanner.open
                line, column = scanner.position(offset)
                message += f" (open '{construct}' at {line + 1}:{column})"
            raise self.error(start + 1, 1, message)
        end = bisect.bisect_right(self.starts, pos) - 1
        return end, self.text[self.starts[start] : pos]

    def scan_block(self, start: int) -> int:
        """Scans an output block that starts at line ``start``.

        Returns the index of the line after the block.
        """
        line = self.lines[start]
        if line.startswith(">"):
            m = RAW_OPEN.match(line, 2)
            if not m:
                return start + 1
            fence = "|" + m.group(1) + "}"
            rest = line[m.end() :]
            j = start
            while fence not in rest:
                j += 1
                if j >= len(self.lines):
                    raise self.error(
                        start + 1, m.start() + 1, "unterminated raw string"
                    )
                rest = self.lines[j]
            return j + 1
        j = start
        while j < len(self.lines) and self.lines[j].startswith(("+", "|")):
            j += 1
        if j < len(self.lines) and ROW_COUNT.fullmatch(self.lines[j].rstrip("\n")):
            j += 1
        return j


class ScanError(Exception):
    def __init__(self, offset: int, message: str) -> None:
        super().__init__(message)
        self.offset = offset
        self.message = message


class Scanner:
    """Scans the text of a command, skipping over quoted strings,
    comments and raw strings."""

    def __init__(self, text: str) -> None:
        self.text = text
        # The construct that is open at the end of the text, and its
        # offset; or None.
        self.open: tuple[str, int] | None = None

    def position(self, offset: int) -> tuple[int, int]:
        """Returns the 0-based line and 1-based column of an offset."""
        line = self.text.count("\n", 0, offset)
        column = offset - (self.text.rfind("\n", 0, offset) + 1)
        return line, column + 1

    def significant(self, i: int = 0) -> Iterator[int]:
        """Yields the offset of each ``(``, ``)`` and ``;``, starting at
        offset ``i``, that is not in a quoted string, comment or raw
        string."""
        text = self.text
        n = len(text)
        while i < n:
            c = text[i]
            if c in "();":
                yield i
                i += 1
            elif c in "'\"":
                i = self._skip(i, c, i + 1, c)
            elif text.startswith("/*", i):
                i = self._skip(i, "/*", i + 2, "*/")
            elif text.startswith("--", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
            elif c == "{" and (m := RAW_OPEN.match(text, i)):
                i = self._skip(i, m.group(0), m.end(), "|" + m.group(1) + "}")
            else:
                i += 1

    def _skip(self, start: int, construct: str, i: int, close: str) -> int:
        j = self.text.find(close, i)
        if j < 0:
            self.open = (construct, start)
            return len(self.text)
        return j + len(close)

    def find_terminator(self, start: int = 0) -> int | None:
        """Returns the offset of the ``;`` that ends the command that
        starts at offset ``start``, or None if the text ends first.

        Raises :class:`ScanError` if there is an unmatched ``)``.
        """
        text = self.text
        parens: list[int] = []
        for i in self.significant(start):
            c = text[i]
            if c == "(":
                parens.append(i)
            elif c == ")":
                if not parens:
                    raise ScanError(i, "unmatched ')'")
                parens.pop()
            elif not parens and (i + 1 == len(text) or text[i + 1] == "\n"):
                return i
        if parens and self.open is None:
            self.open = ("(", parens[-1])
        return None

    def matching_paren(self, open_: int) -> int:
        """Returns the offset of the ``)`` that matches the ``(`` at
        ``open_``."""
        depth = 0
        for i in self.significant(open_):
            c = self.text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise ValueError("unmatched '('")


def split_call(body: str, names: Container[str]) -> tuple[str, str] | None:
    """If ``body`` is a call to one of the functions in ``names``,
    returns the function's name and the text between its parentheses.

    Raises ValueError if the call's closing parenthesis is followed by
    anything other than spaces and line breaks.
    """
    m = NAME.match(body.lstrip(" \n"))
    if not m or m.group(0) not in names:
        return None
    start = len(body) - len(body.lstrip(" \n"))
    i = start + m.end()
    while i < len(body) and body[i] in " \n":
        i += 1
    if i == len(body) or body[i] != "(":
        return None
    close = Scanner(body).matching_paren(i)
    if body[close + 1 :].strip(" \n"):
        raise ValueError(f"unexpected text after call to {m.group(0)}")
    return m.group(0), body[i + 1 : close]


class Kind(Enum):
    """The kind of a function parameter."""

    INT = "int"
    FLOAT = "double"
    BOOLEAN = "boolean"
    STRING = "string"
    QUERY = "query"


def parse_args(text: str, kinds: list[Kind]) -> list[object]:
    """Parses the arguments of a function call.

    Raises ValueError if the arguments do not match ``kinds``.
    """
    args: list[object] = []
    i = _skip_space(text, 0)
    if i == len(text) and not kinds:
        return args
    for n, kind in enumerate(kinds):
        if kind is Kind.QUERY:
            if n != len(kinds) - 1:
                raise ValueError("a query must be the last parameter")
            query = text[i:].strip(" \n")
            if not query:
                raise ValueError("expected a query")
            args.append(query)
            return args
        value, i = _parse_literal(text, i, kind)
        args.append(value)
        i = _skip_space(text, i)
        if n < len(kinds) - 1:
            if i == len(text) or text[i] != ",":
                raise ValueError(f"expected {len(kinds)} arguments, got {len(args)}")
            i = _skip_space(text, i + 1)
    if i != len(text):
        raise ValueError(f"expected {len(kinds)} arguments, got more")
    return args


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \n":
        i += 1
    return i


def _parse_literal(text: str, i: int, kind: Kind) -> tuple[object, int]:
    if kind is Kind.STRING:
        if text.startswith('"', i):
            value, end = json.JSONDecoder().raw_decode(text, i)
            if isinstance(value, str):
                return value, end
        elif m := RAW_OPEN.match(text, i):
            return read_raw_string(text, i)
        raise ValueError("expected a string")
    if kind is Kind.BOOLEAN:
        for word, value in (("true", True), ("false", False)):
            m = NAME.match(text, i)
            if m and m.group(0) == word:
                return value, m.end()
        raise ValueError("expected true or false")
    if kind is Kind.INT:
        m = INTEGER.match(text, i)
        if m and not (m.end() < len(text) and text[m.end()] in ".eE"):
            return int(m.group(0)), m.end()
        raise ValueError("expected an integer")
    m = FLOAT.match(text, i)
    if m:
        return float(m.group(0)), m.end()
    raise ValueError("expected a number")


def read_raw_string(text: str, i: int) -> tuple[str, int]:
    """Reads the raw string that starts at offset ``i``.

    Returns its content and the offset after its closing fence. A tag
    that starts with ``_`` means that the line break right after the
    opening fence is not content.
    """
    m = RAW_OPEN.match(text, i)
    if not m:
        raise ValueError("expected a raw string")
    tag = m.group(1)
    fence = "|" + tag + "}"
    end = text.find(fence, m.end())
    if end < 0:
        raise ValueError("unterminated raw string")
    content = text[m.end() : end]
    if tag.startswith("_") and content.startswith("\n"):
        content = content[1:]
    return content, end + len(fence)
