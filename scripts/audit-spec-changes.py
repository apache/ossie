#!/usr/bin/env python3
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

"""Suggest unlisted spec-changing merged PRs from local Git history.

This is intentionally advisory: not every change under a spec folder is
significant, and commit messages do not reliably encode PR metadata.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys

PR_IN_LOG = re.compile(r"\(#(\d+)\)")
HISTORY_ENTRY = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|.*?\[#(\d+)\]", re.MULTILINE)


def candidate_prs(git_log: str) -> dict[str, str]:
    """Map PR number to commit subject for eligible mainline commits."""
    candidates = {}
    for subject in git_log.splitlines():
        match = PR_IN_LOG.search(subject)
        if match:
            candidates[match.group(1)] = subject
    return candidates


def recorded_prs(history: str) -> set[str]:
    return set(HISTORY_ENTRY.findall(history))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-09-01", help="Earliest merge date, YYYY-MM-DD")
    parser.add_argument("--history", type=Path, default=Path("docs/spec_changes.md"))
    parser.add_argument("--git-ref", default="HEAD", help="Git ref to inspect, default HEAD")
    args = parser.parse_args()
    try:
        history = args.history.read_text(encoding="utf-8")
        result = subprocess.run(
            ["git", "log", "--first-parent", f"--since={args.since}",
             "--format=%s", args.git_ref, "--", "core-spec", "ontology"],
            check=True, text=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.error(f"cannot read repository history: {exc}")

    listed = recorded_prs(history)
    candidates = candidate_prs(result.stdout)
    missing = sorted((number, subject) for number, subject in candidates.items() if number not in listed)
    print(f"Recorded significant PRs: {len(listed)}; candidate PRs not in history: {len(missing)}")
    for number, subject in missing:
        print(f"  https://github.com/apache/ossie/pull/{number}  {subject}")
    print("Review suggestions manually. Editorial and non-normative changes need no entry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
