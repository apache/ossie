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

"""Command-line behavior: what the user actually types, and what they get back.

Covers the input shapes people reach for first -- a whole model directory, a single
file, and (a common mistake) just the view -- plus the exit codes and where output
goes, since those are the converter's contract with a shell script.
"""

import os
import pathlib
import subprocess
import sys

import pytest
from _util import REPO_ROOT, load_fixture_dir, parse

from ossie_cube.cli import main

_ORDERS = (
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: public.orders\n"
    "    dimensions:\n"
    "      - name: id\n"
    "        sql: id\n"
    "        type: number\n"
    "        primary_key: true\n"
    "    measures:\n"
    "      - name: count\n"
    "        type: count\n"
)
_VIEW = (
    "views:\n"
    "  - name: sales\n"
    "    description: Sales overview\n"
    "    cubes:\n"
    "      - join_path: orders\n"
    "        includes: '*'\n"
)


def _write(root, **files):
    for rel, text in files.items():
        path = root / rel.replace("|", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# --- input shapes ---------------------------------------------------------------

def test_a_model_directory_converts(tmp_path, capsys):
    model = _write(tmp_path / "model", **{
        "cubes|orders.yml": _ORDERS, "views|sales.yml": _VIEW})
    assert main(["import", "-i", str(model)]) == 0
    doc = parse(capsys.readouterr().out)
    assert doc["semantic_model"][0]["name"] == "sales"


def test_a_single_file_converts(tmp_path, capsys):
    """Pointing at one `.yml` is a natural thing to try and there is nothing
    ambiguous about it, so it is accepted rather than refused on a technicality."""
    path = tmp_path / "orders.yml"
    path.write_text(_ORDERS)
    assert main(["import", "-i", str(path)]) == 0
    doc = parse(capsys.readouterr().out)
    assert [d["name"] for d in doc["semantic_model"][0]["datasets"]] == ["orders"]


def test_several_paths_merge_into_one_model(tmp_path, capsys):
    """Cube has a single model root, but converting part of a model -- or files from
    different trees -- should not require assembling a directory first."""
    a = tmp_path / "cubes" / "orders.yml"
    b = tmp_path / "views" / "sales.yml"
    for path, text in ((a, _ORDERS), (b, _VIEW)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert main(["import", "-i", str(a), str(b)]) == 0
    model = parse(capsys.readouterr().out)["semantic_model"][0]
    assert model["name"] == "sales"          # the view was picked up
    assert [d["name"] for d in model["datasets"]] == ["orders"]


def test_several_paths_are_keyed_relative_to_their_common_parent(tmp_path, capsys):
    """The keys decide where export writes the files back, so two inputs from
    different subtrees have to stay distinguishable."""
    a = tmp_path / "cubes" / "orders.yml"
    b = tmp_path / "views" / "sales.yml"
    for path, text in ((a, _ORDERS), (b, _VIEW)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(a), str(b), "-o", str(out)]) == 0
    back = tmp_path / "back"
    assert main(["export", "-i", str(out), "-o", str(back)]) == 0
    capsys.readouterr()
    assert (back / "cubes" / "orders.yml").is_file()
    assert (back / "views" / "sales.yml").is_file()


def test_mixing_a_directory_and_a_file_works(tmp_path, capsys):
    model = _write(tmp_path / "model", **{"cubes|orders.yml": _ORDERS})
    extra = tmp_path / "extra.yml"
    extra.write_text(_VIEW)
    assert main(["import", "-i", str(model), str(extra)]) == 0
    assert parse(capsys.readouterr().out)["semantic_model"][0]["name"] == "sales"


def test_overlapping_inputs_are_reported(tmp_path, capsys):
    """Passing a directory and a file inside it is an easy mistake (an overlapping
    glob), and it would otherwise read the same file twice."""
    model = _write(tmp_path / "model", **{"cubes|orders.yml": _ORDERS})
    assert main(["import", "-i", str(model),
                 str(model / "cubes" / "orders.yml")]) == 1
    err = capsys.readouterr().err
    assert "both resolve to 'cubes/orders.yml'" in err


def test_the_same_cube_in_two_inputs_is_reported(tmp_path, capsys):
    a = tmp_path / "one" / "orders.yml"
    b = tmp_path / "two" / "orders.yml"
    for path in (a, b):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_ORDERS)
    # Distinct keys ('one/orders.yml', 'two/orders.yml'), but the same cube name.
    assert main(["import", "-i", str(a), str(b)]) == 1
    assert "defined twice" in capsys.readouterr().err


