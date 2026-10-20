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
    # The coverage information must appear as a matrix, not a prose
    # limitations list.
    text = README.read_text(encoding="utf-8")
    assert "## Coverage matrix" in text
    body = text.split("## Coverage matrix", 1)[1]
    rows = re.findall(r"^\| *L\d+ *\|", body, flags=re.MULTILINE)
    assert len(rows) >= 1, "coverage matrix has no L-numbered limitation rows"


def test_readme_states_the_dialect_registration():
    # The THOUGHTSPOT dialect was registered by apache/ossie#351 (merged 2026-09-01).
    assert "351" in README.read_text(encoding="utf-8")


def test_readme_describes_the_document_root_not_the_removed_wrapper():
    # apache/ossie#383 moved the semantic model's fields to the document root.
    # The mapping table is the README's statement of the input contract, so a
    # stale row here tells a reader to build documents this converter now
    # rejects. Pinned because nothing else reads that row: reverting it left
    # the whole suite green.
    text = README.read_text(encoding="utf-8")
    mapping_rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "document root" in ln]
    assert mapping_rows, "README's mapping table no longer states the document-root contract"
    for line in text.splitlines():
        if "`semantic_model`" in line:
            assert "removed" in line or "reject" in line, (
                f"README still presents `semantic_model` as current: {line!r}"
            )


def test_readme_construct_counts_match_the_catalog():
    """The README's four construct counts, checked against the catalog itself.

    The README states them as bare numbers in prose -- "all 146 constructs ...
    108 with a native equivalent, 37 as a `sql_*_op` pass-through ... and 1
    (`EXISTS_IN()`)". Nothing derived them, so adding or reclassifying a row
    left the sentence quietly wrong, and it is the first quantitative claim a
    reader meets. Each count is asserted to be PRESENT before it is compared,
    so a reworded sentence fails here rather than passing vacuously.
    """
    from collections import Counter

    from ossie_thoughtspot.expressions import CATALOG
    from ossie_thoughtspot.expressions._types import Classification

    text = README.read_text(encoding="utf-8")
    by_class = Counter(c.classification for c in CATALOG.values())
    expected = {
        r"all (\d+) constructs": len(CATALOG),
        r"(\d+) with a native equivalent": by_class[Classification.DIRECT],
        r"(\d+) as a `sql_\*_op` pass-through": by_class[Classification.PASSTHROUGH],
        r"and (\d+) \(`EXISTS_IN\(\)`\)": by_class[Classification.UNMAPPABLE],
    }
    for pattern, count in expected.items():
        match = re.search(pattern, text)
        assert match is not None, (
            f"README no longer states the count matching {pattern!r}; it was "
            f"rewritten without updating this guard, so the counts are now "
            f"unchecked rather than wrong"
        )
        assert int(match.group(1)) == count, (
            f"README says {match.group(1)} for {pattern!r}, catalog has {count}"
        )
