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

import time
from pathlib import Path

import pytest

import ossie_ost
from ossie_ost import Function, Kind, ScriptError, run
from ossie_ost.runner import format_real, quote, raw_string

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
    )
    assert not run(text).passed


def test_error_with_line_separator():
    # The error message contains U+2028, which ost writes unescaped;
    # the regenerated script must still parse, and pass.
    def fail(session, message):
        raise ValueError(message)

    functions = {"fail": Function((Kind.STRING,), fail)}
    text = 'fail("a\u2028b");\n'
    result = run(text, functions=functions)
    assert result.output.endswith("> error: a\u2028b\n")
    assert run(result.output, functions=functions).passed


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
    )
    assert output(first + "plus(1, 2);\n> 4 : int\n") == (
        first + "plus(1, 2);\n> 3 : int\n"
    )


def test_mismatch_large_table():
    # A table of 80,000 rows that does not match: the expected rows are
    # in reverse order, and one is wrong. When putting the rows in the
    # expected order took time quadratic in the number of rows, this
    # took over a minute.
    n = 80_000
    rows = "".join(f"| {i} |\n" for i in range(n - 1, 0, -1)) + "| -1 |\n"
    text = (
        f"select x from range({n}) as t(x);\n"
        f"+---+\n| x |\n+---+\n{rows}+---+\n"
    )
    start = time.perf_counter()
    lines = output(text).splitlines()
    assert time.perf_counter() - start < 10
    expected = [str(i) for i in range(n - 1, -1, -1)]
    assert [line.strip("| ") for line in lines[4 : n + 4]] == expected


def test_row_count_is_not_output():
    # A table has no row count. The line "(1 row)", which earlier versions
    # of ost wrote after a table, starts a command, which never ends.
    text = "select 1 as x;\n+---+\n| x |\n+---+\n| 1 |\n+---+\n(1 row)\n"
    with pytest.raises(ScriptError, match="unterminated command"):
        run(text)


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


def test_quote():
    assert quote("a\nb") == '"a\\nb"'
    assert quote("a\x7f\x85\u2028\u2029b") == '"a\\u007f\\u0085\\u2028\\u2029b"'
    assert quote("caf\u00e9") == '"caf\u00e9"'


def test_format_real():
    assert format_real(0.1) == "0.1"
    # At the binary32 limit, the four-digit candidate 3.403e38 overflows
    # binary32; format_real must try higher precision, not raise.
    largest = 3.4028234663852886e38
    assert format_real(largest) == "3.4028235e+38"
    assert format_real(-largest) == "-3.4028235e+38"


def test_timeout():
    # A timeout fails the script, even if the expected output is the
    # error.
    sql = "select count(*) as c from range(100000000000) as a(x);\n"
    result = run(sql, timeout=0.1)
    assert result.output.endswith("> error: ost: timeout after 0.1 seconds\n")
    result = run(result.output, timeout=0.1)
    assert not result.passed
    assert result.fatal_errors == ((1, "timeout after 0.1 seconds"),)


def test_timeout_between_statements():
    # Each statement is fast, so the timeout almost always expires
    # between statements, when DuckDB ignores an interrupt.
    sql = "select 1 as x; " * 20000 + "select 2 as x;\n"
    result = run(sql, timeout=0.1)
    assert result.output.endswith("> error: ost: timeout after 0.1 seconds\n")
    assert result.fatal_errors == ((1, "timeout after 0.1 seconds"),)


def test_file_access(tmp_path, monkeypatch):
    # A script may read a file in the current directory or below, but
    # not one outside it, even in a directory whose name has the current
    # directory's as a prefix, or through a symbolic link.
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "in.txt").write_text("in\n")
    (tmp_path / "tree2").mkdir()
    (tmp_path / "tree2" / "out.txt").write_text("out\n")
    (root / "link.txt").symlink_to(tmp_path / "tree2" / "out.txt")
    monkeypatch.chdir(root)

    def read(path: str) -> str:
        return output(f"select content from read_text('{path}');\n")

    for path in ["sub/in.txt", str(root / "sub" / "in.txt")]:
        result = read(path)
        assert result.endswith('| "in\\n"  |\n+---------+\n'), path
    for path in [
        "../tree2/out.txt",
        str(tmp_path / "tree2" / "out.txt"),
        "link.txt",
        "/etc/passwd",
    ]:
        assert "> error: Permission Error: Cannot access file" in read(path), path


