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

import pytest

from ossie_ost.parser import (
    Command,
    Kind,
    Problem,
    ScriptError,
    Verbatim,
    check_whitespace,
    parse,
    parse_args,
    read_raw_string,
    split_call,
)

EXAMPLE = """\
# An example script.

connect("scott");

select *
from emp
where job = 'CLERK';
+-------+--------+
| empno | ename  |
+-------+--------+
|  7934 | MILLER |
|  7369 | SMITH  |
+-------+--------+
(2 rows)

plus(1, 2);
> 3 : int

expand(select 1);
> {_|
select 1
  as x
|_} : string
select * from nonexistent;
> error: Catalog Error: Table with name nonexistent does not exist!

# End example.ost
"""


def commands(text: str) -> list[Command]:
    return [c for c in parse(text).chunks if isinstance(c, Command)]


def bodies(text: str) -> list[str]:
    return [c.body for c in commands(text)]


def problems(text: str) -> list[Problem]:
    with pytest.raises(ScriptError) as e:
        parse(text)
    return e.value.problems


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        EXAMPLE,
        "",
        "\n",
        "# just a comment\n",
        "select 1;",  # no newline at the end
        "select 1;\n+---+\n| x |\n+---+\n| 1 |\n+---+\n(1 row)",
        "select 1;\nselect 2;\n\n\nselect 3;\n",
    ],
)
def test_round_trip(text: str) -> None:
    assert parse(text).text() == text


def test_chunks() -> None:
    chunks = parse(EXAMPLE).chunks
    assert [type(c) for c in chunks] == [
        Verbatim,
        Command,
        Verbatim,
        Command,
        Verbatim,
        Command,
        Verbatim,
        Command,
        Command,
        Verbatim,
    ]
    assert chunks[0] == Verbatim("# An example script.\n\n")
    select = chunks[3]
    assert isinstance(select, Command)
    assert select.line == 5
    assert select.body == "select *\nfrom emp\nwhere job = 'CLERK'"
    assert select.text == "select *\nfrom emp\nwhere job = 'CLERK';\n"
    assert len(select.blocks) == 1
    assert select.blocks[0].endswith("(2 rows)\n")


def test_command_without_output() -> None:
    (c,) = commands('connect("scott");\n')
    assert c.expected == ""
    assert c.blocks == ()


def test_raw_string_output() -> None:
    expand = commands(EXAMPLE)[3]
    assert expand.blocks == ("> {_|\nselect 1\n  as x\n|_} : string\n",)


def test_raw_string_output_contains_output_lines() -> None:
    # Lines inside a raw string that look like output or commands
    # belong to the raw string.
    text = "f();\n> {|a\n+--+\n> b;\n# c\n|} : string\nselect 1;\n"
    f, select = commands(text)
    assert f.blocks == ("> {|a\n+--+\n> b;\n# c\n|} : string\n",)
    assert select.body == "select 1"


def test_several_output_blocks() -> None:
    text = "select 1;\n> 1 : int\n> error: oops\n+---+\n| x |\n+---+\n(0 rows)\n"
    (c,) = commands(text)
    assert c.blocks == (
        "> 1 : int\n",
        "> error: oops\n",
        "+---+\n| x |\n+---+\n(0 rows)\n",
    )


def test_table_without_row_count() -> None:
    (c, d) = commands("select 1;\n+---+\n| x |\n+---+\nselect 2;\n")
    assert c.blocks == ("+---+\n| x |\n+---+\n",)
    assert d.body == "select 2"


def test_output_without_command() -> None:
    assert problems("# comment\n> 1 : int\n") == [
        Problem(2, 1, "output without a command")
    ]
    assert problems("select 1;\n> 1 : int\n\n> 2 : int\n") == [
        Problem(4, 1, "output without a command")
    ]


def test_unterminated_raw_string_output() -> None:
    assert problems("f();\n> {|abc\n") == [Problem(2, 3, "unterminated raw string")]


# ---------------------------------------------------------------------------
# Whitespace
# ---------------------------------------------------------------------------


