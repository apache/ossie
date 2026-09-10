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
"""Read Lightdash definitions from a file or a project directory.

Two shapes are understood and merged: dbt schema files (``models:`` /
``seeds:`` lists with Lightdash ``meta``) and Lightdash's own dbt-free model
files (``type: model``, ``sql_from``, a ``dimensions:`` list). The latter are
folded into the dbt shape, with ``sql_from`` kept as the model's source, so
the converter has one input model.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

_MODEL_FILE_TYPES = {"model", "model/v1beta", "model/v1"}
_DIMENSION_OWN_KEYS = {"name", "description", "metrics", "additional_dimensions"}
_MODEL_OWN_KEYS = {"type", "name", "description", "dimensions"}

# Directories dbt or Python tooling generate; nothing in them is authored schema.
_SKIPPED_DIRS = {"target", "dbt_packages", "logs", ".git", "node_modules", "env", "venv", ".venv", "site-packages", "__pycache__"}


def is_model_file(document: Any) -> bool:
    """True for a Lightdash dbt-free model file (``type: model`` + dimensions)."""
    return (
        isinstance(document, dict)
        and document.get("type") in _MODEL_FILE_TYPES
        and isinstance(document.get("name"), str)
        and isinstance(document.get("dimensions"), list)
    )


def model_file_to_dbt_model(document: Dict[str, Any]) -> Dict[str, Any]:
    """Fold a Lightdash model file into the dbt model shape the converter reads.

    Dimensions become columns whose ``meta.dimension`` carries everything but
    the column-level keys; ``metrics`` and ``additional_dimensions`` stay at
    column level; every other top-level key (``sql_from``, ``joins``,
    ``metrics``, ``primary_key``, ``sql_filter``, ...) becomes model meta.
    """
    columns: List[Dict[str, Any]] = []
    for dimension in document["dimensions"]:
        if not isinstance(dimension, dict) or "name" not in dimension:
            continue
        column: Dict[str, Any] = {"name": dimension["name"]}
        if dimension.get("description"):
            column["description"] = dimension["description"]
        meta: Dict[str, Any] = {
            "dimension": {k: v for k, v in dimension.items() if k not in _DIMENSION_OWN_KEYS}
        }
        for key in ("metrics", "additional_dimensions"):
            if dimension.get(key):
                meta[key] = dimension[key]
        column["meta"] = meta
        columns.append(column)
    model: Dict[str, Any] = {"name": document["name"]}
    if document.get("description"):
        model["description"] = document["description"]
    model["meta"] = {k: v for k, v in document.items() if k not in _MODEL_OWN_KEYS}
    model["columns"] = columns
    return model


def load_schema(path: Path) -> Dict[str, Any]:
    """Return ``{"version": 2, "models": [...], "seeds": [...]}`` for ``path``."""
    schema, _ = load_schema_with_skips(path)
    return schema


def load_schema_with_skips(path: Path) -> Tuple[Dict[str, Any], List[Path]]:
    """``load_schema`` plus the files that were skipped because they are not
    valid YAML (templates with placeholders, Jinja-only files, ...).

    A file is read as is. A directory is walked in sorted order; every
    ``.yml`` / ``.yaml`` file contributes its list-valued ``models:`` and
    ``seeds:`` entries (``dbt_project.yml`` has a dict-valued ``models:`` and
    is skipped by that rule), and generated directories are ignored.
    """
    if path.is_file():
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if is_model_file(document):
            return {"version": 2, "models": [model_file_to_dbt_model(document)]}, []
        return document, []

    models: List[Dict[str, Any]] = []
    seeds: List[Dict[str, Any]] = []
    skipped: List[Path] = []
    for file in sorted(path.rglob("*.y*ml")):
        if file.suffix not in (".yml", ".yaml"):
            continue
        if _SKIPPED_DIRS & set(file.relative_to(path).parts[:-1]):
            continue
        try:
            document = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            skipped.append(file)
            continue
        if not isinstance(document, dict):
            continue
        if is_model_file(document):
            models.append(model_file_to_dbt_model(document))
            continue
        if isinstance(document.get("models"), list):
            models.extend(document["models"])
        if isinstance(document.get("seeds"), list):
            seeds.extend(document["seeds"])
    return {"version": 2, "models": models, "seeds": seeds}, skipped
