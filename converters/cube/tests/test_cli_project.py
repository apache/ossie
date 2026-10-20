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

"""`ossie-cube import --project`: the command-line face of view projection."""

import pytest
from _util import by_name, model_of
from test_view_projection import _FANOUT, _MODEL

from ossie_cube.cli import main


def _metrics(out):
    return set(by_name(model_of(out).get("metrics")))


def test_cli_projects_a_view(tmp_path, capsys):
    (tmp_path / "model.yml").write_text(_MODEL)
    assert main(["import", "-i", str(tmp_path), "--view", "sales", "--project"]) == 0
    captured = capsys.readouterr()
    assert _metrics(captured.out) == {"average_value"}
    assert "rooted at dataset 'orders'" in captured.err


def test_cli_projection_is_strict_about_fanout_unless_told_otherwise(tmp_path, capsys):
    (tmp_path / "model.yml").write_text(_FANOUT)
    assert main(["import", "-i", str(tmp_path), "--view", "sales", "--project"]) == 1
    assert "FANOUT_UNSAFE_METRIC" in capsys.readouterr().err
    assert main(["import", "-i", str(tmp_path), "--view", "sales", "--project",
                 "--no-strict-fanout"]) == 0
    assert "FANOUT_UNSAFE_METRIC" in capsys.readouterr().err


def test_cli_projection_needs_a_view_and_takes_no_name(tmp_path, capsys):
    (tmp_path / "model.yml").write_text(_MODEL)
    with pytest.raises(SystemExit):
        main(["import", "-i", str(tmp_path), "--project"])
    with pytest.raises(SystemExit):
        main(["import", "-i", str(tmp_path), "--view", "sales", "--project",
              "--name", "x"])
    with pytest.raises(SystemExit):
        main(["import", "-i", str(tmp_path), "--source", "orders"])