def test_file_access_root_and_script_dir(tmp_path, monkeypatch):
    # The root, not the current directory, limits access; a relative path
    # is also looked up in the script's directory; and a script directory
    # outside the root gives no access. (A relative path works only if the
    # current directory is under the root.)
    root = tmp_path / "root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "beside.txt").write_text("beside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x\n")
    monkeypatch.chdir(root)

    def read(path: str, **kwargs) -> str:
        return run(f"select content from read_text('{path}');\n", **kwargs).output

    denied = "> error: Permission Error: Cannot access file"
    assert '"beside\\n"' in read("beside.txt", root=root, script_dir=scripts)
    assert denied in read(str(outside / "x.txt"), root=root)
    assert '"x\\n"' in read(str(outside / "x.txt"), root=tmp_path)
    assert denied in read("x.txt", root=root, script_dir=outside)

    # A script run by the ost function has the same root and directory.
    nested = "ost({|select content from read_text('beside.txt');\n|});\n"
    assert '"beside\\n"' in run(nested, root=root, script_dir=scripts).output
    x = outside / "x.txt"
    nested = f"ost({{|select content from read_text('{x}');\n|}});\n"
    assert "Permission Error" in run(nested, root=root, script_dir=scripts).output


def test_database_error_fails():
    # A database error fails the script, even if the expected output is
    # the error. An error that ost raises does not.
    message = "Catalog Error: Table with name nonexistent does not exist!"
    text = f"plus(1, 2);\n> 3 : int\nselect * from nonexistent;\n> error: {message}\n"
    result = run(text)
    assert result.output == text
    assert not result.passed
    [(line, whole)] = result.fatal_errors
    assert line == 3 and whole.splitlines()[0] == "database error: " + message
    assert "LINE 1: select * from nonexistent" in whole
    text = 'plus(1, "x");\n> error: ost: plus: expected an integer\n'
    assert run(text).passed


def test_database_error_in_nested_script_fails():
    text = "ost({|select * from nonexistent;\n|});\n"
    result = run(run(text).output)
    assert not result.passed
    [(line, whole)] = result.fatal_errors
    assert line == 1
    assert whole.startswith("database error: Catalog Error: Table with name")


@pytest.mark.parametrize(
    "command, message",
    [
        (
            "select * from read_text('/etc/passwd')",
            'Permission Error: Cannot access file "/etc/passwd"',
        ),
        (
            "select * from read_csv('/etc/passwd')",
            'Permission Error: Cannot access file "/etc/passwd"',
        ),
        (
            "select * from read_text('../../../../../../etc/passwd')",
            'Permission Error: Cannot access file "../../../../../../etc/passwd"',
        ),
        (
            "copy (select 1) to '/tmp/ost.csv'",
            'Permission Error: Cannot access file "/tmp/ost.csv"',
        ),
        ("attach '/tmp/ost.db'", 'Permission Error: Cannot access file "/tmp/ost.db"'),
        (
            "set enable_external_access = true",
            "Invalid Input Error: Cannot enable external access",
        ),
        (
            "set allowed_directories = ['/']",
            "Invalid Input Error: Cannot change allowed_directories",
        ),
    ],
)
def test_file_access_denied(command, message):
    # A script cannot read or write a file outside the root, nor enable
    # access to one. The database refuses, so the script fails.
    result = run(command + ";\n")
    assert not result.passed
    [(line, actual)] = result.fatal_errors
    assert line == 1 and actual.startswith("database error: " + message), actual


def test_custom_function():
    functions = {"twice": Function((Kind.STRING,), lambda session, s: s + s)}
    result = run('twice("ab");\n', functions=functions)
    assert result.output == 'twice("ab");\n> "abab" : string\n'


def test_malformed():
    with pytest.raises(ScriptError) as e:
        ossie_ost.run("select 1; \n", "x.ost")
    assert str(e.value) == "x.ost:1:10: trailing space"
