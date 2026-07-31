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

from ossie_hex.ossie_to_hex.load_ossie_document import load_ossie_document
from ossie_hex.util.errors import ConversionError


def test_a_normal_document() -> None:
    document, warnings = load_ossie_document(
        """
version: "0.2.0.dev0"
semantic_model:
  - name: orders
    datasets:
      - name: orders
        source: analytics.orders
"""
    )

    assert [model.name for model in document.semantic_model] == ["orders"]
    assert warnings == []


def test_a_malformed_document() -> None:
    """Callers catch ConversionError, so neither validator may surface raw."""
    with pytest.raises(ConversionError, match="Invalid Ossie document"):
        load_ossie_document("version: 0.2.0.dev0\nname: not-a-core-document\n")


def test_a_non_mapping_document() -> None:
    with pytest.raises(ConversionError, match="must be a mapping"):
        load_ossie_document("- orders\n")
