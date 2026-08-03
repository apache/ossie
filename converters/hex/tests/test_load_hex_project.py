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

import pytest

from ossie_hex.hex_to_ossie.load_hex_project import load_hex_project
from ossie_hex.util.errors import ConversionError


def test_load_multi_document_yaml() -> None:
    file = """id: messages
base_sql_table: s.messages
---
id: users
base_sql_table: s.users
"""
    project = load_hex_project(
        {"chat.yml": file},
        project_name="chat",
    )

    assert project.name == "chat"
    assert {resource.id for resource in project.resources} == {"messages", "users"}


def test_load_hex_project_rejects_duplicate_ids() -> None:
    files = {
        "a.yml": "id: orders\nbase_sql_table: a.orders\n",
        "b.yml": "id: orders\nbase_sql_table: b.orders\n",
    }

    with pytest.raises(ConversionError, match="Duplicate Hex resource id 'orders'"):
        load_hex_project(files, project_name="demo")


def test_load_hex_project_rejects_non_mapping_document() -> None:
    with pytest.raises(ConversionError, match="expected a mapping"):
        load_hex_project({"invalid.yml": "- orders\n"}, project_name="demo")
