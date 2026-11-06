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

import io
import re
import stat
import tempfile
from pathlib import Path

import pytest

from ossie_ost import run
from ossie_ost.cli import output_path, run_main

PASS = "plus(1, 2);\n> 3 : int\n"
FAIL = "plus(1, 2);\n> 4 : int\n"


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    return tmp_path


def no_outside_dir() -> Path:
    raise AssertionError("outside directory not expected")


def test_help(capsys):
    with pytest.raises(SystemExit) as e:
        run_main(["--help"])
    assert e.value.code == 0
    assert "usage: ost" in capsys.readouterr().out


def test_no_files(capsys):
    with pytest.raises(SystemExit) as e:
        run_main([])
    assert e.value.code == 2


def test_pass(cwd, capsys):
    (cwd / "a.ost").write_text(PASS)
    assert run_main(["a.ost"]) == 0
    assert capsys.readouterr().out == ""
    assert (cwd / "target/ost/a.ost").read_text() == PASS


def test_fail(cwd, capsys):
    (cwd / "tests").mkdir()
    (cwd / "tests/a.ost").write_text(FAIL)
    assert run_main(["tests/a.ost"]) == 1
    out = capsys.readouterr().out
    assert out.startswith(
        "ost: there were failures\n"
        "diff -u tests/a.ost target/ost/tests/a.ost\n"
        "--- tests/a.ost\n"
        "+++ target/ost/tests/a.ost\n"
    )
    assert "-> 4 : int\n+> 3 : int\n" in out
    assert (cwd / "tests/a.ost").read_text() == FAIL
    assert (cwd / "target/ost/tests/a.ost").read_text() == PASS


