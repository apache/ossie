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

"""Resolve a Power BI project folder into TMDL documents.

The documents keep the PBIP folder layout. TOM resolves references between
documents before the existing TMSL-to-Ossie mapping runs.
"""

import json
from pathlib import Path
from typing import NoReturn

from ._common import ConversionError
from .semantic_model_to_ossie import convert_semantic_model_to_ossie
from .tom import TomUnavailableError, deserialize_tmdl_documents

_LOAD_FIRST = ("database.tmdl", "model.tmdl")


def resolve_definition_dir(path: Path) -> Path:
    """Return the ``definition`` directory that holds ``database.tmdl``."""
    path = path.expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".pbip":
        return _definition_from_pbip(path)
    if path.is_dir() and (path / "database.tmdl").is_file():
        return path
    if path.is_dir() and (path / "definition" / "database.tmdl").is_file():
        return path / "definition"
    raise ConversionError(
        f"{path} is not a .pbip file or a semantic model folder "
        "(no definition/database.tmdl)"
    )


def tmdl_documents(definition_dir: Path) -> list[tuple[str, str]]:
    """List ``*.tmdl`` files as ``(logical_path, text)`` for TOM.

    ``logical_path`` is relative to ``definition_dir`` and uses ``/``.
    ``database.tmdl`` and ``model.tmdl`` come first. Everything else is
    sorted by that path. Non-TMDL files are omitted.
    """
    if not (definition_dir / "database.tmdl").is_file():
        raise ConversionError(f"{definition_dir} has no database.tmdl")

    found: list[tuple[str, str]] = []
    for file in definition_dir.rglob("*.tmdl"):
        relative = file.relative_to(definition_dir)
        # `.Hidden.tmdl` is a real TOM table file. Skip hidden directories
        # such as `.pbi`, not file names.
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        found.append((relative.as_posix(), file.read_text(encoding="utf-8-sig")))

    def sort_key(item: tuple[str, str]) -> tuple[int, str]:
        logical = item[0]
        if logical in _LOAD_FIRST:
            return (_LOAD_FIRST.index(logical), logical)
        return (len(_LOAD_FIRST), logical)

    return sorted(found, key=sort_key)


def import_powerbi_project(path: str | Path) -> str:
    """Import a ``.pbip``, ``*.SemanticModel`` folder, or ``definition`` folder.

    Returns Apache Ossie YAML. Table references stay in the TMDL documents;
    TOM resolves them to TMSL before the existing Ossie mapping runs.
    """

    documents = tmdl_documents(resolve_definition_dir(Path(path)))
    try:
        semantic_model = deserialize_tmdl_documents(documents)
    except Exception as exc:
        reraise_tom_load_failure(exc)
    return convert_semantic_model_to_ossie(semantic_model)


def reraise_tom_load_failure(exc: BaseException) -> NoReturn:
    """Raise a ConversionError for a failure while TOM loads TMDL.

    ``ConversionError`` passes through. ``TomUnavailableError`` keeps its
    message. Other exceptions use a .NET ``Message`` when one is present, so
    the CLI can report them without a traceback.
    """

    if isinstance(exc, ConversionError):
        raise exc
    if isinstance(exc, TomUnavailableError):
        raise ConversionError(str(exc)) from exc
    raise ConversionError(getattr(exc, "Message", str(exc))) from exc


def _definition_from_pbip(pbip: Path) -> Path:
    try:
        payload = json.loads(pbip.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{pbip} is not a PBIP JSON file") from exc
    if not isinstance(payload, dict):
        raise ConversionError(f"{pbip} is not a PBIP JSON object")

    report_rel = _report_path(payload)
    if report_rel is None:
        raise ConversionError(f"{pbip} has no report artifact")

    report_dir = (pbip.parent / report_rel).resolve()
    pbir_path = report_dir / "definition.pbir"
    if not pbir_path.is_file():
        raise ConversionError(f"{pbip} report has no definition.pbir")
    try:
        pbir = json.loads(pbir_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{pbir_path} is not JSON") from exc
    if not isinstance(pbir, dict):
        raise ConversionError(f"{pbir_path} is not a report JSON object")

    dataset = pbir.get("datasetReference") or {}
    if not isinstance(dataset, dict):
        raise ConversionError(f"{pbir_path} has no valid datasetReference object")
    if "byConnection" in dataset and "byPath" not in dataset:
        raise ConversionError(
            f"{pbip} references a remote semantic model (byConnection); "
            "import needs a local SemanticModel folder"
        )
    by_path = dataset.get("byPath") or {}
    if not isinstance(by_path, dict) or not isinstance(by_path.get("path"), str):
        raise ConversionError(f"{pbip} has no datasetReference.byPath.path string")
    model_rel = by_path["path"]
    if not model_rel:
        raise ConversionError(f"{pbip} has no datasetReference.byPath")

    definition = (report_dir / model_rel / "definition").resolve()
    if not (definition / "database.tmdl").is_file():
        raise ConversionError(f"{pbip} byPath does not contain definition/database.tmdl")
    return definition


def _report_path(payload: dict) -> str | None:
    artifacts = payload.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise ConversionError("PBIP artifacts must be an array")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        report = artifact.get("report")
        if isinstance(report, dict) and isinstance(report.get("path"), str) and report["path"]:
            return report["path"]
    return None
