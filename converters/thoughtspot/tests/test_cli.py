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

"""Tests for the ``ossie-thoughtspot`` command-line entry point.

Exercises `cli.main` directly (not a subprocess) for speed, plus one test that
resolves the declared `pyproject.toml` console-script string dynamically, so a
typo there fails here instead of surfacing only after a user installs the
package.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

from ossie_thoughtspot import _yaml, cli, ossie_to_thoughtspot, tml, tml_to_ossie
from ossie_thoughtspot.errors import ConversionError

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = PACKAGE_ROOT / "tests" / "fixtures"


def _tml_paths(fixture_name: str) -> list[Path]:
    return sorted((FIXTURES_ROOT / fixture_name).glob("*.tml"))


def _tml_argv(fixture_name: str) -> list[str]:
    return [str(p) for p in _tml_paths(fixture_name)]


def _write_ossie_yaml_from_fixture(fixture_name: str, target: Path) -> None:
    """An Apache Ossie YAML document converted from a TML fixture set -- the
    natural input `to-tml` expects, rather than a hand-authored one."""
    texts = [(str(p), p.read_text(encoding="utf-8")) for p in _tml_paths(fixture_name)]
    document_set = tml.load_document_set(texts)
    result = tml_to_ossie.convert(document_set)
    target.write_text(_yaml.dump(result.model), encoding="utf-8")


def _inject_rls_rules(src_dir: Path, dst_dir: Path, *, table_filename: str) -> None:
    """Copy a fixture directory, adding `rls_rules` to one table document.

    `test_fixtures.py`'s own check forbids `rls_rules` in the committed fixtures
    themselves -- an ERROR-severity issue (`TS-DATASET-RLS-RULES`) needs its own,
    disposable copy rather than mutating a shared fixture.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    for path in src_dir.glob("*.tml"):
        text = path.read_text(encoding="utf-8")
        if path.name == table_filename:
            text += '  rls_rules:\n    - name: rule1\n      filter: "[customer_id] = 1"\n'
        (dst_dir / path.name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# The console-script entry point
# ---------------------------------------------------------------------------


def test_console_script_entry_point_resolves():
    # Reads the declared target straight out of pyproject.toml -- not a
    # hardcoded `from ossie_thoughtspot import cli` -- so a typo in the
    # `[project.scripts]` string itself (wrong module, wrong attribute) fails
    # this test rather than only a user's `pip install`.
    text = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^ossie-thoughtspot\s*=\s*"([^"]+)"', text)
    assert match, "no ossie-thoughtspot console-script entry declared in pyproject.toml"
    module_name, _, attr_name = match.group(1).partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attr_name)
    assert callable(target)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [[], ["to-ossie"], ["to-tml"]])
def test_help_works_for_both_subcommands(argv, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main([*argv, "--help"])
    assert exc_info.value.code == 0
    out, err = capsys.readouterr()
    assert out  # argparse writes --help to stdout
    assert err == ""


# ---------------------------------------------------------------------------
# to-ossie
# ---------------------------------------------------------------------------


def test_to_ossie_writes_a_loadable_ossie_document(tmp_path):
    out_path = tmp_path / "out.ossie.yaml"
    issues_path = tmp_path / "issues.json"
    code = cli.main(
        ["to-ossie", *_tml_argv("tpcds"), "-o", str(out_path), "--issues", str(issues_path)]
    )
    assert code == 0
    document = _yaml.load(out_path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert document["semantic_model"][0]["name"] == "tpcds_retail_model"


def test_to_ossie_help_documents_the_overwrite_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["to-ossie", "--help"])
    out, _ = capsys.readouterr()
    assert "--force" in out


# ---------------------------------------------------------------------------
# to-tml
# ---------------------------------------------------------------------------


def test_to_tml_writes_expected_filenames(tmp_path):
    ossie_path = tmp_path / "input.ossie.yaml"
    _write_ossie_yaml_from_fixture("minimal", ossie_path)
    out_dir = tmp_path / "out"

    code = cli.main(["to-tml", str(ossie_path), "-o", str(out_dir)])
    assert code == 0
    assert {p.name for p in out_dir.iterdir()} == {
        "orders.table.tml",
        "customers.table.tml",
        "minimal_orders_model.model.tml",
    }


def test_to_tml_writes_tables_before_the_model(tmp_path, monkeypatch):
    ossie_path = tmp_path / "input.ossie.yaml"
    _write_ossie_yaml_from_fixture("minimal", ossie_path)
    out_dir = tmp_path / "out"

    write_order: list[str] = []
    resolved_out_dir = out_dir.resolve()
    original_write_text = Path.write_text

    def _tracking_write_text(self, *args, **kwargs):
        if self.parent == resolved_out_dir:
            write_order.append(self.name)
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _tracking_write_text)

    code = cli.main(["to-tml", str(ossie_path), "-o", str(out_dir)])
    assert code == 0
    assert write_order[-1] == "minimal_orders_model.model.tml"
    assert set(write_order[:-1]) == {"orders.table.tml", "customers.table.tml"}


