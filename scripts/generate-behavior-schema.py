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

"""Embed the canonical Behavior resource so the core schema works offline.

Use --apply after editing behavior-layer.schema.json, or --check in CI.
"""

import argparse
import json
from pathlib import Path

_CORE_SPEC = Path(__file__).resolve().parents[1] / "core-spec"


def render(core_text: str, behavior: dict) -> str:
    # Replace only the generated member, preserving the rest of the core file.
    marker = '        "behavior": '
    if core_text.count(marker) != 1:
        raise ValueError("Expected one SemanticModel behavior member")
    start = core_text.index(marker) + len(marker)
    current, length = json.JSONDecoder().raw_decode(core_text[start:])
    core = json.loads(core_text)
    if current != core["$defs"]["SemanticModel"]["properties"]["behavior"]:
        raise ValueError("Behavior member is outside SemanticModel")
    # Keep $id: local fragment references belong to the embedded resource.
    embedded = json.dumps(behavior, indent=2, ensure_ascii=False).replace("\n", "\n        ")
    return core_text[:start] + embedded + core_text[start + length:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    core_path = _CORE_SPEC / "ossie-schema.json"
    current = core_path.read_text(encoding="utf-8")
    behavior = json.loads((_CORE_SPEC / "behavior-layer.schema.json").read_text(encoding="utf-8"))
    expected = render(current, behavior)
    if args.apply:
        core_path.write_text(expected, encoding="utf-8")
        return 0
    if current != expected:
        print("Behavior schema is out of sync; run python3 scripts/generate-behavior-schema.py --apply")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
