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
from inline_snapshot import snapshot

from ossie_hex.cli import main
from ossie_hex.cli.report import format_export_report
from ossie_hex.util.problem import Problem

TPCDS = Path(__file__).resolve().parents[4] / "examples" / "tpcds_semantic_model.yaml"


def test_error_report_is_failed() -> None:
    problem = Problem(severity="error", message="A definition was omitted.")

    report = format_export_report(
        input="INPUT",
        output="OUTPUT",
        projects=[],
        problems=[problem],
        verbosity=0,
    )

    assert report == snapshot("""\
Failed.
Could not convert INPUT.
Encountered 1 problem: 1 error.
  (Run with -v to see a grouped summary.)""")


def test_tpcds(tmp_path: Path) -> None:
    input_file = TPCDS
    i = str(input_file)
    output_dir = tmp_path / "hex"
    o = str(output_dir)
    dialect = "snowflake"

    code = main(["export", "-i", i, "-o", o, "--dialect", dialect])

    assert code == 0
    output_paths = sorted(
        str(p.relative_to(tmp_path)) for p in output_dir.rglob("*.yml")
    )
    assert output_paths == snapshot(
        [
            "hex/tpcds_retail_model/customer.yml",
            "hex/tpcds_retail_model/date_dim.yml",
            "hex/tpcds_retail_model/item.yml",
            "hex/tpcds_retail_model/store.yml",
            "hex/tpcds_retail_model/store_sales.yml",
        ]
    )


def test_missing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Should write to the current working directory"""
    monkeypatch.chdir(tmp_path)

    code = main(["export", "-i", str(TPCDS)])

    assert code == 0
    output_paths = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.yml"))
    assert output_paths == snapshot(
        [
            "tpcds_retail_model/customer.yml",
            "tpcds_retail_model/date_dim.yml",
            "tpcds_retail_model/item.yml",
            "tpcds_retail_model/store.yml",
            "tpcds_retail_model/store_sales.yml",
        ]
    )


def test_default_dialect(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Should use ANSI SQL without reporting a fallback."""
    input_file = TPCDS
    i = str(input_file)
    output_dir = tmp_path / "hex_out"
    o = str(output_dir)

    code = main(["export", "-i", i, "-o", o])

    assert code == 0
    message = (
        capsys.readouterr()
        .err.replace(str(output_dir), "OUTPUT")
        .replace(str(input_file), "INPUT")
    )
    assert message == snapshot("""\
Success!
Converted INPUT -> OUTPUT/tpcds_retail_model/ (1 project, 5 files).
Encountered 49 problems: 49 warnings.
  (Run with -v to see a grouped summary.)
""")


def test_verbose_problem_summary(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output_dir = tmp_path / "hex_out"

    code = main(["export", "-v", "-i", str(TPCDS), "-o", str(output_dir)])

    assert code == 0
    message = (
        capsys.readouterr()
        .err.replace(str(output_dir), "OUTPUT")
        .replace(str(TPCDS), "INPUT")
    )
    assert message == snapshot("""\
Success!
Converted INPUT -> OUTPUT/tpcds_retail_model/ (1 project, 5 files).
Encountered 49 problems: 49 warnings.

Warnings (49)
  40× `ai_context` — AI context is not preserved in Hex and was dropped.
   3× `Field.is_time` — Temporal role markers are not supported in Hex and were dropped.
   2× `Field.datatype` — A datatype is required in Hex; a default was used.
   1× `Dataset.primary_key` — Composite primary keys are not supported in Hex and were dropped.
   1× `Dataset.unique_keys` — Composite unique keys are not supported in Hex and were dropped.
   1× `SemanticModel.description` — Project descriptions are not supported in Hex and were dropped.
   1× `SemanticModel.custom_extensions` — Custom extensions are not preserved in Hex and were dropped.
""")


def test_double_verbose_includes_phase_and_cause(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output_dir = tmp_path / "hex_out"

    code = main(["export", "-vv", "-i", str(TPCDS), "-o", str(output_dir)])

    assert code == 0
    message = capsys.readouterr().err
    assert "[load] `Field.datatype`" in message
    assert (
        "Cause: semantic_model > tpcds_retail_model > datasets > date_dim > "
        "fields > d_quarter_name > datatype"
    ) in message
    assert "[convert] `ai_context`" in message


def test_missing_input_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.yml"
    output_dir = tmp_path / "hex_out"

    code = main(["export", "-i", str(missing), "-o", str(output_dir)])

    assert code == 1
    message = (
        capsys.readouterr()
        .err.replace(str(output_dir.resolve()), "OUTPUT")
        .replace(str(missing.resolve()), "INPUT")
    )
    assert message == snapshot("""\
Failure!
Could not convert INPUT.
Encountered 1 problem: 1 fatal.

Fatal errors (1)
  [load] File does not exist: `INPUT`
""")


def test_invalid_dialect(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Should error"""
    input_file = tmp_path / "model.yaml"
    i = str(input_file)
    output_dir = tmp_path / "hex_out"
    o = str(output_dir)
    dialect = "invalid"

    with pytest.raises(SystemExit) as exc:
        main(["export", "-i", i, "-o", o, "--dialect", dialect])

    assert exc.value.code == 2
    message = capsys.readouterr().err
    assert message == snapshot("""\
usage: ossie-hex export [-h] -i INPUT [-o OUTPUT] [-d DIALECT] [-v]
ossie-hex export: error: argument -d/--dialect: invalid choice: 'invalid' (choose from 'ansi_sql', 'snowflake', 'mdx', 'maql', 'tableau', 'databricks', 'bigquery', 'thoughtspot')
""")
