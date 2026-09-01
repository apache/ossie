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

"""Packaging invariants that upstream's PR checklist gates."""
from pathlib import Path

import ossie_thoughtspot

ROOT = Path(__file__).resolve().parents[1]
LICENSE_MARKER = "Licensed to the Apache Software Foundation (ASF)"


def test_package_exposes_a_version():
    assert ossie_thoughtspot.__version__ == "0.1.0"


def test_every_source_file_carries_the_asf_header():
    sources = [
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
    ]
    assert sources, "expected at least one source file"
    missing = [
        str(p.relative_to(ROOT))
        for p in sources
        if LICENSE_MARKER not in p.read_text(encoding="utf-8")
    ]
    assert missing == [], f"ASF header missing from: {missing}"


def test_non_python_packaging_files_carry_the_asf_header():
    # M4: the glob above only covers src/**/*.py and tests/**/*.py, so
    # pyproject.toml, .gitignore, README.md, and the CI workflow were
    # ungated. Each uses a different comment syntax ('#', HTML comment,
    # YAML '#'), so this checks for the licence text itself, not an exact
    # comment-prefixed line.
    repo_root = ROOT.parent.parent
    files = {
        "pyproject.toml": ROOT / "pyproject.toml",
        ".gitignore": ROOT / ".gitignore",
        "README.md": ROOT / "README.md",
        "CI workflow": repo_root / ".github" / "workflows" / "converter-thoughtspot-ci.yml",
    }
    missing = [
        label for label, path in files.items()
        if not path.is_file() or LICENSE_MARKER not in path.read_text(encoding="utf-8")
    ]
    assert missing == [], f"ASF header missing from: {missing}"
