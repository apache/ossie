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

"""The `ossie-solid` command line interface."""

import pytest
import yaml
from conftest import FIXTURES, fixture, normalized_yaml

from ossie_solid.cli import main


def test_import_writes_an_ossie_model_to_a_file(tmp_path):
    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(FIXTURES / "tpcds_solid.yaml"), "-o", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["version"] == "0.2.0.dev0"


def test_import_writes_to_stdout_without_an_output_path(capsys):
    assert main(["import", "-i", str(FIXTURES / "databricks_solid.yaml")]) == 0
    assert yaml.safe_load(capsys.readouterr().out)["semantic_model"][0]["name"] == (
        "orders_analytics"
    )


def test_export_writes_a_solid_model_to_a_file(tmp_path):
    out = tmp_path / "solid.yaml"
    assert main(["export", "-i", str(FIXTURES / "tpcds_ossie.yaml"), "-o", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["semantic_model"]["name"] == (
        "tpcds_retail_model"
    )


def test_a_round_trip_through_the_cli_matches_the_input(tmp_path, capsys):
    ossie = tmp_path / "model.yaml"
    solid = tmp_path / "solid.yaml"
    main(["import", "-i", str(FIXTURES / "databricks_solid.yaml"), "-o", str(ossie)])
    main(["export", "-i", str(ossie), "-o", str(solid)])
    assert normalized_yaml(solid.read_text()) == normalized_yaml(
        fixture("databricks_solid.yaml")
    )


def test_warnings_go_to_stderr_as_plain_lines(capsys):
    main(["import", "-i", str(FIXTURES / "tpcds_solid.yaml")])
    err = capsys.readouterr().err
    assert err.startswith("Warning: [metric]")
    assert "Traceback" not in err


def test_a_conversion_error_exits_non_zero_with_a_message(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("semantic_model: [unclosed\n")
    assert main(["import", "-i", str(bad)]) == 1
    assert capsys.readouterr().err.startswith("Error: Invalid YAML")


def test_a_missing_input_file_exits_non_zero_with_a_message(capsys):
    assert main(["import", "-i", "/nonexistent/model.yaml"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_the_dialect_flag_is_honoured(tmp_path):
    out = tmp_path / "model.yaml"
    main([
        "import",
        "-i", str(FIXTURES / "databricks_solid.yaml"),
        "-o", str(out),
        "--dialect", "ANSI_SQL",
    ])
    model = yaml.safe_load(out.read_text())["semantic_model"][0]
    assert model["datasets"][0]["fields"][0]["expression"]["dialects"][0]["dialect"] == (
        "ANSI_SQL"
    )


def test_an_unknown_dialect_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        main(["import", "-i", str(FIXTURES / "tpcds_solid.yaml"), "--dialect", "ORACLE"])


def test_the_name_flag_renames_the_model(tmp_path):
    out = tmp_path / "model.yaml"
    main([
        "import",
        "-i", str(FIXTURES / "tpcds_solid.yaml"),
        "-o", str(out),
        "--name", "renamed_model",
    ])
    assert yaml.safe_load(out.read_text())["semantic_model"][0]["name"] == (
        "renamed_model"
    )


def test_a_missing_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        main([])
