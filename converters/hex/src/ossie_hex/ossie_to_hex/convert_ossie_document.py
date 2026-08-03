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

from ossie import OSIDialect, OSIDocument, OSISemanticModel

from ..hex_types import HexProject
from ..util.errors import ConversionError, ConversionWarning
from .convert_ossie_semantic_model import convert_ossie_semantic_model


def convert_ossie_document(
    ossie_document: OSIDocument,
    *,
    model_name: str | None = None,
    dialect: OSIDialect | None = None,
    base_model: str | None = None,
    warnings: list[ConversionWarning],
) -> tuple[HexProject, list[ConversionWarning]]:
    """Convert Ossie Document to a Hex project.

    ``base_model`` is the name of the base model to use for the Hex project.
    ``dialect`` selects the OSI dialect to use from multi-dialect expressions.
    ``model_name`` is the name of the model to use for the Hex project.

    Returns ``(hex_project, warnings)``.
    """
    ossie_semantic_model, warnings = _pick_ossie_semantic_model(
        ossie_document, model_name
    )
    ossie_dialect = _pick_ossie_dialect(ossie_document, dialect)
    hex_resources, warnings = convert_ossie_semantic_model(
        ossie_semantic_model,
        ossie_dialect,
        base_model=base_model,
        warnings=warnings,
    )
    hex_project = HexProject(
        name=ossie_semantic_model.name,
        resources=hex_resources,
    )

    return hex_project, warnings


def _pick_ossie_semantic_model(
    ossie_document: OSIDocument,
    model_name: str | None,
) -> tuple[OSISemanticModel, list[ConversionWarning]]:
    models = ossie_document.semantic_model
    warnings: list[ConversionWarning] = []
    if not models:
        raise ConversionError("Ossie document has no semantic_model entries")
    if model_name:
        model = next((m for m in models if m.name == model_name), None)
        if model is None:
            raise ConversionError(f"Ossie semantic model '{model_name}' not found")
    else:
        model = models[0]
        if len(models) > 1:
            warnings.append(
                ConversionWarning(
                    f"Ossie document has {len(models)} semantic models; "
                    f"exporting '{model.name}' (pass --model to select another)"
                )
            )
    return model, warnings


def _pick_ossie_dialect(
    document: OSIDocument,
    requested: OSIDialect | None,
) -> OSIDialect:
    if requested is not None:
        return requested
    if document.dialects:
        return document.dialects[0]
    return OSIDialect.ANSI_SQL
