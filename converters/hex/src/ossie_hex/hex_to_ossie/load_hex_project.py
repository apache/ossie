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

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ..hex_types import HexProject, HexResource, parse_hex_resource
from ..util.errors import ConversionError
from ..util.yaml import load_yaml_all


def load_hex_project(
    project_dir: str | Path,
    *,
    name: str | None = None,
    dialect: str,
) -> HexProject:
    """Load a Hex project directory of ``.yml`` / ``.yaml`` resource files."""
    root = Path(project_dir)
    if not root.is_dir():
        raise ConversionError(f"Hex project path is not a directory: {root}")

    files = sorted(
        [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}
        ]
    )
    if not files:
        raise ConversionError(f"No Hex YAML resources found under {root}")

    resources: list[HexResource] = []
    seen_ids: set[str] = set()

    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        docs = load_yaml_all(text, what=rel)
        for idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                raise ConversionError(
                    f"{rel} document {idx}: expected a mapping, got {type(doc).__name__}"
                )
            try:
                resource = parse_hex_resource(doc)
            except ValidationError as e:
                raise ConversionError(f"Invalid Hex resource in {rel}: {e}") from e
            if resource.id in seen_ids:
                raise ConversionError(
                    f"Duplicate Hex resource id '{resource.id}' in {rel}"
                )
            seen_ids.add(resource.id)
            resources.append(resource)

    project_name = name or root.name
    return HexProject(
        name=project_name,
        dialect=dialect,
        resources=resources,
    )
