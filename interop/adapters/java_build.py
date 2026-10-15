#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build a Java converter in an isolated interop workspace."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUPPORTED = {"polaris", "salesforce"}


def _copy_module(module: str, output: Path) -> Path:
    sandbox = output / "repo"
    module_source = REPO / "converters" / module
    module_target = sandbox / "converters" / module
    shutil.copytree(
        module_source,
        module_target,
        ignore=shutil.ignore_patterns("target", ".idea", "*.iml"),
    )
    if module == "salesforce":
        shutil.copytree(REPO / "core-spec", sandbox / "core-spec")
    return module_target


def build(module: str, output: Path) -> None:
    if module not in SUPPORTED:
        raise ValueError(f"unsupported Java converter: {module}")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    module_root = _copy_module(module, output)

    command = ["mvn", "-q", "-DskipTests", "package"]
    if module == "polaris":
        command.extend(
            [
                "dependency:build-classpath",
                "-Dmdep.outputFile=target/interop-classpath.txt",
            ]
        )
    process = subprocess.run(
        command,
        cwd=module_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=__import__("sys").stderr)
    if process.returncode != 0:
        raise SystemExit(process.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True, choices=sorted(SUPPORTED))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.module, args.output)


if __name__ == "__main__":
    main()
