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

import yaml
from ossie import OssieDocument, OssieSemanticModel
from pydantic import ValidationError

from ..util.yaml import load_yaml
from .context import ExportContext
from .load_ossie_semantic_model import load_ossie_semantic_model


def load_ossie_document(
    *,
    document_path: str | Path,
    ctx: ExportContext,
) -> OssieDocument | None:
    """Parse file contents as an Ossie document.

    ``document_path`` is the path to a file, expected to be in Ossie YAML format.

    Returns ``ossie_document``.
    """
    file_path = _validate_document_file_path(document_path, ctx=ctx)
    file_contents = _read_document_file_contents(file_path, ctx=ctx)
    file_data = _parse_document_yaml(file_path, file_contents, ctx=ctx)
    document = _validate_document_yaml(file_data, ctx=ctx)

    # additional validation
    if document:
        semantic_model = _load_ossie_semantic_models(document.semantic_model, ctx=ctx)
        document = document.model_copy(update={"semantic_model": semantic_model})

    return document


def _validate_document_file_path(
    document_path: str | Path,
    *,
    ctx: ExportContext,
) -> Path | None:
    document_path = Path(document_path).resolve()
    if not document_path.exists():
        ctx.fatal(f"File does not exist: `{document_path}`")
        return None
    elif not document_path.is_file():
        ctx.fatal(f"File is not a file: `{document_path}`")
        return None
    return document_path


def _read_document_file_contents(
    file_path: Path | None,
    *,
    ctx: ExportContext,
) -> str | None:
    if file_path is None:
        return None
    try:
        file_contents = file_path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        ctx.fatal(
            f"Failed to read file `{file_path}`: {e}",
            path=[str(file_path)],
        )
        return None
    if not file_contents:
        ctx.fatal(
            f"File is empty: `{file_path}`",
            path=[str(file_path)],
        )
        return None
    return file_contents


def _parse_document_yaml(
    file_path: Path | None,
    file_contents: str | None,
    *,
    ctx: ExportContext,
) -> dict | None:
    if file_path is None or file_contents is None:
        return None
    try:
        raw = load_yaml(file_contents)
    except yaml.YAMLError as e:
        ctx.fatal(
            f"Invalid YAML in file `{file_path}`: {e}",
            path=[str(file_path)],
        )
        return None
    if not isinstance(raw, dict):
        ctx.fatal(f"YAML document must be a mapping: `{file_contents}`")
        return None
    return raw


def _validate_document_yaml(
    data: dict | None,
    *,
    ctx: ExportContext,
) -> OssieDocument | None:
    if data is None:
        return None
    try:
        document = OssieDocument.model_validate(data)
    except ValidationError as e:
        ctx.fatal(f"Invalid Ossie document: {e}")
        return None
    return document


def _load_ossie_semantic_models(
    semantic_models: list[OssieSemanticModel],
    *,
    ctx: ExportContext,
) -> list[OssieSemanticModel]:
    with ctx.problem_scope("semantic_model"):  # field name is singular in the spec
        result = list[OssieSemanticModel]()
        for semantic_model in semantic_models:
            if semantic_model := load_ossie_semantic_model(semantic_model, ctx=ctx):
                result.append(semantic_model)
    return result
