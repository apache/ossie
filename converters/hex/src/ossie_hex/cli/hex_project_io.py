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

from ..util.errors import ConversionError

_HEX_PROJECT_FILE_EXTENSIONS = {".yml", ".yaml"}


def read_hex_project(project_dir: str | Path) -> dict[str, str]:
    """Read a Hex project directory into a mapping of file names → contents text.

    File names are relative to ``project_dir``, and come back in sorted order so
    that a project reads the same way twice.
    """
    root = Path(project_dir)
    if not root.is_dir():
        raise ConversionError(f"Hex project path is not a directory: {root}")

    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _HEX_PROJECT_FILE_EXTENSIONS
    )
    if not paths:
        raise ConversionError(
            f"No Hex project files found under directory: {root}. Looked for extensions: {_HEX_PROJECT_FILE_EXTENSIONS}"
        )

    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }


def write_hex_project(
    project_dir: str | Path,
    files: dict[str, str],
) -> None:
    """Write a mapping of file names → YAML text into ``project_dir``."""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
