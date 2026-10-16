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

"""Tests for the ossie-bigquery CLI: read the input file, write the LookML
output, and report a conversion failure on stderr with a non-zero exit status
rather than a traceback."""

import pytest
import yaml

from ossie_bigquery import cli

OSSIE_MODEL = {
    "version": "0.2.0.dev0",
    "name": "retail",
    "datasets": [
        {
            "name": "orders",
            "source": "proj.sales.orders",
            "primary_key": ["order_id"],
            "fields": [
                {
                    "name": "order_id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                    },
                    "datatype": "Integer",
                }
            ],
        }
    ],
}


@pytest.fixture
def ossie_file(tmp_path):
    path = tmp_path / "ossie.yaml"
    path.write_text(yaml.dump(OSSIE_MODEL))
    return path


class TestConversion:
    def test_writes_a_lookml_document(self, ossie_file, tmp_path):
        out = tmp_path / "out.lkml"
        cli.main(["-i", str(ossie_file), "-o", str(out)])
        text = out.read_text()
        assert "view: orders {" in text
        assert "explore: orders {}" in text

    def test_reports_what_it_wrote(self, ossie_file, tmp_path, capsys):
        out = tmp_path / "out.lkml"
        cli.main(["-i", str(ossie_file), "-o", str(out)])
        assert f"Converted {ossie_file} -> {out}" in capsys.readouterr().out

    def test_long_option_names_work(self, ossie_file, tmp_path):
        out = tmp_path / "out.lkml"
        cli.main(["--input", str(ossie_file), "--output", str(out)])
        assert out.exists()


class TestErrors:
    def _bad_relationship_file(self, tmp_path):
        model = dict(OSSIE_MODEL)
        model["relationships"] = [
            {"name": "broken", "from": "orders", "to": "orders",
             "from_columns": None, "to_columns": ["order_id"]}
        ]
        bad = tmp_path / "bad.yaml"
        bad.write_text(yaml.dump(model))
        return bad

    def test_a_conversion_error_exits_non_zero_without_a_traceback(self, tmp_path, capsys):
        bad = self._bad_relationship_file(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["-i", str(bad), "-o", str(tmp_path / "out.lkml")])
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: Relationship 'broken'")
        assert "Traceback" not in err

    def test_a_failed_conversion_writes_no_output_file(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(yaml.dump({"version": "0.2.0.dev0"}))
        out = tmp_path / "out.lkml"
        with pytest.raises(SystemExit):
            cli.main(["-i", str(bad), "-o", str(out)])
        assert not out.exists()

    def test_a_missing_input_file_is_reported_not_raised(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["-i", str(tmp_path / "nope.yaml"), "-o", str(tmp_path / "out.lkml")])
        assert excinfo.value.code == 1
        assert capsys.readouterr().err.startswith("Error: ")

    def test_invalid_yaml_is_reported_not_raised(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: [unclosed\nversion: x")
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["-i", str(bad), "-o", str(tmp_path / "out.lkml")])
        assert excinfo.value.code == 1
        assert "Error: Invalid Ossie YAML" in capsys.readouterr().err