def test_whitespace_problems() -> None:
    text = "select 1;\nselect\t2;\nselect 3; \n\r\n  \nok\n"
    assert check_whitespace(text) == [
        Problem(2, 7, "tab"),
        Problem(3, 10, "trailing space"),
        Problem(4, 1, "carriage return"),
        Problem(5, 1, "trailing space"),
    ]


def test_whitespace_in_string_literal() -> None:
    # Even inside a string literal, a tab or trailing space is an error.
    assert problems("select 'a \nb';\n") == [Problem(1, 10, "trailing space")]


def test_script_error_message() -> None:
    with pytest.raises(ScriptError) as e:
        parse("a\t;\nb; \n", path="tests/x.ost")
    assert str(e.value) == ("tests/x.ost:1:2: tab\ntests/x.ost:2:3: trailing space")


# ---------------------------------------------------------------------------
# Command termination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # A ';' in a single-quoted string at the end of a line.
        ("select 'a;\nb' as s;\n", ["select 'a;\nb' as s"]),
        # A ';' in a double-quoted identifier.
        ('select 1 as "a;\nb";\n', ['select 1 as "a;\nb"']),
        # Escaped quotes.
        ("select 'it''s;\n';\n", ["select 'it''s;\n'"]),
        ('select 1 as "x"";\n";\n', ['select 1 as "x"";\n"']),
        # Block comment.
        ("select /* a;\n b */ 1;\n", ["select /* a;\n b */ 1"]),
        # Line comment; the ';' at the end of it is not a terminator.
        ("select 1 -- one;\n, 2;\n", ["select 1 -- one;\n, 2"]),
        # A ';' followed by a comment does not end the command.
        ("select 1; -- one\nselect 2;\n", ["select 1; -- one\nselect 2"]),
        # Mid-line ';'.
        ("select 1; select 2;\n", ["select 1; select 2"]),
        # Nested parentheses.
        (
            "query(select (1 +\n (2));\n select 3);\nselect 4;\n",
            ["query(select (1 +\n (2));\n select 3)", "select 4"],
        ),
        # Raw strings.
        ("ost({|select 1;\n|});\n", ["ost({|select 1;\n|})"]),
        ("ost({x|a;\n|};\n|x});\n", ["ost({x|a;\n|};\n|x})"]),
        ("ost({_|\na;\n|_});\n", ["ost({_|\na;\n|_})"]),
        # A comment line inside a command is part of the command.
        ("select 1\n# not a comment\n;\n", ["select 1\n# not a comment\n"]),
        # Quotes inside comments, and comment markers inside quotes.
        ("select 1 -- it's\n;\n", ["select 1 -- it's\n"]),
        ("select '--', '/*';\n", ["select '--', '/*'"]),
        # Blank lines inside a command.
        ("select 1,\n\n2;\n", ["select 1,\n\n2"]),
    ],
)
def test_termination(text: str, expected: list[str]) -> None:
    assert bodies(text) == expected
    assert parse(text).text() == text


@pytest.mark.parametrize(
    "text, problem",
    [
        ("select 1", Problem(1, 1, "unterminated command")),
        (
            "\nquery(select (1);\n",
            Problem(2, 1, "unterminated command (open '(' at 2:6)"),
        ),
        (
            "select 1;\nselect 'a;\n",
            Problem(2, 1, "unterminated command (open ''' at 2:8)"),
        ),
        (
            'select "a;\n',
            Problem(1, 1, "unterminated command (open '\"' at 1:8)"),
        ),
        (
            "select 1 /* ;\n",
            Problem(1, 1, "unterminated command (open '/*' at 1:10)"),
        ),
        (
            "ost({x|;\n|});\n",
            Problem(1, 1, "unterminated command (open '{x|' at 1:5)"),
        ),
        ("select 1);\n", Problem(1, 9, "unmatched ')'")),
        ("select 1;\nselect\n  1);\n", Problem(3, 4, "unmatched ')'")),
    ],
)
def test_malformed_command(text: str, problem: Problem) -> None:
    assert problems(text) == [problem]


