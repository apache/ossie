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

TPCDS = Path(__file__).resolve().parents[4] / "examples" / "tpcds_semantic_model.yaml"


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


def test_missing_dialect(tmp_path: Path) -> None:
    """Should not error"""
    input_file = TPCDS
    i = str(input_file)
    output_dir = tmp_path / "hex_out"
    o = str(output_dir)

    code = main(["export", "-i", i, "-o", o])

    assert code == 0


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
Wrote 0 hex semantic project(s) to OUTPUT
Encountered 1 problem(s):
[FATAL] File does not exist: `INPUT`
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
usage: ossie-hex export [-h] -i INPUT [-o OUTPUT] [-d DIALECT]
ossie-hex export: error: argument -d/--dialect: invalid choice: 'invalid' (choose from 'ansi_sql', 'snowflake', 'mdx', 'maql', 'tableau', 'databricks', 'bigquery', 'thoughtspot')
""")
