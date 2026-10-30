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

import datetime
from pathlib import Path

import pytest

import ossie_ost
from ossie_ost import Function, Kind, ScriptError, run
from ossie_ost.runner import format_interval, format_real, raw_string

SCRIPTS = sorted((Path(__file__).parent / "scripts").glob("*.ost"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_script(path):
    text = path.read_text(encoding="utf-8")
    result = run(text, str(path))
    assert result.output == text


def output(text: str) -> str:
    """Runs a script, and returns the regenerated script."""
    return run(text).output


def test_new_output():
    assert output("plus(1, 2);\n") == "plus(1, 2);\n> 3 : int\n"


def test_no_output():
    text = "create table t (i int);\ninsert into t values (1);\n"
    assert output(text) == text


def test_last_statement():
    assert output("select 1 as a; select 2 as b;\n") == (
        "select 1 as a; select 2 as b;\n+---+\n| b |\n+---+\n| 2 |\n+---+\n(1 row)\n"
    )


def test_unordered_rows_match():
    text = (
        "select * from (values (1, 'a'), (2, 'b')) as t(x, y);\n"
        "+--+--+\n"
        "| X | y |\n"
        "+--+--+\n"
        "|2|b|\n"
        "|1|a|\n"
        "+--+--+\n"
        "(2 rows)\n"
    )
    assert run(text).passed


def test_ordered_rows_must_be_in_order():
    text = (
        "select * from (values (1), (2)) as t(x) order by x;\n"
        "+---+\n"
        "| x |\n"
        "+---+\n"
        "| 2 |\n"
        "| 1 |\n"
        "+---+\n"
        "(2 rows)\n"
    )
    assert not run(text).passed


def test_query_unordered_overrides_order_by():
    text = (
        "queryUnordered(select * from (values (1), (2)) as t(x) order by x);\n"
        "+---+\n"
        "| x |\n"
        "+---+\n"
        "| 2 |\n"
        "| 1 |\n"
        "+---+\n"
        "(2 rows)\n"
    )
    assert run(text).passed


def test_mismatch_keeps_expected_row_order():
    text = (
        "select * from (values (1), (2), (3)) as t(x);\n"
        "+---+\n"
        "| x |\n"
        "+---+\n"
        "| 3 |\n"
        "| 9 |\n"
        "| 1 |\n"
        "+---+\n"
        "(3 rows)\n"
    )
    assert output(text).splitlines()[4:7] == ["| 3 |", "| 1 |", "| 2 |"]


def test_wrong_row_count():
    text = "select 1 as x;\n+---+\n| x |\n+---+\n| 1 |\n+---+\n(2 rows)\n"
    assert not run(text).passed


def test_strings_and_nulls():
    text = "select 'a b' as s union all select '' union all select null;\n"
    lines = output(text).splitlines()
    assert lines[4:7] == ["| a b |", '| ""  |', "|     |"]
    assert run(output(text)).passed


@pytest.mark.parametrize(
    "sql, cell",
    [
        ("1::hugeint", "1"),
        ("-0.50::decimal(5, 2)", "-0.50"),
        ("0.1::real", "0.1"),
        ("0.1::double + 0.2::double", "0.30000000000000004"),
        ("1e16::double", "1e+16"),
        ("'a|b'", '"a|b"'),
        ("'x' || chr(10) || 'y'", '"x\\ny"'),
        ("date '2024-03-10'", "2024-03-10"),
        ("time '12:34:56'", "12:34:56"),
        ("timestamp '2024-03-10 12:34:56.5'", "2024-03-10 12:34:56.5"),
        ("timestamptz '2024-03-10 12:34:56+05'", "2024-03-10 07:34:56+00"),
        ("interval '25 hours'", "P1DT1H"),
        ("true", "true"),
    ],
)
def test_format_cell(sql, cell):
    lines = output(f"select {sql} as c;\n").splitlines()
    assert lines[4].strip("| ") == cell


def test_format_real():
    assert format_real(0.10000000149011612) == "0.1"
    assert format_real(float("inf")) == "inf"


@pytest.mark.parametrize(
    "delta, text",
    [
        (datetime.timedelta(0), "PT0S"),
        (datetime.timedelta(hours=-1), "-PT1H"),
        (datetime.timedelta(days=3, hours=4, minutes=5, seconds=6.5), "P3DT4H5M6.5S"),
        (datetime.timedelta(days=2), "P2D"),
    ],
)
def test_format_interval(delta, text):
    assert format_interval(delta) == text


def test_unsupported_type():
    assert output("select [1] as l;\n").endswith(
        "> error: ost: unsupported type INTEGER[]\n"
    )


def test_error():
    assert output("select * from nonexistent;\n").endswith(
        "> error: Catalog Error: Table with name nonexistent does not exist!\n"
    )


def test_wrong_argument():
    assert output('plus(1, "x");\n').endswith(
        "> error: ost: plus: expected an integer\n"
    )


def test_connect_scott():
    text = 'connect("scott");\nselect count(*) as c from scott.emp, dept;\n'
    assert "| 56 |" in output(text)


def test_value_matching():
    assert run("plus(1, 2);\n>    3:int\n").passed
    assert not run("plus(1, 2);\n> 3 : double\n").passed


def test_two_blocks_do_not_match():
    text = "plus(1, 2);\n> 3 : int\n> 3 : int\n"
    assert output(text) == "plus(1, 2);\n> 3 : int\n"


def test_match_strict():
    text = 'set("matchStrict", true);\nplus(1, 2);\n>  3 : int\n'
    assert output(text).endswith("plus(1, 2);\n> 3 : int\n")


def test_raw_string():
    assert raw_string("a\nb") == "{|a\nb|}"
    assert raw_string("a\n b\n") == "{_|\na\n b\n|_}"
    assert raw_string("a|}\nb") == "{a|a|}\nb|a}"


def test_ost_function():
    text = "ost({|plus(1, 2);\n|});\n"
    assert output(text) == text + "> {|plus(1, 2);\n> 3 : int\n|} : string\n"
    assert run(output(text)).passed


def test_ost_function_malformed():
    assert output("ost({|select (1;\n|});\n").endswith(
        "> error: <string>:1:1: unterminated command (open '(' at 1:8)\n"
    )


def test_timeout():
    sql = "select count(*) as c from range(100000000000) as a(x);\n"
    assert run(sql, timeout=0.1).output.endswith(
        "> error: ost: timeout after 0.1 seconds\n"
    )


def test_custom_function():
    functions = {"twice": Function((Kind.STRING,), lambda session, s: s + s)}
    result = run('twice("ab");\n', functions=functions)
    assert result.output == 'twice("ab");\n> "abab" : string\n'


def test_malformed():
    with pytest.raises(ScriptError) as e:
        ossie_ost.run("select 1; \n", "x.ost")
    assert str(e.value) == "x.ost:1:10: trailing space"