# ---------------------------------------------------------------------------
# Issues: JSON, routed correctly, never mixed with document output
# ---------------------------------------------------------------------------


def test_issues_land_in_the_issues_file_as_json_and_not_in_the_document(tmp_path):
    out_path = tmp_path / "out.ossie.yaml"
    issues_path = tmp_path / "issues.json"
    code = cli.main(
        ["to-ossie", *_tml_argv("tpcds"), "-o", str(out_path), "--issues", str(issues_path)]
    )
    assert code == 0  # tpcds carries WARNING/INFO issues only, never ERROR

    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    assert isinstance(issues, list)
    assert len(issues) > 0
    assert {i["severity"] for i in issues} <= {"INFO", "WARNING", "ERROR"}

    document_text = out_path.read_text(encoding="utf-8")
    for issue in issues:
        assert issue["code"] not in document_text
        assert issue["message"] not in document_text


def test_issues_default_to_stderr_and_stdout_stays_silent(tmp_path, capsys):
    out_path = tmp_path / "out.ossie.yaml"
    code = cli.main(["to-ossie", *_tml_argv("minimal"), "-o", str(out_path)])
    assert code == 0

    out, err = capsys.readouterr()
    assert out == ""  # never interleaved with document output on stdout
    issues = json.loads(err)
    assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# Exit code: tied to has_errors(), not to the mere presence of an issue
# ---------------------------------------------------------------------------


def test_exit_code_zero_on_a_clean_conversion(tmp_path):
    # The minimal fixture's only issue is INFO-severity.
    code = cli.main(
        ["to-ossie", *_tml_argv("minimal"), "-o", str(tmp_path / "out.yaml")]
    )
    assert code == 0


def test_exit_code_zero_on_warnings_only(tmp_path):
    # The tpcds fixture carries WARNING-severity issues but no ERROR.
    code = cli.main(["to-ossie", *_tml_argv("tpcds"), "-o", str(tmp_path / "out.yaml")])
    assert code == 0


def test_exit_code_one_on_an_error_severity_issue_but_the_document_is_still_written(tmp_path):
    error_fixture = tmp_path / "error_fixture"
    _inject_rls_rules(
        FIXTURES_ROOT / "minimal", error_fixture, table_filename="orders.table.tml"
    )
    out_path = tmp_path / "out.yaml"
    issues_path = tmp_path / "issues.json"

    code = cli.main(
        [
            "to-ossie",
            *[str(p) for p in error_fixture.glob("*.tml")],
            "-o",
            str(out_path),
            "--issues",
            str(issues_path),
        ]
    )

    assert code == 1
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    assert any(i["severity"] == "ERROR" for i in issues)
    # A conversion with a declared-loss ERROR is still a *successful* conversion
    # that reported it -- the document is written regardless of exit code.
    assert out_path.exists()
    document = _yaml.load(out_path.read_text(encoding="utf-8"))
    assert document["semantic_model"][0]["name"] == "minimal_orders_model"


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------


