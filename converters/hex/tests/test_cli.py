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
import yaml

from ossie_hex.cli import main


def test_cli_import_export(minimal_hex_path: str, tmp_path: Path) -> None:
    out_yaml = tmp_path / "model.yaml"
    code = main(
        [
            "import",
            "-i",
            minimal_hex_path,
            "-o",
            str(out_yaml),
            "--dialect",
            "snowflake",
            "--name",
            "cli_demo",
        ]
    )
    assert code == 0
    assert out_yaml.exists()
    doc = yaml.safe_load(out_yaml.read_text())
    assert doc["semantic_model"][0]["name"] == "cli_demo"

    out_dir = tmp_path / "hex_out"
    code = main(
        [
            "export",
            "-i",
            str(out_yaml),
            "-o",
            str(out_dir),
            "--dialect",
            "snowflake",
        ]
    )
    assert code == 0
    assert list(out_dir.rglob("*.yml"))


def test_cli_missing_dialect_on_import(
    minimal_hex_path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["import", "-i", minimal_hex_path])
    assert exc.value.code == 2
    message = capsys.readouterr().err
    print(message)
    assert "choose from bigquery, databricks, duckdb, snowflake, spark" in message


def test_cli_export_invalid_dialect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "export",
                "-i",
                "model.yaml",
                "-o",
                "hex_project",
                "--dialect",
                "invalid",
            ]
        )

    assert exc.value.code == 2
    message = capsys.readouterr().err
    print(message)
    assert (
        "error: argument -d/--dialect: invalid choice: 'INVALID' (choose from ANSI_SQL, SNOWFLAKE, MDX, MAQL, TABLEAU, DATABRICKS, BIGQUERY)"
        in message
    )


def test_cli_import_invalid_dialect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["import", "-i", "hex_project", "--dialect", "invalid"])

    assert exc.value.code == 2
    message = capsys.readouterr().err
    assert (
        "error: argument -d/--dialect: invalid choice: 'invalid' (choose from bigquery, databricks, duckdb, snowflake, spark)"
        in message
    )