def test_a_single_path_is_keyed_exactly_as_before(tmp_path, capsys):
    """The multi-path anchor must not change the one-directory case, since the keys
    are what export writes back."""
    model = _write(tmp_path / "model", **{
        "cubes|orders.yml": _ORDERS, "views|sales.yml": _VIEW})
    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(model), "-o", str(out)]) == 0
    back = tmp_path / "back"
    assert main(["export", "-i", str(out), "-o", str(back)]) == 0
    capsys.readouterr()
    assert (back / "cubes" / "orders.yml").is_file()
    assert (back / "views" / "sales.yml").is_file()


def test_only_a_view_file_is_refused_with_an_actionable_message(tmp_path, capsys):
    """The likeliest mistake for a view-first user: a Cube view looks like the whole
    model, but it projects members from cubes and defines none, so the error names
    the cubes whose files are missing rather than claiming nothing was recognized."""
    path = tmp_path / "sales.yml"
    path.write_text(_VIEW)
    assert main(["import", "-i", str(path)]) == 1
    err = capsys.readouterr().err
    assert "found only view(s) 'sales' and no cubes" in err
    assert "projects members from cubes" in err
    assert "'orders'" in err  # named from the view's join_path


def test_a_view_with_no_cube_references_still_explains_itself(tmp_path, capsys):
    path = tmp_path / "bare.yml"
    path.write_text("views:\n  - name: sales\n    description: Sales\n")
    assert main(["import", "-i", str(path)]) == 1
    assert "Include the files defining the cubes it draws from" in \
        capsys.readouterr().err


def test_a_missing_path_is_reported_not_traced(tmp_path, capsys):
    assert main(["import", "-i", str(tmp_path / "nope")]) == 1
    assert "is not a file or directory" in capsys.readouterr().err


def test_an_empty_directory_is_reported(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["import", "-i", str(empty)]) == 1
    assert "holds no files" in capsys.readouterr().err


def test_node_modules_and_dotfiles_are_skipped(tmp_path, capsys):
    model = _write(tmp_path / "model", **{
        "cubes|orders.yml": _ORDERS,
        "node_modules|junk.yml": "cubes:\n  - name: junk\n    sql_table: t\n",
        ".hidden.yml": "cubes:\n  - name: hidden\n    sql_table: t\n",
    })
    assert main(["import", "-i", str(model)]) == 0
    doc = parse(capsys.readouterr().out)
    assert [d["name"] for d in doc["semantic_model"][0]["datasets"]] == ["orders"]


# --- output and exit codes ------------------------------------------------------

def test_output_goes_to_a_file_when_asked(tmp_path, capsys):
    model = _write(tmp_path / "model", **{"cubes|orders.yml": _ORDERS})
    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(model), "-o", str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert parse(out.read_text())["semantic_model"][0]["datasets"]


def test_issues_go_to_stderr_so_stdout_stays_pipeable(tmp_path, capsys):
    model = _write(tmp_path / "model", **{
        "cubes|users.yml": (
            "cubes:\n"
            "  - name: users\n"
            "    sql_table: public.users\n"
            "    dimensions:\n"
            "      - name: home\n"
            "        type: geo\n"
            "        latitude:\n"
            "          sql: lat\n"
            "        longitude:\n"
            "          sql: lon\n"
        )})
    assert main(["import", "-i", str(model)]) == 0
    captured = capsys.readouterr()
    assert "GEO_DIMENSION_SPLIT" in captured.err
    assert "conversion issue" in captured.err
    parse(captured.out)  # stdout is still clean YAML


