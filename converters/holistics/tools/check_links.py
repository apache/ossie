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

"""Resolve every relative link and heading anchor across the documents.

The mapping is split over several files, so a link that was a same-page anchor
can quietly become a dangling cross-file reference. This resolves each one
against the files and headings that actually exist. External URLs are not
fetched; only their shape is checked.

Dev tooling, not part of the wheel. Run it with ``python3 tools/check_links.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

LINK = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")
HEADING = re.compile(r"(?m)^#{1,6}\s+(?P<title>.+?)\s*$")


def slug(title: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces hyphenated."""
    s = title.strip().lower()
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s).strip("-")


def anchors(path: Path) -> set[str]:
    return {slug(m.group("title")) for m in HEADING.finditer(path.read_text())}


def main() -> int:
    docs = [HERE / "README.md"] + sorted((HERE / "docs").glob("*.md"))
    failures: list[str] = []
    checked = 0

    for doc in docs:
        text = doc.read_text()
        for match in LINK.finditer(text):
            target = match.group("target").strip()
            where = f"{doc.relative_to(HERE).as_posix()}: [{match.group('text')}]({target})"
            checked += 1

            if target.startswith(("http://", "https://")):
                if " " in target:
                    failures.append(f"{where} -> malformed URL")
                continue
            if target.startswith("mailto:"):
                continue

            file_part, _, anchor = target.partition("#")
            resolved = doc.parent / file_part if file_part else doc
            if not resolved.exists():
                failures.append(f"{where} -> no such file {resolved.relative_to(HERE).as_posix()}")
                continue
            if anchor and anchor not in anchors(resolved):
                failures.append(
                    f"{where} -> {resolved.name} has no heading anchored '{anchor}'"
                )

    if failures:
        print(f"{len(failures)} of {checked} links do not resolve:\n", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"all {checked} links across {len(docs)} documents resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
