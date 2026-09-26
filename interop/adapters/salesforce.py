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

"""Interop shim around the Salesforce converter's shaded executable JAR."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _jar(build_root: Path) -> Path:
    jars = sorted(
        path
        for path in (build_root / "target").glob("ossie-salesforce-converter-*.jar")
        if not path.name.startswith("original-")
    )
    if len(jars) != 1:
        raise RuntimeError(f"expected one Salesforce executable JAR, found {len(jars)}")
    return jars[0]


def _run(direction: str, source: Path, staging: Path, build_root: Path) -> list[Path]:
    staged_input = staging / ("input" + source.suffix)
    shutil.copy2(source, staged_input)
    process = subprocess.run(
        ["java", "-jar", str(_jar(build_root)), direction, str(staged_input)],
        cwd=staging,
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
    return sorted(path for path in staging.iterdir() if path.is_file() and path != staged_input)


def export_model(source: Path, output: Path, build_root: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ossie-salesforce-export-") as temp_dir:
        generated = _run("toSF", source, Path(temp_dir), build_root)
        json_files = [path for path in generated if path.suffix.lower() == ".json"]
        if not json_files:
            raise RuntimeError("Salesforce export produced no JSON models")
        for path in json_files:
            shutil.copy2(path, output / path.name)


def import_model(source: Path, output: Path, build_root: Path) -> None:
    sources = [source] if source.is_file() else sorted(source.rglob("*.json"))
    if not sources:
        raise RuntimeError("Salesforce import input contains no JSON models")

    version: str | None = None
    semantic_models: list[object] = []
    for native in sources:
        with tempfile.TemporaryDirectory(prefix="ossie-salesforce-import-") as temp_dir:
            generated = _run("toOssie", native, Path(temp_dir), build_root)
            yaml_files = [path for path in generated if path.suffix.lower() in {".yaml", ".yml"}]
            if not yaml_files:
                raise RuntimeError(f"Salesforce import produced no Ossie YAML for {native.name}")
            for path in yaml_files:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(document, dict):
                    raise TypeError(f"Salesforce import produced invalid YAML: {path.name}")
                current_version = document.get("version")
                if version is None:
                    version = current_version
                elif current_version != version:
                    raise RuntimeError("Salesforce import produced inconsistent Ossie versions")
                models = document.get("semantic_model")
                if not isinstance(models, list):
                    raise TypeError(f"Salesforce import omitted semantic_model: {path.name}")
                semantic_models.extend(models)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(
            {"version": version or "0.2.0.dev0", "semantic_model": semantic_models},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "import"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--build-root", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "export":
        export_model(args.input, args.output, args.build_root)
    else:
        import_model(args.input, args.output, args.build_root)


if __name__ == "__main__":
    main()
