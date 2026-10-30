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

from pathlib import Path

import pytest

import ossie_ost
from ossie_ost import Function, Kind, ScriptError, run
from ossie_ost.runner import raw_string

SCRIPTS = sorted((Path(__file__).parent / "scripts").glob("*.ost"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_script(path):
    text = path.read_text(encoding="utf-8")
    result = run(text, str(path))
    assert result.output == text


def output(text: str) -> str:
    """Runs a script, and returns the regenerated script."""
    return run(text).output


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


def test_mismatch_keeps_order_of_matching_command():
    # The first command's rows are correct but in a different order
    # than returned (ORDER BY guarantees the actual order); the second
    # command's output is wrong. Only the second command's output
    # should change.
    first = (
        "queryUnordered(select * from (values (1), (2), (3)) as t(x)"
        " order by x);\n"
        "+---+\n"
        "| x |\n"
        "+---+\n"
        "| 3 |\n"
        "| 1 |\n"
        "| 2 |\n"
        "+---+\n"
        "(3 rows)\n"
    )
    assert output(first + "plus(1, 2);\n> 4 : int\n") == (
        first + "plus(1, 2);\n> 3 : int\n"
    )


def test_wrong_row_count():
    text = "select 1 as x;\n+---+\n| x |\n+---+\n| 1 |\n+---+\n(2 rows)\n"
    assert not run(text).passed


def test_value_type_must_match():
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