def test_fanout_warns_by_default_and_the_flag_exits_nonzero(tmp_path, capsys):
    model = _write(tmp_path / "model", **{"cubes|m.yml": (
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.user_id = {users}.id\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: id\n"
        "        sql: id\n"
        "        type: number\n"
        "        primary_key: true\n"
        "    measures:\n"
        "      - name: ltv\n"
        "        sql: \"{CUBE}.ltv\"\n"
        "        type: sum\n"
    )})
    assert main(["import", "-i", str(model)]) == 0
    captured = capsys.readouterr()
    assert "FANOUT_UNSAFE_METRIC" in captured.err
    assert parse(captured.out)["semantic_model"][0]["metrics"]

    assert main(["import", "-i", str(model), "--strict-fanout"]) == 1
    assert "FANOUT_UNSAFE_METRIC" in capsys.readouterr().err


def test_view_and_name_flags_take_effect(tmp_path, capsys):
    model = _write(tmp_path / "model", **{
        "cubes|orders.yml": _ORDERS,
        "views|a.yml": "views:\n  - name: a\n    description: A\n",
        "views|b.yml": "views:\n  - name: b\n    description: B\n",
    })
    assert main(["import", "-i", str(model), "--view", "b"]) == 0
    assert parse(capsys.readouterr().out)["semantic_model"][0]["description"] == "B"

    assert main(["import", "-i", str(model), "--view", "b",
                 "--name", "custom"]) == 0
    assert parse(capsys.readouterr().out)["semantic_model"][0]["name"] == "custom"

    assert main(["import", "-i", str(model), "--view", "ghost"]) == 1
    assert "not found" in capsys.readouterr().err


# --- export ---------------------------------------------------------------------

def test_export_writes_the_model_directory(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["export", "-i",
                 str(REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"),
                 "-o", str(out)]) == 0
    assert (out / "model" / "cubes" / "store_sales.yml").is_file()
    assert (out / "model" / "views" / "tpcds_retail_model.yml").is_file()
    assert "Wrote 6 file(s)" in capsys.readouterr().err


def test_export_of_a_missing_input_is_reported(tmp_path, capsys):
    assert main(["export", "-i", str(tmp_path / "nope.yaml"),
                 "-o", str(tmp_path / "out")]) == 1
    assert "Error:" in capsys.readouterr().err


def test_a_cli_round_trip_reproduces_the_fixture(tmp_path, capsys):
    fixture = load_fixture_dir("tpcds_cube")
    src = _write(tmp_path / "src", **{k.replace("/", "|"): v
                                      for k, v in fixture.items()})
    ossie = tmp_path / "model.yaml"
    back = tmp_path / "back"
    assert main(["import", "-i", str(src), "-o", str(ossie)]) == 0
    assert main(["export", "-i", str(ossie), "-o", str(back)]) == 0
    capsys.readouterr()
    for rel in fixture:
        assert (back / rel.replace("/", "/")).is_file(), rel
        assert parse((back / rel).read_text()) == parse(fixture[rel])


def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_a_non_ascii_model_converts_under_a_non_utf8_locale(tmp_path):
    """A German title or a Russian description must not depend on the machine's locale.

    Python's `open()` defaults to the locale's preferred encoding, so on any host that is
    not UTF-8 -- a Windows console, a container with `LC_ALL=C` -- reading the model died
    with `UnicodeDecodeError: 'ascii' codec can't decode byte 0xc3`. Run in a subprocess
    because the encoding is fixed from the environment at interpreter start, so an
    in-process check would only ever see the test runner's own UTF-8.
    """
    model = tmp_path / "model"
    model.mkdir()
    (model / "orders.yml").write_text(
        _ORDERS.replace("        primary_key: true\n",
                        "        primary_key: true\n        title: Größe\n"),
        encoding="utf-8")
    out = tmp_path / "out.yaml"

    env = {
        **os.environ,
        "LC_ALL": "C", "LANG": "C",
        # Both would otherwise put the interpreter back into UTF-8 and hide the point.
        "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from ossie_cube.cli import main; sys.exit(main(sys.argv[1:]))",
         "import", "-i", str(model), "-o", str(out)],
        capture_output=True, text=True, env=env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "Größe" in out.read_text(encoding="utf-8")
