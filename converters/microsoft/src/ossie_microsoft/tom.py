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

"""Optional, offline structural validation using Microsoft's Tabular Object Model."""

import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path

_ASSEMBLIES = (
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
    "Microsoft.AnalysisServices.Tabular.Json.dll",
)
_ASSEMBLY_ENV = "OSSIE_MICROSOFT_TOM_ASSEMBLIES"


class TomUnavailableError(RuntimeError):
    """Raised when the optional TOM runtime or assemblies are unavailable."""


class TomValidationError(ValueError):
    """Raised by :meth:`TomValidationResult.raise_for_errors` for an invalid model."""


@dataclass(frozen=True)
class TomValidationIssue:
    """One structural error reported by TOM."""

    message: str
    source: str | None = None


@dataclass(frozen=True)
class TomValidationResult:
    """Result of deserializing and structurally validating one TMSL database."""

    errors: tuple[TomValidationIssue, ...]
    deserialized: bool

    @property
    def is_valid(self):
        return self.deserialized and not self.errors

    def raise_for_errors(self):
        if self.is_valid:
            return
        messages = [
            f"{issue.source}: {issue.message}" if issue.source else issue.message
            for issue in self.errors
        ]
        raise TomValidationError("\n".join(messages) or "TOM could not deserialize the model")


def _assembly_directory(assembly_dir):
    if assembly_dir is not None:
        return Path(assembly_dir).resolve()
    configured = os.environ.get(_ASSEMBLY_ENV)
    return Path(configured).resolve() if configured else (Path.cwd() / ".tom" / "assemblies")


@lru_cache
def _load_tom(assembly_dir):
    try:
        from clr_loader import get_coreclr
        from pythonnet import get_runtime_info, set_runtime
    except ImportError as exc:
        raise TomUnavailableError(
            "TOM validation and TMDL serialization require the optional 'tom' extra; install with "
            "`pip install apache-ossie-microsoft[tom]`"
        ) from exc

    missing = [
        name
        for name in (*_ASSEMBLIES, "dotnet.runtimeconfig.json")
        if not (assembly_dir / name).is_file()
    ]
    if missing:
        raise TomUnavailableError(
            f"TOM assemblies are unavailable in {assembly_dir}; run "
            "`python scripts/restore_tom.py` or set OSSIE_MICROSOFT_TOM_ASSEMBLIES"
        )

    if get_runtime_info() is None:
        set_runtime(
            get_coreclr(runtime_config=os.fspath(assembly_dir / "dotnet.runtimeconfig.json"))
        )

    import clr

    if os.fspath(assembly_dir) not in sys.path:
        sys.path.append(os.fspath(assembly_dir))
    for name in _ASSEMBLIES:
        clr.AddReference(os.fspath(assembly_dir / name))

    return import_module("Microsoft.AnalysisServices.Tabular")


def validate_tmsl(document, *, assembly_dir=None):
    """Validate a TMSL mapping or JSON string without connecting to a server.

    TOM checks model structure and object references, but does not validate DAX.
    """

    raw = json.dumps(document) if isinstance(document, dict) else document
    if not isinstance(raw, str):
        raise TypeError("document must be a TMSL mapping or JSON string")

    tom = _load_tom(_assembly_directory(assembly_dir))
    try:
        database = tom.JsonSerializer.DeserializeDatabase(raw)
    except Exception as exc:  # noqa: BLE001 - pythonnet surfaces managed exceptions here
        message = getattr(exc, "Message", str(exc))
        return TomValidationResult((TomValidationIssue(f"DeserializeDatabase: {message}"),), False)

    validation = database.Model.Validate()
    errors = tuple(
        TomValidationIssue(
            message=error.Message,
            source=str(error.Source) if error.Source is not None else None,
        )
        for error in validation.Errors
    )
    return TomValidationResult(errors, True)


def serialize_tmdl(document, *, assembly_dir=None):
    """Serialize a TMSL mapping or JSON string into a single canonical TMDL document.

    Returns the TMDL text for the whole database, with every object nested inline
    rather than split across the TMDL folder representation.
    """

    raw = json.dumps(document) if isinstance(document, dict) else document
    if not isinstance(raw, str):
        raise TypeError("document must be a TMSL mapping or JSON string")

    tom = _load_tom(_assembly_directory(assembly_dir))
    database = tom.JsonSerializer.DeserializeDatabase(raw)
    return str(tom.TmdlSerializer.SerializeDatabase(database))


def deserialize_tmdl(document, *, assembly_dir=None):
    """Parse a single TMDL document into a TMSL mapping.

    Accepts the database-rooted document :func:`serialize_tmdl` produces as well as a
    model-rooted one.
    """

    if not isinstance(document, str):
        raise TypeError("document must be TMDL text")

    tom = _load_tom(_assembly_directory(assembly_dir))
    serialization = import_module("Microsoft.AnalysisServices.Tabular.Serialization")
    system = import_module("System")

    context = serialization.MetadataSerializationContext.Create(
        serialization.MetadataSerializationStyle.Tmdl
    )
    for logical_path, content in _split_tmdl_documents(document.lstrip("\ufeff")):
        context.ReadFromDocument(
            logical_path,
            system.IO.StringReader(content),
            system.Text.Encoding.UTF8,
        )
    return json.loads(tom.JsonSerializer.SerializeDatabase(context.ToDatabase(None)))


def _split_tmdl_documents(document):
    """Split one TMDL document into the documents TOM's parser accepts.

    ``TmdlSerializer.SerializeDatabase`` nests ``model`` inside ``database``, but the
    parser only reads the two as the separate documents of the folder representation.
    A hand-written document may also place the two side by side; both forms are split
    on the ``model`` header and the model block is de-indented to its own root.
    """
    lines = document.replace("\r\n", "\n").split("\n")
    if not lines[0].startswith("database "):
        return [("model.tmdl", document)]

    model = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("model ")),
        None,
    )
    if model is None:
        return [("model.tmdl", document)]

    start = model
    while start and lines[start - 1].strip().startswith("///"):
        start -= 1
    indent = lines[model][: len(lines[model]) - len(lines[model].lstrip("\t "))]
    body = [
        line[len(indent) :] if line.startswith(indent) else line for line in lines[start:]
    ]
    return [
        ("database.tmdl", "\n".join(lines[:start])),
        ("model.tmdl", "\n".join(body)),
    ]


def validate_bim(path, *, assembly_dir=None):
    """Read a UTF-8 (optionally BOM-prefixed) ``.bim`` file and validate it."""

    return validate_tmsl(
        Path(path).read_text(encoding="utf-8-sig"),
        assembly_dir=assembly_dir,
    )
