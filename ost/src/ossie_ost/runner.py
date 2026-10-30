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

"""Runs an .ost script, and generates the regenerated script.

Each command is run against an in-memory DuckDB database. Its output
is compared with the expected output (see *MATCHING* in
``docs/ost.md``); if they match, the expected output is kept as
written, otherwise it is replaced with the actual output.
"""

from __future__ import annotations

import datetime
import json
import math
import re
import struct
import threading
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import resources

import duckdb

from ossie_ost.parser import (
    ROW_COUNT,
    Command,
    Kind,
    ScriptError,
    parse,
    parse_args,
    read_raw_string,
    split_call,
)

ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
VALUE_TYPE = re.compile(r" *: *([a-z]+)")

INTEGER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
}
FAMILIES = {
    "BOOLEAN": "boolean",
    "FLOAT": "real",
    "DOUBLE": "double",
    "VARCHAR": "string",
    "DATE": "date",
    "TIME": "time",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP_S": "timestamp",
    "TIMESTAMP_MS": "timestamp",
    "TIMESTAMP_NS": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamptz",
    "INTERVAL": "interval",
    # The type of a bare NULL literal; every value is NULL.
    '"NULL"': "string",
}
NUMERIC_FAMILIES = {"integer", "decimal", "real", "double"}

# Statements whose result is a set of rows. Other statements (such as
# CREATE) have no output, and so do INSERT, UPDATE and DELETE unless
# they have a RETURNING clause.
ROW_STATEMENTS = {
    duckdb.StatementType.SELECT,
    duckdb.StatementType.EXPLAIN,
    duckdb.StatementType.PRAGMA,
    duckdb.StatementType.CALL,
    duckdb.StatementType.EXECUTE,
    duckdb.StatementType.RELATION,
}
DML_STATEMENTS = {
    duckdb.StatementType.INSERT,
    duckdb.StatementType.UPDATE,
    duckdb.StatementType.DELETE,
    duckdb.StatementType.MERGE_INTO,
}


class OstError(Exception):
    """An error raised by ``ost`` itself, rather than the database."""

    def __init__(self, message: str) -> None:
        super().__init__("ost: " + message)


# Outputs.


@dataclass(frozen=True)
class Table:
    """A set of rows. Each cell is a formatted value (unquoted), or
    None for NULL."""

    columns: tuple[str, ...]
    families: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]
    ordered: bool


@dataclass(frozen=True)
class Value:
    """A value returned by a function. ``text`` is the formatted value;
    for a string, its content."""

    type: str
    text: str
    quoted: bool = False


@dataclass(frozen=True)
class Error:
    message: str


Output = Table | Value | Error | None


# Formatting values.


def family(type_name: str) -> str | None:
    """Returns the family of a DuckDB type, or None if it is outside the
    common core."""
    if type_name in INTEGER_TYPES:
        return "integer"
    if type_name.startswith("DECIMAL("):
        return "decimal"
    return FAMILIES.get(type_name)


def format_cell(value: object, family_: str, type_name: str) -> str | None:
    if value is None:
        return None
    if family_ == "boolean":
        return "true" if value else "false"
    if family_ == "decimal":
        scale = int(type_name.rstrip(")").split(",")[1])
        return f"{value:.{scale}f}"
    if family_ == "real":
        return format_real(value)
    if family_ == "double":
        return repr(value)
    if family_ == "date":
        return value.isoformat()
    if family_ == "time":
        return format_time(value)
    if family_ == "timestamp":
        return value.date().isoformat() + " " + format_time(value.time())
    if family_ == "timestamptz":
        value = value.astimezone(datetime.UTC)
        return value.date().isoformat() + " " + format_time(value.time()) + "+00"
    if family_ == "interval":
        return format_interval(value)
    return str(value)


def format_real(x: float) -> str:
    """Formats a 32-bit float as the shortest decimal that converts back
    to it."""
    if math.isfinite(x):
        for digits in range(1, 10):
            s = f"{x:.{digits}g}"
            if _to_real(float(s)) == x:
                return repr(float(s))
    return repr(x)


