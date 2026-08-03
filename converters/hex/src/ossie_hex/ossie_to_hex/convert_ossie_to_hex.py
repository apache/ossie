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

from ossie import OSIDialect

from ..util.errors import ConversionWarning
from .convert_ossie_document import convert_ossie_document
from .dump_hex_resource import hex_resource_to_yaml
from .load_ossie_document import load_ossie_document


def convert_ossie_to_hex(
    ossie_yaml: str,
    *,
    model_name: str | None = None,
    dialect: OSIDialect | None = None,
    base_model: str | None = None,
) -> tuple[dict[str, str], list[ConversionWarning]]:
    """Convert Ossie YAML to a Hex project files.

    ``base_model`` is the name of the base model to use for the Hex project.
    ``dialect`` selects the OSI dialect to use from multi-dialect expressions.
    ``model_name`` is the name of the model to use for the Hex project.

    Returns ``(files, warnings)``.
    """
    warnings: list[ConversionWarning] = []
    ossie_document, warnings = load_ossie_document(ossie_yaml)
    hex_project, warnings = convert_ossie_document(
        ossie_document,
        model_name=model_name,
        dialect=dialect,
        base_model=base_model,
        warnings=warnings,
    )

    files: dict[str, str] = {}
    for resource in hex_project.resources:
        files[f"{resource.id}.yml"] = hex_resource_to_yaml(resource)

    return files, warnings
