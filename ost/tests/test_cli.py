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
from pathlib import Path

import pytest

from ossie_ost.cli import OUTSIDE_DIR, output_path, run_main

PASS = "plus(1, 2);\n> 3 : int\n"
FAIL = "plus(1, 2);\n> 4 : int\n"


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


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
    out = output_path(Path("a/b/c.ost"), Path("."), Path("target/ost"))
    assert out == Path("target/ost/a/b/c.ost")
    (cwd / "a").mkdir()
    monkeypatch.chdir(cwd / "a")
    out2 = output_path(Path("b/c.ost"), Path(".."), Path("target/ost"))
    assert out2 == Path("../target/ost/a/b/c.ost")
    assert out2.resolve() == (cwd / "target/ost/a/b/c.ost").resolve()


def test_output_path_absolute_output_dir(cwd):
    out = output_path(Path("a/c.ost"), Path("."), cwd / "out")
    assert out == cwd / "out/a/c.ost"


def test_output_path_outside_dir(cwd):
    (cwd / "d").mkdir()
    x = cwd / "x/test.ost"
    y = cwd / "y/test.ost"
    out_x = output_path(x, Path("d"), Path("target/ost"))
    out_y = output_path(y, Path("d"), Path("target/ost"))
    assert out_x == OUTSIDE_DIR / x.resolve().relative_to(x.resolve().anchor)
    assert out_y == OUTSIDE_DIR / y.resolve().relative_to(y.resolve().anchor)
    assert out_x != out_y


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
