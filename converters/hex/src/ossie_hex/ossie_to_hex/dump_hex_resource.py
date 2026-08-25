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

from pathlib import Path
from typing import Any

from pydantic_core import PydanticSerializationError
from yaml import YAMLError

from ..hex import HexResource
from ..util.yaml import dump_yaml
from .context import ExportContext


def dump_hex_resource(
    hex_resource: HexResource,
    project_dir: Path,
    *,
    ctx: ExportContext,
) -> None:
    """Serialize and write a Hex resource to a file.

    Args:
        - `hex_resource`: A Hex resource
        - `project_dir`: A canonical path to the directory for the Hex project
    """
    with ctx.problem_scope(hex_resource.id):
        file_path = _resolve_file_path(project_dir, hex_resource, ctx=ctx)
        json_data = _serialize_to_json(hex_resource, ctx=ctx)
        yaml_data = _serialize_to_yaml(json_data, ctx=ctx)
        _write_file(file_path, yaml_data, ctx=ctx)


def _serialize_to_json(hex_resource: HexResource, *, ctx: ExportContext) -> dict | None:
    try:
        json_data = hex_resource.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
            exclude_defaults=True,
        )
        json_data = _reorder_fields(json_data)
        return json_data
    except PydanticSerializationError as e:
        ctx.error(f"Failed to serialize to JSON: {e}")


def _serialize_to_yaml(json_data: dict | None, *, ctx: ExportContext) -> str | None:
    if json_data is None:
        return None
    try:
        return dump_yaml(json_data)
    except YAMLError as e:
        ctx.error(f"Failed to serialize to YAML: {e}")


def _resolve_file_path(
    project_dir: Path, hex_resource: HexResource, *, ctx: ExportContext
) -> Path | None:
    try:
        file_path = (project_dir / f"{hex_resource.id}.yml").resolve()
    except (OSError, ValueError) as e:
        ctx.error(f"Failed to resolve file path: {e}")
        return None
    if not file_path.is_relative_to(project_dir):
        ctx.error("Resource id would write a file outside the project directory")
        return None
    return file_path


def _write_file(
    file_path: Path | None,
    data: str | None,
    *,
    ctx: ExportContext,
) -> None:
    if file_path is None or data is None:
        return
    try:
        file_path.write_text(data, encoding="utf-8")
    except OSError as e:
        ctx.error(f"Failed to write to file: {e}")


_FIELD_ORDER = ("id", "name", "description")


def _reorder_fields(value: Any) -> Any:
    """Pydantic's model_dump() method always follows field declaration order, without
    options to customize. But we can reorder the fields recursively.

    This is presently useful so that `description` does not appear at the end of the
    output.
    """
    if isinstance(value, list):
        return [_reorder_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    ordered = {key: _reorder_fields(value[key]) for key in _FIELD_ORDER if key in value}
    ordered.update(
        {
            key: _reorder_fields(item)
            for key, item in value.items()
            if key not in ordered
        }
    )
    return ordered
