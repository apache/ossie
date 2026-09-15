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

from ..hex import HexProject
from .context import ExportContext
from .dump_hex_resource import dump_hex_resource


def dump_hex_project(
    hex_project: HexProject,
    *,
    dir: Path,
    ctx: ExportContext,
) -> None:
    with ctx.problem_scope(hex_project.name):
        project_dir = _resolve_project_dir(dir, hex_project.name, ctx=ctx)
        project_dir = _write_project_dir(project_dir, ctx=ctx)
        if project_dir is None:
            return

        with ctx.problem_scope("resources"):
            for hex_resource in hex_project.resources:
                dump_hex_resource(hex_resource, project_dir, ctx=ctx)


def _resolve_project_dir(
    dir: Path, project_name: str, *, ctx: ExportContext
) -> Path | None:
    try:
        dir = dir.resolve()
        project_dir = (dir / project_name).resolve()
    except (OSError, ValueError) as e:
        ctx.error(f"Failed to resolve project directory: {e}")
        return None
    if not project_dir.is_relative_to(dir):
        ctx.error(
            f"Project name would write files outside the output directory: {project_name}"
        )
        return None
    return project_dir


def _write_project_dir(project_dir: Path | None, *, ctx: ExportContext) -> Path | None:
    if project_dir is None:
        return None
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as e:
        ctx.error(f"Failed to create project directory: {e}")
        return None
    return project_dir