# ---------------------------------------------------------------------------
# Function calls
# ---------------------------------------------------------------------------

FUNCTIONS = {"query", "plus", "isValid"}


@pytest.mark.parametrize(
    "body, expected",
    [
        ("plus(1, 2)", ("plus", "1, 2")),
        ("plus (1, 2)", ("plus", "1, 2")),
        ("plus\n  (1, 2)\n", ("plus", "1, 2")),
        ("  plus()", ("plus", "")),
        ("query(select f(x), ')' from t)", ("query", "select f(x), ')' from t")),
        ("isValid(\n  select 1)", ("isValid", "\n  select 1")),
        # Not calls.
        ("select plus(1, 2)", None),
        ("Plus(1, 2)", None),  # names are case-sensitive
        ("isvalid(select 1)", None),
        ("plusx(1, 2)", None),
        ("plus", None),
        ("plus + 1", None),
        ("values (1)", None),
    ],
)
def test_split_call(body: str, expected: tuple[str, str] | None) -> None:
    assert split_call(body, FUNCTIONS) == expected


def test_split_call_trailing_text() -> None:
    with pytest.raises(ValueError, match="unexpected text after call to plus"):
        split_call("plus(1, 2) + 3", FUNCTIONS)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, kinds, expected",
    [
        ("", [], []),
        ("  ", [], []),
        ("1, -2", [Kind.INT, Kind.INT], [1, -2]),
        ("1.5, -2.5e3, 3", [Kind.FLOAT] * 3, [1.5, -2500.0, 3.0]),
        ("true,false", [Kind.BOOLEAN, Kind.BOOLEAN], [True, False]),
        ('"a\\"b\\n\\u00e9"', [Kind.STRING], ['a"b\né']),
        ('"x", 1', [Kind.STRING, Kind.INT], ["x", 1]),
        ("{|a\nb|}", [Kind.STRING], ["a\nb"]),
        ("\n  select a, b\n  from t\n", [Kind.QUERY], ["select a, b\n  from t"]),
        ('"scott", select 1, 2', [Kind.STRING, Kind.QUERY], ["scott", "select 1, 2"]),
    ],
)
def test_parse_args(text: str, kinds: list[Kind], expected: list[object]) -> None:
    assert parse_args(text, kinds) == expected


@pytest.mark.parametrize(
    "text, kinds, message",
    [
        ("1", [], "expected 0 arguments, got more"),
        ("1", [Kind.INT, Kind.INT], "expected 2 arguments, got 1"),
        ("1, 2, 3", [Kind.INT, Kind.INT], "expected 2 arguments, got more"),
        ("1.5", [Kind.INT], "expected an integer"),
        ('"1"', [Kind.INT], "expected an integer"),
        ("1", [Kind.STRING], "expected a string"),
        ("yes", [Kind.BOOLEAN], "expected true or false"),
        ("trueish", [Kind.BOOLEAN], "expected true or false"),
        ("", [Kind.QUERY], "expected a query"),
        ("1", [Kind.QUERY, Kind.INT], "a query must be the last parameter"),
    ],
)
def test_parse_args_error(text: str, kinds: list[Kind], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_args(text, kinds)


@pytest.mark.parametrize(
    "text, content",
    [
        ("{|abc|}", "abc"),
        ("{||}", ""),
        ("{|a\nb\n|}", "a\nb\n"),
        # After '{|', a line break is content.
        ("{|\na|}", "\na"),
        # After '{_|', a line break is not content.
        ("{_|\n  a\n|_}", "  a\n"),
        ("{_|a|_}", "a"),
        # A longer tag, because the content contains '|}'.
        ("{a|x|}y|a}", "x|}y"),
        ("{_a|\nx|_}y|_a}", "x|_}y"),
        # Quotes and backslashes are verbatim.
        ('{|"\\n"|}', '"\\n"'),
    ],
)
def test_read_raw_string(text: str, content: str) -> None:
    assert read_raw_string(text, 0) == (content, len(text))
