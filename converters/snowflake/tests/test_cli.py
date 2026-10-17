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

"""Tests for the ossie-snowflake CLI."""

import warnings

import pytest
import yaml

from ossie_snowflake import cli

SNOWFLAKE_MODEL = {
    "name": "retail",
    "tables": [
        {
            "name": "orders",
            "base_table": {"database": "DB", "schema": "SCH", "table": "FCT_ORDER"},
            "dimensions": [
                {"name": "order_id", "expr": "ORDER_ID", "data_type": "VARCHAR"}
            ],
            "facts": [{"name": "amount", "expr": "AMOUNT", "data_type": "NUMBER"}],
        }
    ],
}


@pytest.fixture
def snowflake_file(tmp_path):
    path = tmp_path / "snowflake.yaml"
    path.write_text(yaml.dump(SNOWFLAKE_MODEL))
    return path


@pytest.fixture
def ossie_file(tmp_path, snowflake_file):
    path = tmp_path / "ossie.yaml"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cli.main(["snowflake-to-ossie", "-i", str(snowflake_file), "-o", str(path)])
    return path


class TestSubcommands:
    def test_snowflake_to_ossie_writes_an_ossie_document(self, snowflake_file, tmp_path):
        out = tmp_path / "out.yaml"
        cli.main(["snowflake-to-ossie", "-i", str(snowflake_file), "-o", str(out)])
        document = yaml.safe_load(out.read_text())
        assert document["version"] == "0.2.0.dev0"
        assert [d["name"] for d in document["datasets"]] == ["orders"]

    def test_ossie_to_snowflake_writes_a_cortex_analyst_document(self, ossie_file, tmp_path):
        out = tmp_path / "out.yaml"
        cli.main(["ossie-to-snowflake", "-i", str(ossie_file), "-o", str(out)])
        document = yaml.safe_load(out.read_text())
        assert [t["name"] for t in document["tables"]] == ["orders"]

    def test_both_directions_report_what_they_wrote(self, snowflake_file, tmp_path, capsys):
        out = tmp_path / "out.yaml"
        cli.main(["snowflake-to-ossie", "-i", str(snowflake_file), "-o", str(out)])
        assert f"Converted {snowflake_file} -> {out}" in capsys.readouterr().out


class TestBackwardCompatibility:
    def test_a_bare_input_output_pair_still_exports_to_snowflake(self, ossie_file, tmp_path):
        # This spelling predates the subcommands and is what the published
        # `ossie-snowflake` entry point accepted.
        out = tmp_path / "legacy.yaml"
        cli.main(["-i", str(ossie_file), "-o", str(out)])
        assert [t["name"] for t in yaml.safe_load(out.read_text())["tables"]] == ["orders"]

    def test_the_bare_form_matches_the_named_subcommand(self, ossie_file, tmp_path):
        legacy = tmp_path / "legacy.yaml"
        named = tmp_path / "named.yaml"
        cli.main(["-i", str(ossie_file), "-o", str(legacy)])
        cli.main(["ossie-to-snowflake", "-i", str(ossie_file), "-o", str(named)])
        assert legacy.read_text() == named.read_text()

    def test_long_option_names_work_in_the_bare_form(self, ossie_file, tmp_path):
        out = tmp_path / "legacy.yaml"
        cli.main(["--input", str(ossie_file), "--output", str(out)])
        assert out.exists()


class TestErrors:
    def test_no_arguments_asks_for_a_subcommand(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([])
        assert excinfo.value.code == 2
        assert "required: command" in capsys.readouterr().err

    @pytest.mark.parametrize("argv", [["-h"], ["--help"]])
    def test_help_lists_both_directions(self, argv, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "snowflake-to-ossie" in out
        assert "ossie-to-snowflake" in out

    def test_a_conversion_error_exits_non_zero_without_a_traceback(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("- not a mapping\n")
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["snowflake-to-ossie", "-i", str(bad), "-o", str(tmp_path / "o.yaml")])
        assert excinfo.value.code == 1
        assert "Error: " in capsys.readouterr().err

    def test_a_failed_conversion_writes_no_output_file(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("- not a mapping\n")
        out = tmp_path / "o.yaml"
        with pytest.raises(SystemExit):
            cli.main(["snowflake-to-ossie", "-i", str(bad), "-o", str(out)])
        assert not out.exists()

    def test_a_missing_input_file_is_reported_like_any_other_error(
        self, tmp_path, capsys
    ):
        # A typo in a path is the caller's to fix, so it reads as an error message
        # rather than as a converter crash.
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "snowflake-to-ossie",
                "-i", str(tmp_path / "absent.yaml"),
                "-o", str(tmp_path / "out.yaml"),
            ])
        assert exc.value.code == 1
        assert capsys.readouterr().err.startswith("Error: ")

    def test_an_unwritable_output_path_is_reported(
        self, snowflake_file, tmp_path, capsys
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "snowflake-to-ossie",
                "-i", str(snowflake_file),
                "-o", str(tmp_path / "absent-dir" / "out.yaml"),
            ])
        assert exc.value.code == 1
        assert capsys.readouterr().err.startswith("Error: ")

    def test_an_input_file_that_is_not_text_is_reported(self, tmp_path, capsys):
        path = tmp_path / "binary.yaml"
        path.write_bytes(b"\xf3\x28\x00\xff")
        with pytest.raises(SystemExit) as exc:
            cli.main([
                "snowflake-to-ossie", "-i", str(path), "-o", str(tmp_path / "out.yaml"),
            ])
        assert exc.value.code == 1
        assert capsys.readouterr().err.startswith("Error: ")
