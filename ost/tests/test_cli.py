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

import pytest

from ossie_ost.cli import run_main

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
        "ost: tests/a.ost failed; regenerated script is target/ost/tests/a.ost\n"
        "--- tests/a.ost\n"
        "+++ target/ost/tests/a.ost\n"
    )
    assert "-> 4 : int\n+> 3 : int\n" in out
    assert (cwd / "tests/a.ost").read_text() == FAIL
    assert (cwd / "target/ost/tests/a.ost").read_text() == PASS


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
