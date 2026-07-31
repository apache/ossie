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

from ossie_hex.hex_to_ossie.load_hex_project import load_hex_project
from ossie_hex.hex_types import HexDialect
from ossie_hex.util.errors import ConversionError


def test_load_multi_document_yaml(named_joins_hex_path: str) -> None:
    project = load_hex_project(
        named_joins_hex_path, dialect=HexDialect.DUCKDB.value, name="chat"
    )

    assert {resource.id for resource in project.resources} == {"messages", "users"}


def test_load_hex_project_rejects_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("id: orders\nbase_sql_table: a.orders\n")
    (tmp_path / "b.yml").write_text("id: orders\nbase_sql_table: b.orders\n")

    with pytest.raises(ConversionError, match="Duplicate Hex resource id 'orders'"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)


def test_load_hex_project_rejects_non_mapping_document(tmp_path: Path) -> None:
    (tmp_path / "invalid.yml").write_text("- orders\n")

    with pytest.raises(ConversionError, match="expected a mapping"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)


def test_load_empty_project_errors(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="No Hex YAML"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)
