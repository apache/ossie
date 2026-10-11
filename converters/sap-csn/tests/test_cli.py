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

import json

import pytest
import yaml

from ossie_sap_csn.cli import main

from .helpers import FIXTURES_DIR, TPCDS_PATH


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["ossie-sap-csn", *argv])
    main()


def test_csn_to_ossie_writes_yaml(tmp_path, monkeypatch, capsys):
    out = tmp_path / "model.yaml"
    run(monkeypatch, "csn-to-ossie", "-i", str(FIXTURES_DIR / "bookshop.csn.json"), "-o", str(out), "--name", "shop")
    doc = yaml.safe_load(out.read_text())
    assert doc["name"] == "shop"
    assert doc["version"] == "0.2.0.dev0"
    assert "[WARNING] RELATIONSHIP_DROPPED: Authors.books" in capsys.readouterr().err


def test_ossie_to_csn_writes_json(tmp_path, monkeypatch, capsys):
    out = tmp_path / "model.csn.json"
    run(monkeypatch, "ossie-to-csn", "-i", str(TPCDS_PATH), "-o", str(out))
    csn = json.loads(out.read_text())
    assert "store_sales" in csn["definitions"]
    assert "[WARNING] METRIC_NOT_MODELED: cumulative_sales" in capsys.readouterr().err


def test_a_file_that_is_not_json_exits_with_a_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "csn-to-ossie", "-i", str(bad), "-o", str(tmp_path / "o.yaml"))
    assert exit_info.value.code == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_an_invalid_ossie_document_exits_with_a_message(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, "ossie-to-csn", "-i", str(bad), "-o", str(tmp_path / "o.json"))
    assert exit_info.value.code == 1
    assert "not a valid Ossie document" in capsys.readouterr().err
