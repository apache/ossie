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

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_declares_both_directions():
    text = README.read_text(encoding="utf-8")
    assert "ThoughtSpot TML -> Ossie" in text or "ThoughtSpot TML → Ossie" in text
    assert "Ossie -> ThoughtSpot TML" in text or "Ossie → ThoughtSpot TML" in text


def test_readme_carries_a_coverage_matrix_with_rows():
    # P21: a matrix, not a prose limitations list.
    text = README.read_text(encoding="utf-8")
    assert "## Coverage matrix" in text
    body = text.split("## Coverage matrix", 1)[1]
    rows = re.findall(r"^\| *L\d+ *\|", body, flags=re.MULTILINE)
    assert len(rows) >= 1, "coverage matrix has no L-numbered limitation rows"


def test_readme_states_the_dialect_caveat():
    # The THOUGHTSPOT dialect is not registered until apache/ossie#351 merges.
    assert "351" in README.read_text(encoding="utf-8")
