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

import yaml
from ossie import OSIDocument

from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from ossie_hex.hex_types import HexDialect


def test_independently_unique_dimensions_stay_separate_keys(tmp_path: Path) -> None:
    """Hex marks each dimension unique alone, so they are not a composite key."""
    (tmp_path / "users.yml").write_text(
        """
id: users
base_sql_table: s.users
dimensions:
- id: user_id
  type: string
  unique: true
- id: email
  type: string
  unique: true
- id: username
  type: string
  unique: true
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    dataset = OSIDocument.model_validate(yaml.safe_load(yaml_text)).semantic_model[0]

    assert dataset.datasets[0].primary_key == ["user_id"]
    assert dataset.datasets[0].unique_keys == [["email"], ["username"]]
