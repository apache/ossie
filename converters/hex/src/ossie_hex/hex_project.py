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

"""Load and write Hex project directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ._common import ConversionError, dump_yaml, load_yaml_all
from .hex_models import HexModel, HexProject, HexView, parse_hex_resource


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

    resources: list[HexModel | HexView] = []
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


def write_hex_project(
    project_dir: str | Path,
    files: dict[str, str],
) -> None:
    """Write a mapping of relative paths → YAML text into ``project_dir``."""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")


def resource_to_yaml(resource: HexModel | HexView | dict[str, Any]) -> str:
    """Serialize a Hex resource to YAML text."""
    parsed_resource = (
        resource
        if isinstance(resource, (HexModel, HexView))
        else parse_hex_resource(resource)
    )
    data = parsed_resource.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
        exclude_unset=True,
    )
    if isinstance(parsed_resource, HexView):
        # only for View, type is optional for Model
        data = {"id": data.pop("id"), "type": "view", **data}
    return dump_yaml(_compact_hex_resource(data))


def _compact_hex_resource(data: dict[str, Any]) -> dict[str, Any]:
    """Omit derived defaults for cleaner Hex YAML output."""
    out = dict(data)
    dims = out.get("dimensions")
    if isinstance(dims, list):
        out["dimensions"] = [_compact_dimension(d) for d in dims]
    relations = out.get("relations")
    if isinstance(relations, list):
        out["relations"] = [_compact_relation(r) for r in relations]
    return out


def _compact_dimension(dim: dict[str, Any]) -> dict[str, Any]:
    out = dict(dim)
    if out.get("expr_sql") == out.get("id") and not out.get("expr_calc"):
        out.pop("expr_sql", None)
    return out


def _compact_relation(relation: dict[str, Any]) -> dict[str, Any]:
    out = dict(relation)
    if out.get("target") == out.get("id"):
        out.pop("target", None)
    return out
