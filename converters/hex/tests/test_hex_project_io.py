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

from ossie_hex.cli.hex_project_io import read_hex_project, write_hex_project
from ossie_hex.util.errors import ConversionError


def test_read_yaml_files_anywhere_under_the_directory(tmp_path: Path) -> None:
    """Hex leaves the layout to the author, so nesting and both suffixes count."""
    (tmp_path / "orders.yml").write_text("id: orders\n", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "customers.yaml").write_text("id: customers\n")
    (tmp_path / "notes.md").write_text("not a resource\n", encoding="utf-8")

    assert read_hex_project(tmp_path) == {
        "models/customers.yaml": "id: customers\n",
        "orders.yml": "id: orders\n",
    }


def test_read_hex_project_rejects_a_path_that_is_not_a_directory(
    tmp_path: Path,
) -> None:
    resource = tmp_path / "orders.yml"
    resource.write_text("id: orders\n", encoding="utf-8")

    with pytest.raises(ConversionError, match="is not a directory"):
        read_hex_project(resource)


def test_read_empty_project_errors(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="No Hex project files found"):
        read_hex_project(tmp_path)


def test_write_hex_project_creates_the_directories_it_needs(tmp_path: Path) -> None:
    project = tmp_path / "hex" / "project"

    write_hex_project(project, {"orders.yml": "id: orders\n"})

    assert (project / "orders.yml").read_text(encoding="utf-8") == "id: orders\n"
