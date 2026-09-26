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

"""Tests for the ossie-hologres command line interface."""

import pytest
from _util import FIXTURES
from ossie_hologres.cli import main


class TestExport:
    def test_writes_ddl_to_stdout(self, capsys):
        assert main(["export", "-i", str(FIXTURES / "fixtureB_ossie.yaml")]) == 0
        out = capsys.readouterr().out
        assert out == (FIXTURES / "fixtureB_semantic_view.sql").read_text(encoding="utf-8")

    def test_writes_ddl_to_a_file(self, tmp_path):
        out = tmp_path / "view.sql"
        assert main(["export", "-i", str(FIXTURES / "fixtureA_ossie.yaml"), "-o", str(out)]) == 0
        assert out.read_text(encoding="utf-8").startswith("CREATE SEMANTIC VIEW svacc_order_sv")

    def test_drop_if_exists_flag(self, capsys):
        main(["export", "-i", str(FIXTURES / "fixtureA_ossie.yaml"), "--drop-if-exists"])
        assert capsys.readouterr().out.startswith("DROP SEMANTIC VIEW IF EXISTS")

    def test_schema_flag_qualifies_the_view(self, capsys):
        main(["export", "-i", str(FIXTURES / "fixtureA_ossie.yaml"), "--schema", "analytics"])
        assert "CREATE SEMANTIC VIEW analytics.svacc_order_sv" in capsys.readouterr().out

    def test_database_mismatch_is_reported_as_an_error(self, capsys):
        code = main(["export", "-i", str(FIXTURES / "fixtureA_ossie.yaml"), "--database", "other"])
        assert code == 1
        assert "Error:" in capsys.readouterr().err

    def test_missing_input_file_is_reported_as_an_error(self, capsys):
        assert main(["export", "-i", "does-not-exist.yaml"]) == 1
        assert "Error:" in capsys.readouterr().err


class TestMetricOwnerOption:
    def _model(self, tmp_path):
        path = tmp_path / "model.yaml"
        path.write_text(
            "version: 0.2.0.dev0\n"
            "name: sv\n"
            "datasets:\n"
            "- name: o\n"
            "  source: public.orders\n"
            "  primary_key: [id]\n"
            "metrics:\n"
            "- name: n\n"
            "  expression:\n"
            "    dialects:\n"
            "    - dialect: ANSI_SQL\n"
            "      expression: COUNT(*)\n",
            encoding="utf-8",
        )
        return path

    def test_supplies_an_owner_for_count_star(self, tmp_path, capsys):
        assert main(["export", "-i", str(self._model(tmp_path)), "--metric-owner", "n=o"]) == 0
        assert "o.n AS COUNT(*)" in capsys.readouterr().out

    def test_without_it_the_metric_cannot_be_converted(self, tmp_path, capsys):
        assert main(["export", "-i", str(self._model(tmp_path))]) == 1
        assert "--metric-owner" in capsys.readouterr().err

    def test_skip_unsupported_metrics_drops_it_instead(self, tmp_path, capsys):
        with pytest.warns(UserWarning, match="skipped"):
            code = main(["export", "-i", str(self._model(tmp_path)), "--skip-unsupported-metrics"])
        assert code == 0
        assert "METRICS" not in capsys.readouterr().out

    @pytest.mark.parametrize("bad", ["n", "=o", "n=", ""])
    def test_malformed_metric_owner_is_rejected(self, tmp_path, bad):
        with pytest.raises(SystemExit):
            main(["export", "-i", str(self._model(tmp_path)), "--metric-owner", bad])


class TestImport:
    def test_writes_ossie_to_stdout(self, capsys):
        assert main(["import", "-i", str(FIXTURES / "fixtureB_model_yaml.yaml")]) == 0
        out = capsys.readouterr().out
        assert out.startswith("version: 0.2.0.dev0")
        assert "name: svacc_sales_sv" in out

    def test_name_flag_overrides_the_model_name(self, capsys):
        main(["import", "-i", str(FIXTURES / "fixtureA_model_yaml.yaml"), "--name", "renamed"])
        assert "name: renamed" in capsys.readouterr().out

    def test_writes_ossie_to_a_file(self, tmp_path):
        out = tmp_path / "model.yaml"
        assert main(["import", "-i", str(FIXTURES / "fixtureA_model_yaml.yaml"), "-o", str(out)]) == 0
        assert "svacc_order_sv" in out.read_text(encoding="utf-8")

    def test_malformed_input_is_reported_as_an_error(self, tmp_path, capsys):
        path = tmp_path / "bad.yaml"
        path.write_text("- not a mapping\n", encoding="utf-8")
        assert main(["import", "-i", str(path)]) == 1
        assert "Error:" in capsys.readouterr().err


class TestParser:
    def test_a_subcommand_is_required(self):
        with pytest.raises(SystemExit):
            main([])

    def test_unknown_subcommand_is_rejected(self):
        with pytest.raises(SystemExit):
            main(["convert"])