def test_fail_twice(cwd, capsys):
    (cwd / "a b.ost").write_text(FAIL)
    (cwd / "c.ost").write_text(FAIL)
    assert run_main(["a b.ost", "c.ost"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line[:1] not in "-+@ >"] == [
        "ost: there were failures",
        "diff -u 'a b.ost' 'target/ost/a b.ost'",
        "diff -u c.ost target/ost/c.ost",
    ]


def test_update(cwd):
    (cwd / "a.ost").write_text(FAIL)
    assert run_main(["--update", "a.ost"]) == 1
    assert (cwd / "a.ost").read_text() == PASS
    assert run_main(["a.ost"]) == 0


def test_directory_skips_output_dir(cwd):
    (cwd / "d/e").mkdir(parents=True)
    (cwd / "d/a.ost").write_text(PASS)
    (cwd / "d/e/b.ost").write_text(PASS)
    (cwd / "d/out").mkdir()
    (cwd / "d/out/c.ost").write_text(FAIL)
    assert run_main(["--output-dir", "d/out", "d"]) == 0
    assert (cwd / "d/out/d/e/b.ost").read_text() == PASS


def test_output_path_independent_of_cwd(cwd, monkeypatch):
    out = output_path(Path("a/b/c.ost"), Path("."), Path("target/ost"), no_outside_dir)
    assert out == Path("target/ost/a/b/c.ost")
    (cwd / "a").mkdir()
    monkeypatch.chdir(cwd / "a")
    out2 = output_path(Path("b/c.ost"), Path(".."), Path("target/ost"), no_outside_dir)
    assert out2 == Path("../target/ost/a/b/c.ost")
    assert out2.resolve() == (cwd / "target/ost/a/b/c.ost").resolve()


def test_output_path_absolute_output_dir(cwd):
    out = output_path(Path("a/c.ost"), Path("."), cwd / "out", no_outside_dir)
    assert out == cwd / "out/a/c.ost"


def test_output_path_outside_dir(cwd):
    (cwd / "d").mkdir()
    x = cwd / "x/test.ost"
    y = cwd / "y/test.ost"
    outside = cwd / "outside"
    out_x = output_path(x, Path("d"), Path("target/ost"), lambda: outside)
    out_y = output_path(y, Path("d"), Path("target/ost"), lambda: outside)
    assert out_x == outside / x.resolve().relative_to(x.resolve().anchor)
    assert out_y == outside / y.resolve().relative_to(y.resolve().anchor)
    assert out_x != out_y


def test_script_outside_dir(cwd, capsys):
    # The regenerated script of a script outside the -d directory is
    # written in a new temporary directory that only the user can write
    # to, and the diff command names it. Each run has its own directory.
    (cwd / "d").mkdir()
    (cwd / "x").mkdir()
    (cwd / "x/a.ost").write_text(FAIL)
    outs = []
    for _ in range(2):
        assert run_main(["-d", "d", "x/a.ost"]) == 1
        m = re.search(r"^diff -u x/a.ost (\S+)$", capsys.readouterr().out, re.M)
        out = Path(m.group(1))
        assert out.read_text() == PASS
        top = out.parents[len(out.relative_to(cwd / "tmp").parts) - 2]
        assert top.parent == cwd / "tmp" and top.name.startswith("ost-")
        assert stat.S_IMODE(top.stat().st_mode) == 0o700
        outs.append(out)
    assert outs[0] != outs[1]


def test_script_inside_dir_makes_no_temporary_directory(cwd):
    (cwd / "a.ost").write_text(FAIL)
    assert run_main(["a.ost"]) == 1
    assert list((cwd / "tmp").iterdir()) == []


def test_file_access(cwd, capsys):
    # A script can read a file beside it, by relative path, if it is
    # under the -d directory, but not otherwise.
    (cwd / "scripts").mkdir()
    (cwd / "scripts/data.txt").write_text("x\n")
    (cwd / "other").mkdir()
    query = "select content from read_text('data.txt');\n"
    expected = run(query, root=cwd, script_dir=cwd / "scripts").output
    assert '"x\\n"' in expected
    (cwd / "scripts/a.ost").write_text(expected)
    assert run_main(["scripts/a.ost"]) == 0
    assert run_main(["-d", ".", "scripts/a.ost"]) == 0
    assert run_main(["-d", "other", "scripts/a.ost"]) == 1
    assert "Permission Error" in capsys.readouterr().out


def test_database_error(cwd, capsys):
    # A script with a database error fails, even if its expected output
    # is the error, and ost names the command.
    message = "Catalog Error: Table with name nonexistent does not exist!"
    text = f"{PASS}select * from nonexistent;\n> error: {message}\n"
    (cwd / "a.ost").write_text(text)
    assert run_main(["a.ost"]) == 1
    out = capsys.readouterr().out
    assert out.startswith(
        f"ost: there were failures\na.ost:3: database error: {message}\n"
    )


def test_database_error_whole_message(cwd, capsys):
    # ost prints the whole message of a database error, not just its
    # first line; the script's output has only the first line.
    (cwd / "a.ost").write_text("select * fro emp;\n")
    assert run_main(["a.ost"]) == 1
    out = capsys.readouterr().out
    assert (
        'a.ost:1: database error: Parser Error: syntax error at or near "emp"\n'
        "\n"
        "LINE 1: select * fro emp\n"
        "                     ^^^\n"
    ) in out
    assert (cwd / "target/ost/a.ost").read_text() == (
        "select * fro emp;\n"
        '> error: Parser Error: syntax error at or near "emp"\n'
    )


def test_malformed(cwd, capsys):
    (cwd / "a.ost").write_text("plus(1, 2;\n")
    (cwd / "b.ost").write_text(FAIL)
    assert run_main(["a.ost", "b.ost"]) == 2
    assert capsys.readouterr().err == (
        "a.ost:1:1: unterminated command (open '(' at 1:5)\n"
    )


def test_missing_file(cwd, capsys):
    assert run_main(["nope.ost"]) == 2
    assert "nope.ost" in capsys.readouterr().err


def test_stdin(cwd, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("plus(1, 2);\n"))
    assert run_main(["-"]) == 1
    assert capsys.readouterr().out == PASS
    assert not (cwd / "target").exists()