def test_refuses_to_overwrite_an_existing_output_file_without_force(tmp_path, capsys):
    out_path = tmp_path / "out.yaml"
    out_path.write_text("pre-existing content\n", encoding="utf-8")

    code = cli.main(["to-ossie", *_tml_argv("minimal"), "-o", str(out_path)])
    assert code == 1
    assert out_path.read_text(encoding="utf-8") == "pre-existing content\n"
    _, err = capsys.readouterr()
    assert "--force" in err


def test_force_allows_overwriting_an_existing_output_file(tmp_path):
    out_path = tmp_path / "out.yaml"
    out_path.write_text("pre-existing content\n", encoding="utf-8")

    code = cli.main(["to-ossie", *_tml_argv("minimal"), "-o", str(out_path), "--force"])
    assert code == 0
    assert "pre-existing content" not in out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Two additional tests, chosen for what the filesystem-writing contract calls
# out as its two real hazards: an output path escaping the target directory,
# and a partial multi-file write when only some target files already exist.
# ---------------------------------------------------------------------------


def test_safe_target_path_refuses_to_escape_the_output_directory(tmp_path):
    # `tml.dump_document_set` already sanitises every filename it mints, so a
    # document name cannot reach `cli._safe_target_path` carrying a path
    # separator through the normal `to-tml` flow. This attacks the guard
    # directly, bypassing the dumper, because it is the last line of defence
    # this module owns and the one thing it must never trust implicitly.
    directory = tmp_path / "out"
    directory.mkdir()
    with pytest.raises(ConversionError):
        cli._safe_target_path(directory, "../../etc/passwd")

    # A resolved symlink case too (this project has already gotten a naive
    # string-prefix comparison wrong on macOS before): a candidate directory
    # that is itself reached through a symlink must still be treated as
    # legitimate, not rejected as "outside" its own resolved self.
    real_target = tmp_path / "real"
    real_target.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_target)
    resolved = cli._safe_target_path(link_dir, "orders.table.tml")
    assert resolved == (real_target / "orders.table.tml").resolve()


def test_to_tml_refuses_atomically_leaving_no_partial_output(tmp_path):
    ossie_path = tmp_path / "input.ossie.yaml"
    _write_ossie_yaml_from_fixture("minimal", ossie_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # Only one of the three eventual output files already exists.
    (out_dir / "customers.table.tml").write_text("do not touch\n", encoding="utf-8")

    code = cli.main(["to-tml", str(ossie_path), "-o", str(out_dir)])

    assert code == 1
    # The pre-existing file is untouched, and nothing else was written --
    # a conflict on one target must not let the other, non-conflicting
    # targets be written anyway.
    assert (out_dir / "customers.table.tml").read_text(encoding="utf-8") == "do not touch\n"
    assert not (out_dir / "orders.table.tml").exists()
    assert not (out_dir / "minimal_orders_model.model.tml").exists()


def test_ossie_to_thoughtspot_convert_agrees_with_the_cli_on_filenames(tmp_path):
    # Cross-check the CLI's output against calling the library directly --
    # guards against the CLI silently diverging from `dump_document_set`'s
    # own naming (e.g. by renaming files itself instead of using the names
    # the dumper already sanitised and ordered).
    ossie_path = tmp_path / "input.ossie.yaml"
    _write_ossie_yaml_from_fixture("minimal", ossie_path)
    ossie_document = _yaml.load(ossie_path.read_text(encoding="utf-8"))
    expected = {name for name, _ in tml.dump_document_set(ossie_to_thoughtspot.convert(ossie_document).documents)}

    out_dir = tmp_path / "out"
    cli.main(["to-tml", str(ossie_path), "-o", str(out_dir)])
    assert {p.name for p in out_dir.iterdir()} == expected