def _to_real(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def format_time(t: datetime.time) -> str:
    s = f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    if t.microsecond:
        s += f".{t.microsecond:06d}".rstrip("0")
    return s


def format_interval(d: datetime.timedelta) -> str:
    """Formats an interval as an ISO 8601 duration, such as
    ``P3DT4H5M6.5S``."""
    us = (d.days * 86400 + d.seconds) * 1_000_000 + d.microseconds
    if us == 0:
        return "PT0S"
    sign = "-" if us < 0 else ""
    days, us = divmod(abs(us), 86400 * 1_000_000)
    hours, us = divmod(us, 3600 * 1_000_000)
    minutes, us = divmod(us, 60 * 1_000_000)
    seconds, us = divmod(us, 1_000_000)
    s = sign + "P"
    if days:
        s += f"{days}D"
    if hours or minutes or seconds or us:
        s += "T"
        if hours:
            s += f"{hours}H"
        if minutes:
            s += f"{minutes}M"
        if seconds or us:
            s += str(seconds)
            if us:
                s += f".{us:06d}".rstrip("0")
            s += "S"
    return s


def needs_quotes(s: str) -> bool:
    """Whether a string cell must be written in quotes."""
    return (
        s == ""
        or s[0] == " "
        or s[-1] == " "
        or s[0] == '"'
        or "|" in s
        or any((c.isspace() and c != " ") or unicodedata.category(c) == "Cc" for c in s)
    )


def quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def to_value(v: object) -> Value | None:
    """Converts the result of a function to a value."""
    if v is None:
        return None
    if isinstance(v, bool):
        return Value("boolean", "true" if v else "false")
    if isinstance(v, int):
        return Value("int", str(v))
    if isinstance(v, float):
        return Value("double", repr(v))
    if isinstance(v, str):
        return Value("string", v, True)
    raise OstError(f"unsupported type {type(v).__name__}")


# Writing outputs.


def render(output: Output) -> str:
    if output is None:
        return ""
    if isinstance(output, Error):
        return f"> error: {output.message}\n"
    if isinstance(output, Value):
        text = output.text
        if output.type == "string":
            text = raw_string(text) if can_be_raw(text) else quote(text)
        return f"> {text} : {output.type}\n"
    return render_table(output)


def render_table(table: Table) -> str:
    rows = [
        [_cell_text(c, f) for c, f in zip(row, table.families)] for row in table.rows
    ]
    widths = [
        max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(table.columns)
    ]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+\n"
    lines = [border, _row_line(table.columns, widths, [False] * len(widths)), border]
    right = [f in NUMERIC_FAMILIES for f in table.families]
    lines.extend(_row_line(r, widths, right) for r in rows)
    n = len(rows)
    lines += [border, "(1 row)\n" if n == 1 else f"({n} rows)\n"]
    return "".join(lines)


def _cell_text(cell: str | None, family_: str) -> str:
    if cell is None:
        return ""
    if family_ == "string" and needs_quotes(cell):
        return quote(cell)
    return cell


def _row_line(cells, widths: list[int], right: list[bool]) -> str:
    parts = [c.rjust(w) if r else c.ljust(w) for c, w, r in zip(cells, widths, right)]
    return "| " + " | ".join(parts) + " |\n"


def can_be_raw(s: str) -> bool:
    """Whether a string value is written as a raw string."""
    return "\n" in s and " \n" not in s and all(c == "\n" or " " <= c <= "~" for c in s)


def raw_string(s: str) -> str:
    lines = s.split("\n")
    prefix = "_" if len(lines) > 1 and lines[1].startswith(" ") else ""
    for tag in _tags():
        tag = prefix + tag
        close = "|" + tag + "}"
        if close not in s:
            newline = "\n" if prefix else ""
            return "{" + tag + "|" + newline + s + close
    raise AssertionError("unreachable")


def _tags():
    """Yields "", "a", ..., "z", "aa", "ab", ..."""
    yield ""
    n = 0
    while True:
        n += 1
        tag = ""
        i = n
        while i > 0:
            i, r = divmod(i - 1, 26)
            tag = chr(ord("a") + r) + tag
        yield tag


# Reading expected outputs.


def parse_block(block: str) -> Output:
    """Parses a block of expected output. Returns None if it cannot be
    parsed."""
    if block.startswith("> error: "):
        return Error(block[len("> error: ") :].rstrip("\n"))
    if block.startswith("> "):
        return _parse_value(block[2:].rstrip("\n"))
    if block.startswith("+"):
        return _parse_table(block.splitlines())
    return None


def _parse_value(text: str) -> Value | None:
    try:
        if text.startswith('"'):
            content, end = json.JSONDecoder().raw_decode(text)
            quoted = True
        elif text.startswith("{"):
            content, end = read_raw_string(text, 0)
            quoted = True
        else:
            content, sep, _ = text.rpartition(":")
            if not sep:
                return None
            content, end, quoted = content.strip(" "), len(content), False
    except ValueError:
        return None
    m = VALUE_TYPE.fullmatch(text, end)
    if not m:
        return None
    return Value(m.group(1), content, quoted)


def _is_border(line: str) -> bool:
    return line.startswith("+") and set(line) <= {"+", "-"}


def _parse_table(lines: list[str]) -> Table | None:
    if len(lines) < 5 or not (
        _is_border(lines[0]) and _is_border(lines[2]) and _is_border(lines[-2])
    ):
        return None
    if not ROW_COUNT.fullmatch(lines[-1]):
        return None
    header = _parse_row(lines[1])
    rows = [_parse_row(line) for line in lines[3:-2]]
    if header is None or any(r is None or len(r) != len(header) for r in rows):
        return None
    if int(lines[-1].strip("()").split()[0]) != len(rows):
        return None
    columns = tuple(h or "" for h in header)
    return Table(columns, (), tuple(tuple(r) for r in rows), False)


def _parse_row(line: str) -> list[str | None] | None:
    """Parses a row such as ``| 10 | "a|b" |  |``; an empty cell is
    None."""
    if not line.startswith("|"):
        return None
    cells: list[str | None] = []
    i = 1
    while i < len(line):
        while i < len(line) and line[i] == " ":
            i += 1
        if line.startswith('"', i):
            try:
                cell, i = json.JSONDecoder().raw_decode(line, i)
            except ValueError:
                return None
            while i < len(line) and line[i] == " ":
                i += 1
            if not line.startswith("|", i):
                return None
            cells.append(cell)
        else:
            j = line.find("|", i)
            if j < 0:
                return None
            cell = line[i:j].rstrip(" ")
            cells.append(cell or None)
            i = j
        i += 1
    return cells


# Matching.


def matches(command: Command, actual: Output, strict: bool) -> bool:
    if strict:
        return command.expected == render(actual)
    if not command.blocks:
        return actual is None
    if len(command.blocks) > 1 or actual is None:
        return False
    expected = parse_block(command.blocks[0])
    if isinstance(actual, Table) and isinstance(expected, Table):
        if [c.lower() for c in actual.columns] != [c.lower() for c in expected.columns]:
            return False
        if actual.ordered:
            return actual.rows == expected.rows
        return sorted(actual.rows, key=_row_key) == sorted(expected.rows, key=_row_key)
    if isinstance(actual, Value) and isinstance(expected, Value):
        return (
            actual.type == expected.type
            and actual.text == expected.text
            and (expected.quoted or actual.type != "string")
        )
    if isinstance(actual, Error) and isinstance(expected, Error):
        return actual.message == expected.message
    return False


def _row_key(row: tuple[str | None, ...]) -> tuple:
    return tuple((c is not None, c or "") for c in row)


def reorder(actual: Table, command: Command) -> Table:
    """Puts the rows of an unordered table that match an expected row
    first, in the order of the expected output, so that a diff shows
    only the rows that changed."""
    if actual.ordered or len(command.blocks) != 1:
        return actual
    expected = parse_block(command.blocks[0])
    if not isinstance(expected, Table):
        return actual
    rest = list(actual.rows)
    first = []
    for row in expected.rows:
        if row in rest:
            rest.remove(row)
            first.append(row)
    return Table(actual.columns, actual.families, tuple(first + rest), False)


# Running scripts.


@dataclass(frozen=True)
class Function:
    """A function that a script can call.

    ``kinds`` are the kinds of its parameters. ``fn`` is called with the
    :class:`Session` and the arguments, and returns a value, a
    :class:`Table`, or None for no output. If it raises, the command's
    output is an error.
    """

    kinds: tuple[Kind, ...]
    fn: Callable[..., object]


def _connect(session: Session, name: str) -> None:
    session.connect(name)


def _set(session: Session, name: str, value: bool) -> None:
    if name not in session.properties:
        raise OstError(f"unknown property {quote(name)}")
    session.properties[name] = value


def _ost(session: Session, script: str) -> str:
    try:
        return run(script, timeout=session.timeout, functions=session.extra).output
    except ScriptError as e:
        p = e.problems[0]
        raise ValueError(f"{e.path}:{p.line}:{p.column}: {p.message}") from None


BUILTINS: dict[str, Function] = {
    "connect": Function((Kind.STRING,), _connect),
    "query": Function((Kind.QUERY,), lambda s, sql: s.query(sql)),
    "queryOrdered": Function((Kind.QUERY,), lambda s, sql: s.query(sql, True)),
    "queryUnordered": Function((Kind.QUERY,), lambda s, sql: s.query(sql, False)),
    "plus": Function((Kind.INT, Kind.INT), lambda s, a, b: a + b),
    "set": Function((Kind.STRING, Kind.BOOLEAN), _set),
    "ost": Function((Kind.STRING,), _ost),
}


class Session:
    """The state of a running script: its database and properties."""

    def __init__(self, timeout: float, extra: Mapping[str, Function] | None) -> None:
        self.timeout = timeout
        self.extra = extra
        self.functions = {**BUILTINS, **(extra or {})}
        self.properties: dict[str, object] = {"matchStrict": False}
        self._connection: duckdb.DuckDBPyConnection | None = None

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self.connect("empty")
        return self._connection

    def connect(self, name: str) -> None:
        if name == "empty":
            sql = None
        elif name == "scott":
            sql = resources.files("ossie_ost").joinpath("fixtures/scott.sql")
            sql = sql.read_text(encoding="utf-8")
        else:
            raise OstError(f"unknown fixture {quote(name)}")
        self.close()
        self._connection = duckdb.connect(":memory:")
        self._connection.execute("set TimeZone = 'UTC'")
        if sql:
            self._connection.execute(sql)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def query(self, sql: str, ordered: bool | None = None) -> Table | None:
        """Executes SQL, and returns the result of its last statement."""
        if ordered is None:
            ordered = bool(ORDER_BY.search(sql))
        con = self.connection
        timed_out = threading.Event()

        def interrupt() -> None:
            timed_out.set()
            con.interrupt()

        timer = threading.Timer(self.timeout, interrupt)
        timer.start()
        try:
            statement = cursor = None
            for statement in con.extract_statements(sql):
                cursor = con.execute(statement)
            if cursor is None or cursor.description is None:
                return None
            columns = tuple(d[0] for d in cursor.description)
            if statement.type not in ROW_STATEMENTS and not (
                statement.type in DML_STATEMENTS and columns != ("Count",)
            ):
                return None
            type_names = [str(d[1]) for d in cursor.description]
            families = []
            for t in type_names:
                f = family(t)
                if f is None:
                    raise OstError(f"unsupported type {t}")
                families.append(f)
            rows = tuple(
                tuple(
                    format_cell(v, f, t) for v, f, t in zip(row, families, type_names)
                )
                for row in cursor.fetchall()
            )
            return Table(columns, tuple(families), rows, ordered)
        except duckdb.Error:
            if timed_out.is_set():
                raise OstError(f"timeout after {self.timeout:g} seconds") from None
            raise
        finally:
            timer.cancel()

    def execute(self, command: Command) -> Output:
        """Executes a command, and returns its output."""
        try:
            call = split_call(command.body, self.functions)
            if call is None:
                return self.query(command.body)
            name, text = call
            function = self.functions[name]
            try:
                args = parse_args(text, list(function.kinds))
            except ValueError as e:
                raise OstError(f"{name}: {e}") from None
            result = function.fn(self, *args)
            if isinstance(result, Table):
                return result
            return to_value(result)
        except Exception as e:  # noqa: BLE001 - every failure is output
            return Error(_first_line(str(e)) or type(e).__name__)

    def regenerate(self, command: Command) -> str:
        """Runs a command, and returns the output to write after it."""
        actual = self.execute(command)
        if matches(command, actual, bool(self.properties["matchStrict"])):
            return command.expected
        if isinstance(actual, Table):
            actual = reorder(actual, command)
        return render(actual)


def _first_line(message: str) -> str:
    line = message.split("\n", 1)[0]
    return line.replace("\t", " ").replace("\r", "").rstrip(" ")


@dataclass(frozen=True)
class Result:
    path: str
    input: str
    output: str

    @property
    def passed(self) -> bool:
        return self.output == self.input


def run(
    text: str,
    path: str = "<string>",
    *,
    timeout: float = 60,
    functions: Mapping[str, Function] | None = None,
    echo: Callable[[str], None] | None = None,
) -> Result:
    """Runs a script, and returns the regenerated script.

    Raises :class:`ScriptError` if the script is malformed. If ``echo``
    is given, calls it with each piece of the regenerated script as it
    is generated.
    """
    script = parse(text, path)
    session = Session(timeout, functions)
    pieces = []
    try:
        for chunk in script.chunks:
            if isinstance(chunk, Command):
                output = session.regenerate(chunk)
                piece = chunk.text
                if output and not piece.endswith("\n"):
                    piece += "\n"
                piece += output
            else:
                piece = chunk.text
            pieces.append(piece)
            if echo:
                echo(piece)
    finally:
        session.close()
    return Result(path, text, "".join(pieces))
