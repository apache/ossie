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
"""Rewrite every committed snapshot under `tests/fixtures/`.

Run after a deliberate change to the converter, then read the diff. The same
`tests/snapshots.py` definitions drive `tests/test_snapshots.py`, so what this
writes is exactly what that compares.

    python3 tools/update_snapshots.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from snapshots import FIXTURES, all_snapshots  # noqa: E402


def main() -> int:
    written = 0
    for relative, text in sorted(all_snapshots().items()):
        path = FIXTURES / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
        written += 1
    print(f"{written} snapshot(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
